# -*- coding: utf-8 -*-
"""llm_client 适配器测试：优先 claude -p、有 key 才 SDK。全程 mock subprocess，不真调 CLI。"""
import json
import types
import pytest

from x_agent import llm_client as lc


def _fake_run_factory(result="ok", is_error=False, rc=0, raw=None):
    def _fake_run(cmd, input=None, capture_output=True, text=True, env=None, timeout=None):
        payload = raw if raw is not None else json.dumps(
            {"result": result, "is_error": is_error,
             "usage": {"input_tokens": 11, "output_tokens": 7}})
        return types.SimpleNamespace(returncode=rc, stdout=payload, stderr="boom")
    return _fake_run


@pytest.fixture
def no_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # 让 CLIClient 认为 claude 存在
    monkeypatch.setattr(lc.shutil, "which", lambda _x: "/usr/bin/claude")
    monkeypatch.setattr(lc.os.path, "isfile", lambda _x: True)


def test_build_client_picks_cli_without_key(no_key):
    assert isinstance(lc.build_client(), lc.CLIClient)
    assert lc.backend_name() == "claude-cli"


def test_build_client_picks_sdk_with_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-xxx")
    assert lc.backend_name() == "anthropic-sdk"
    fake = types.ModuleType("anthropic")
    fake.Anthropic = lambda *a, **k: "SDK_CLIENT"
    monkeypatch.setitem(__import__("sys").modules, "anthropic", fake)
    assert lc.build_client() == "SDK_CLIENT"


def test_cli_create_returns_compatible_shape(no_key, monkeypatch):
    monkeypatch.setattr(lc.subprocess, "run", _fake_run_factory(result="你好世界"))
    c = lc.build_client()
    resp = c.messages.create(model="claude-sonnet-4-6", max_tokens=100,
                             system="你是助手", messages=[{"role": "user", "content": "hi"}])
    assert resp.content[0].text == "你好世界"          # 兼容 msg.content[0].text
    assert resp.content[0].type == "text"
    assert resp.usage.input_tokens == 11 and resp.usage.output_tokens == 7


def test_cli_is_error_raises(no_key, monkeypatch):
    monkeypatch.setattr(lc.subprocess, "run", _fake_run_factory(is_error=True))
    with pytest.raises(RuntimeError):
        lc.build_client().messages.create(messages=[{"role": "user", "content": "x"}])


def test_cli_bad_returncode_raises(no_key, monkeypatch):
    monkeypatch.setattr(lc.subprocess, "run", _fake_run_factory(rc=1))
    with pytest.raises(RuntimeError):
        lc.build_client().messages.create(messages=[{"role": "user", "content": "x"}])


def test_cli_non_json_raises(no_key, monkeypatch):
    monkeypatch.setattr(lc.subprocess, "run", _fake_run_factory(raw="not json"))
    with pytest.raises(RuntimeError):
        lc.build_client().messages.create(messages=[{"role": "user", "content": "x"}])


def test_model_alias():
    assert lc._model_alias("claude-haiku-4-5") == "haiku"
    assert lc._model_alias("claude-opus-4-8") == "opus"
    assert lc._model_alias("claude-sonnet-4-6") == "sonnet"
    assert lc._model_alias("gpt-4") is None


def test_content_blocks_input(no_key, monkeypatch):
    # messages content 为 blocks 列表时也能拼
    captured = {}

    def _run(cmd, input=None, **k):
        captured["prompt"] = input
        return types.SimpleNamespace(returncode=0, stdout=json.dumps(
            {"result": "r", "is_error": False, "usage": {}}), stderr="")
    monkeypatch.setattr(lc.subprocess, "run", _run)
    lc.build_client().messages.create(
        system="S", messages=[{"role": "user", "content": [{"type": "text", "text": "块文本"}]}])
    assert "S" in captured["prompt"] and "块文本" in captured["prompt"]
