# -*- coding: utf-8 -*-
"""统一 LLM 客户端：**优先 claude -p（订阅、无需 API key），有 key 才走 anthropic SDK**。

动机：ANTHROPIC_API_KEY 为空时，classifier/industry/psych/rag/persona 的 LLM 路径全死。
本适配器让它们在无 key 下也能跑——退化为 shell 调 `claude -p` 无头模式（走 Claude Code 订阅）。

接口与 anthropic SDK 兼容：返回对象有 `.content[0].text`（type=text）和 `.usage.{input,output}_tokens`，
所以现有 `client.messages.create(model=, max_tokens=, system=, messages=[...])` 调用点无需改。

选择逻辑（build_client）：
  - 环境有非空 ANTHROPIC_API_KEY → anthropic.Anthropic()（原生，功能全）
  - 否则 → CLIClient()（claude -p），CLI 也不可用则 build 时报错。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess


# ── 兼容 anthropic 响应形状的轻量对象 ─────────────────────────────────────────
class _Block:
    __slots__ = ("type", "text")

    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _Usage:
    __slots__ = ("input_tokens", "output_tokens")

    def __init__(self, i: int = 0, o: int = 0):
        self.input_tokens = i
        self.output_tokens = o


class _Resp:
    __slots__ = ("content", "usage")

    def __init__(self, text: str, usage: _Usage):
        self.content = [_Block(text)]
        self.usage = usage


def _claude_bin() -> str:
    return (os.environ.get("CLAUDE_BIN")
            or shutil.which("claude")
            or "/Users/pany19/.nvm/versions/node/v22.22.2/bin/claude")


def _model_alias(model: str | None) -> str | None:
    """SDK 全名 → CLI 别名；未知则 None（用 CLI 默认）。"""
    m = (model or "").lower()
    if "haiku" in m:
        return "haiku"
    if "opus" in m:
        return "opus"
    if "sonnet" in m:
        return "sonnet"
    return None


class _Messages:
    def __init__(self, client: "CLIClient"):
        self._c = client

    def create(self, model: str | None = None, max_tokens: int = 1024,
               system: str | None = None, messages=None, **_ignore) -> _Resp:
        # system + 各 user message 拼成单一 prompt（CLI 无独立 system 通道）
        parts = []
        if system:
            parts.append(str(system))
        for m in (messages or []):
            c = m.get("content", "")
            if isinstance(c, list):  # 兼容 content blocks
                c = "".join(b.get("text", "") for b in c if isinstance(b, dict))
            parts.append(str(c))
        prompt = "\n\n".join(p for p in parts if p)

        cmd = [self._c.bin, "-p", "--output-format", "json"]
        alias = _model_alias(model)
        if alias:
            cmd += ["--model", alias]

        env = dict(os.environ)
        # 回环不走代理；证书用 certifi（与 nightly 一致）
        env["NO_PROXY"] = "localhost,127.0.0.1,::1," + env.get("NO_PROXY", "")
        env["no_proxy"] = env["NO_PROXY"]

        try:
            out = subprocess.run(cmd, input=prompt, capture_output=True,
                                 text=True, env=env, timeout=self._c.timeout)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"claude -p 超时（{self._c.timeout}s）") from e
        if out.returncode != 0:
            raise RuntimeError(f"claude -p 失败(rc={out.returncode}): {out.stderr[:300]}")

        raw = out.stdout.strip()
        try:
            data = json.loads(raw)
        except Exception as e:
            raise RuntimeError(f"claude -p 输出非 JSON: {raw[:200]}") from e
        if data.get("is_error"):
            raise RuntimeError(f"claude -p 报错: {data.get('result', '')[:300]}")
        text = data.get("result") or ""
        usage = data.get("usage") or {}
        return _Resp(text, _Usage(usage.get("input_tokens", 0),
                                  usage.get("output_tokens", 0)))


class CLIClient:
    """claude -p 无头客户端，接口子集兼容 anthropic.Anthropic()。"""

    def __init__(self, timeout: int = 180):
        self.bin = _claude_bin()
        if not (os.path.isfile(self.bin) or shutil.which(self.bin)):
            raise RuntimeError(f"未找到 claude CLI（{self.bin}）；设 CLAUDE_BIN 或装 claude")
        self.timeout = timeout
        self.messages = _Messages(self)


def has_api_key() -> bool:
    return bool((os.environ.get("ANTHROPIC_API_KEY") or "").strip())


def build_client(cfg=None):
    """有非空 ANTHROPIC_API_KEY → anthropic SDK；否则 → CLIClient（claude -p）。"""
    if has_api_key():
        import anthropic
        return anthropic.Anthropic()
    return CLIClient()


def backend_name() -> str:
    return "anthropic-sdk" if has_api_key() else "claude-cli"
