"""
dune_fetcher.py 解析逻辑回归测试。

测试范围（纯逻辑，不做任何真实网络/SDK 调用）：
  - _safe_float：失败回退 0.0
  - _fmt_addr：长地址缩写
  - DuneFetcher.fetch_smart_money / fetch_whale_alerts / fetch_btc_holders
    —— 用 mock 替换 _latest，喂入仿真 rows，验证字段映射/过滤门槛/Tweet 结构
  - build_dune_client：配置开关 / 缺 Key 的分支

注意：dune-client SDK 可能未安装。DuneFetcher.__init__ 会 import dune_client，
为避免依赖真实 SDK，我们用 DuneFetcher.__new__ 绕过 __init__，再 mock _latest。

本文件既可用 pytest 运行：
    python -m pytest tests/test_dune_fetcher.py -v
也可直接当脚本运行：
    python tests/test_dune_fetcher.py
"""
from __future__ import annotations

import os
import sys
from unittest import mock

# 让 `import x_agent.dune_fetcher` 在任意 cwd 下都能工作
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from x_agent import dune_fetcher as df
from x_agent.dune_fetcher import (
    DuneFetcher,
    build_dune_client,
    _safe_float,
    _fmt_addr,
    QUERY_SMART_MONEY,
    QUERY_WHALE_TRANSFER,
    QUERY_BTC_HOLDERS,
)
from x_agent.fetcher import Tweet


# ── 测试替身 ──────────────────────────────────────────────────────────────────

def _make_fetcher(rows):
    """绕过 __init__（避免 import dune_client），返回一个 _latest 固定吐 rows 的 DuneFetcher。"""
    f = DuneFetcher.__new__(DuneFetcher)
    f._client = None
    f._latest = lambda query_id: list(rows)  # type: ignore[attr-defined]
    return f


# ── 1. _safe_float ────────────────────────────────────────────────────────────

def test_safe_float_valid():
    assert _safe_float("1234.5") == 1234.5
    assert _safe_float(42) == 42.0
    assert _safe_float(3.14) == 3.14
    assert _safe_float("0") == 0.0


def test_safe_float_invalid_returns_zero():
    assert _safe_float(None) == 0.0
    assert _safe_float("abc") == 0.0
    assert _safe_float("") == 0.0
    assert _safe_float([]) == 0.0


# ── 2. _fmt_addr ──────────────────────────────────────────────────────────────

def test_fmt_addr_long_abbreviates():
    addr = "0x1234567890abcdef1234567890abcdef12345678"
    assert _fmt_addr(addr) == "0x1234...5678"


def test_fmt_addr_short_unchanged():
    # 长度 <= 12 原样返回
    assert _fmt_addr("0x1234") == "0x1234"
    assert _fmt_addr("123456789012") == "123456789012"  # 恰好 12


def test_fmt_addr_none_and_empty():
    assert _fmt_addr(None) == ""
    assert _fmt_addr("") == ""


# ── 3. fetch_smart_money ───────────────────────────────────────────────────────

def test_fetch_smart_money_parses_and_filters():
    rows = [
        {  # 满足门槛
            "wallet": "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1111",
            "action": "buy",
            "token": "PEPE",
            "usd_value": 750000,
            "block_time": "2026-06-01T00:00:00Z",
        },
        {  # 低于默认门槛 500000，应被过滤
            "wallet": "0xBBBB",
            "action": "sell",
            "token": "DOGE",
            "usd_value": 100000,
        },
    ]
    f = _make_fetcher(rows)
    tweets = f.fetch_smart_money()
    assert len(tweets) == 1
    tw = tweets[0]
    assert isinstance(tw, Tweet)
    assert tw.id == f"dune_sm_{QUERY_SMART_MONEY}_0"
    assert tw.author == "dune_analytics"
    assert tw.source_label == "onchain"
    assert tw.group_tag == "onchain"
    assert "0xAAAA...1111" in tw.text
    assert "buy" in tw.text and "PEPE" in tw.text
    assert tw.created_at == "2026-06-01T00:00:00Z"
    assert tw.metrics["usd_value"] == 750000
    assert tw.url == f"https://dune.com/queries/{QUERY_SMART_MONEY}"


def test_fetch_smart_money_custom_threshold():
    rows = [{"wallet": "0x1", "action": "buy", "token": "X", "usd_value": 200000}]
    f = _make_fetcher(rows)
    assert f.fetch_smart_money(min_usd=100000)  # 通过
    assert f.fetch_smart_money(min_usd=300000) == []  # 被过滤


def test_fetch_smart_money_alias_fields():
    # 使用 address / type / symbol / amount_usd 别名字段
    rows = [{
        "address": "0xCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC9999",
        "type": "swap",
        "symbol": "WBTC",
        "amount_usd": 600000,
    }]
    f = _make_fetcher(rows)
    tweets = f.fetch_smart_money()
    assert len(tweets) == 1
    assert "swap" in tweets[0].text
    assert "WBTC" in tweets[0].text


def test_fetch_smart_money_empty_rows():
    assert _make_fetcher([]).fetch_smart_money() == []


# ── 4. fetch_whale_alerts ──────────────────────────────────────────────────────

def test_fetch_whale_alerts_parses_and_filters():
    rows = [
        {
            "from": "0xFROMFROMFROMFROMFROMFROMFROMFROM1111",
            "to": "0xTOTOTOTOTOTOTOTOTOTOTOTOTOTOTOTO2222",
            "token_symbol": "USDT",
            "token_amount": 5000000,
            "usd_value": 5000000,
            "block_time": "2026-06-02T00:00:00Z",
        },
        {  # 低于 1,000,000 门槛
            "from": "0xa", "to": "0xb", "token_symbol": "ETH",
            "token_amount": 1, "usd_value": 500,
        },
    ]
    f = _make_fetcher(rows)
    tweets = f.fetch_whale_alerts()
    assert len(tweets) == 1
    tw = tweets[0]
    assert tw.id == f"dune_wh_{QUERY_WHALE_TRANSFER}_0"
    assert "USDT" in tw.text
    assert "0xFROM...1111" in tw.text
    assert "0xTOTO...2222" in tw.text
    assert tw.metrics["usd_value"] == 5000000
    assert tw.metrics["token_amount"] == 5000000
    assert tw.created_at == "2026-06-02T00:00:00Z"


def test_fetch_whale_alerts_default_token_eth():
    # 不提供 token_symbol/symbol → 默认 ETH
    rows = [{"from": "0x1", "to": "0x2", "amount": 100, "usd_value": 2000000}]
    f = _make_fetcher(rows)
    tweets = f.fetch_whale_alerts()
    assert len(tweets) == 1
    assert "ETH" in tweets[0].text


# ── 5. fetch_btc_holders ───────────────────────────────────────────────────────

def test_fetch_btc_holders_parses_and_filters():
    rows = [
        {"cohort": "巨鲸", "balance": 12000, "change": 250},
        {"cohort": "小户", "balance": 5, "change": 3},   # |change| < 10 被过滤
    ]
    f = _make_fetcher(rows)
    tweets = f.fetch_btc_holders()
    assert len(tweets) == 1
    tw = tweets[0]
    assert tw.id == f"dune_btc_{QUERY_BTC_HOLDERS}_0"
    assert "巨鲸" in tw.text
    assert "+250" in tw.text  # 正变化带 + 号
    assert tw.metrics["token_amount"] == 12000


def test_fetch_btc_holders_negative_change_sign():
    rows = [{"cohort": "大户", "balance": 8000, "change": -150}]
    f = _make_fetcher(rows)
    tweets = f.fetch_btc_holders()
    assert len(tweets) == 1
    # 负变化不额外加号（已有负号）
    assert "-150" in tweets[0].text
    assert "+-150" not in tweets[0].text


def test_fetch_btc_holders_limit_top_10():
    # 构造 12 条均满足门槛，只取前 10 条
    rows = [{"cohort": f"c{i}", "balance": 1000, "change": 100} for i in range(12)]
    f = _make_fetcher(rows)
    tweets = f.fetch_btc_holders()
    assert len(tweets) == 10


# ── 6. build_dune_client ───────────────────────────────────────────────────────

def test_build_dune_client_disabled_returns_none():
    assert build_dune_client({}) is None
    assert build_dune_client({"dune": {"enabled": False}}) is None


def test_build_dune_client_no_api_key_returns_none():
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("DUNE_API_KEY", None)
        assert build_dune_client({"dune": {"enabled": True}}) is None


def test_build_dune_client_init_failure_returns_none():
    # 提供 Key，但 DuneFetcher 初始化抛错（如 SDK 缺失）→ 捕获后返回 None
    with mock.patch.dict(os.environ, {"DUNE_API_KEY": "fake"}):
        with mock.patch.object(df, "DuneFetcher", side_effect=ImportError("no sdk")):
            assert build_dune_client({"dune": {"enabled": True}}) is None


def test_build_dune_client_success():
    sentinel = object()
    with mock.patch.dict(os.environ, {"DUNE_API_KEY": "fake"}):
        with mock.patch.object(df, "DuneFetcher", return_value=sentinel) as MF:
            got = build_dune_client({"dune": {"enabled": True}})
            assert got is sentinel
            MF.assert_called_once_with("fake")


# ── 7. DuneFetcher.__init__ 校验 ───────────────────────────────────────────────

def test_dune_fetcher_empty_key_raises():
    import pytest
    with pytest.raises(ValueError):
        DuneFetcher("")


# ── 独立运行入口（无 pytest 时） ──────────────────────────────────────────────

def _run_standalone() -> int:
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {fn.__name__}: {e}")
            failed += 1
        except Exception as e:  # noqa: BLE001
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed (total {passed + failed})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_standalone())
