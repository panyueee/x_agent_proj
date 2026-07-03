# -*- coding: utf-8 -*-
"""Claude LLM 薄封装：统一模型、重试、token 统计（成本估算），测试时可注入 FakeLLM。"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-5"
PROMPTS_DIR = Path(__file__).resolve().parents[2] / "agents" / "prompts"

# 美元 / 百万 token（claude-sonnet-5 标准价，保守口径；介绍期为 2/10）
PRICE_PER_MTOK = {
    "claude-sonnet-5": (3.0, 15.0),
}


def load_prompt(name: str) -> str:
    """从 agents/prompts/ 读提示词模板（与代码分离）。"""
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def split_sections(template: str) -> dict[str, str]:
    """把模板按 '## <SECTION>' 二级标题切成段落字典（如 MAP / REDUCE）。"""
    sections: dict[str, str] = {}
    current, buf = None, []
    for line in template.splitlines():
        m = re.match(r"^##\s+([A-Z_]+)\s*$", line)
        if m:
            if current:
                sections[current] = "\n".join(buf).strip()
            current, buf = m.group(1), []
        elif current:
            buf.append(line)
    if current:
        sections[current] = "\n".join(buf).strip()
    return sections


def extract_json(text: str):
    """从模型输出提取 JSON：优先 ```json 代码块，退化到首个 {...} 大括号配平。"""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    start = text.find("{")
    if start == -1:
        raise ValueError("输出中未找到 JSON")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("输出中 JSON 大括号不配平")


class ClaudeLLM:
    """真实 Claude 客户端。需要环境变量 ANTHROPIC_API_KEY。"""

    def __init__(self, model: str = DEFAULT_MODEL, max_retries: int = 3):
        # 有 key 走 anthropic SDK，否则退化为 claude -p（订阅、无需 key）
        from x_agent.llm_client import build_client
        try:
            import anthropic  # 延迟导入，mock 测试/无 key 时不强依赖
            self._anthropic = anthropic
            self._retry_errs = (anthropic.RateLimitError,
                                anthropic.InternalServerError,
                                anthropic.APIConnectionError)
        except Exception:
            self._anthropic = None
            self._retry_errs = (RuntimeError,)   # CLI 路径的失败也重试
        self.client = build_client()
        self.model = model
        self.max_retries = max_retries
        self.input_tokens = 0
        self.output_tokens = 0
        self.calls = 0

    def complete(self, system: str, prompt: str, max_tokens: int = 4000) -> str:
        last_err = None
        for attempt in range(self.max_retries):
            try:
                resp = self.client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": prompt}],
                )
                self.calls += 1
                self.input_tokens += resp.usage.input_tokens
                self.output_tokens += resp.usage.output_tokens
                return "".join(b.text for b in resp.content if b.type == "text")
            except self._retry_errs as e:
                last_err = e
                wait = 5 * (attempt + 1)
                logger.warning("LLM 调用失败(%s)，%ss 后重试: %s", type(e).__name__, wait, e)
                time.sleep(wait)
        raise last_err

    def cost_usd(self) -> float:
        pin, pout = PRICE_PER_MTOK.get(self.model, (3.0, 15.0))
        return self.input_tokens / 1e6 * pin + self.output_tokens / 1e6 * pout

    def usage_summary(self) -> str:
        return (f"LLM 调用 {self.calls} 次，input {self.input_tokens} tok / "
                f"output {self.output_tokens} tok，估算成本 ${self.cost_usd():.3f}（标准价口径）")
