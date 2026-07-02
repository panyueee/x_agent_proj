# -*- coding: utf-8 -*-
"""signal_perf / signal_track 单测：手算前瞻收益、防未来函数、超额口径、聚合折叠。

全部用内存里手工造的宽表/DataFrame，不读 data/ 真实行情、不碰 output/ 下的库。

运行:
    .venv/bin/python -m pytest tests/test_signal_perf.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from x_agent.signal_perf import forward_returns, map_signal_ticker  # noqa: E402
from x_agent.signal_track import (matched_keywords, is_short, _signal_level,  # noqa: E402
                                  _bucket_stats)


# --------------------------------------------------------------------------- #
# 造一个 6 个交易日的迷你行情：symbol X close = [10,11,12,13,14,15]
# --------------------------------------------------------------------------- #
@pytest.fixture
def mkt():
    cal = pd.DatetimeIndex(pd.to_datetime(
        ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09", "2026-01-12"]))
    close = pd.DataFrame({"X": [10.0, 11.0, 12.0, 13.0, 14.0, 15.0]}, index=cal)
    # 基准 B：[100,100,100,100,100,100]（零收益）→ 超额==原始收益，便于手算
    bench = pd.Series([100.0] * 6, index=cal, name="B")
    return cal, close, bench


def _ev(signal_date, sym="X", sid="s1", score=5, market="a"):
    return pd.DataFrame([{"signal_id": sid, "market": market, "security_id": sym,
                         "signal_date": pd.Timestamp(signal_date), "score": score}])


# --------------------------------------------------------------------------- #
# 1. 手算前瞻收益 + 严格次日入场
# --------------------------------------------------------------------------- #
def test_forward_return_hand_computed(mkt):
    cal, close, bench = mkt
    # 信号本地日 = 01-05 → 入场严格取其后第一个交易日 = 01-06 (close=11)
    out = forward_returns(_ev("2026-01-05"), close, cal, bench, horizons=(1, 3))
    assert len(out) == 2
    r1 = out[out.horizon == 1].iloc[0]
    assert r1["entry_date"] == "2026-01-06"          # 严格之后，不是 01-05
    assert r1["entry_close"] == 11.0
    assert r1["exit_close"] == 12.0                    # 01-07
    assert r1["ret"] == pytest.approx(12 / 11 - 1)
    r3 = out[out.horizon == 3].iloc[0]
    assert r3["exit_close"] == 14.0                    # 01-06 + 3 = 01-09
    assert r3["ret"] == pytest.approx(14 / 11 - 1)


def test_zero_bench_excess_equals_ret(mkt):
    cal, close, bench = mkt
    out = forward_returns(_ev("2026-01-05"), close, cal, bench, horizons=(1,))
    r = out.iloc[0]
    assert r["excess"] == pytest.approx(r["ret"])      # 基准零收益 → 超额==收益
    assert r["hit"] == 1


# --------------------------------------------------------------------------- #
# 2. 防未来函数
# --------------------------------------------------------------------------- #
def test_after_hours_signal_never_uses_same_day_close(mkt):
    cal, close, bench = mkt
    # 盘后信号：本地日归一到 01-07（不管几点）→ 入场必须是 01-08，绝不用 01-07 收盘
    out = forward_returns(_ev("2026-01-07"), close, cal, bench, horizons=(1,))
    r = out.iloc[0]
    assert r["entry_date"] == "2026-01-08"
    assert r["entry_close"] == 13.0                    # 01-08，不是 01-07 的 12.0


def test_signal_on_last_day_dropped(mkt):
    cal, close, bench = mkt
    # 信号本地日 = 最后一个交易日 01-12 → 无"严格之后"的入场日 → 全丢
    out = forward_returns(_ev("2026-01-12"), close, cal, bench, horizons=(1,))
    assert len(out) == 0


def test_insufficient_future_bars_dropped_not_fabricated(mkt):
    cal, close, bench = mkt
    # 信号 01-08 → 入场 01-09(idx4)。h=1 → idx5 存在(ok)；h=3 → idx7 越界 → 丢
    out = forward_returns(_ev("2026-01-08"), close, cal, bench, horizons=(1, 3))
    assert set(out.horizon) == {1}                     # h=3 被丢，绝不补值
    assert len(out) == 1


def test_stale_benchmark_nulls_excess(mkt):
    cal, close, _ = mkt
    # 基准真实数据只到 01-07，之后无（模拟 000300 止于早于信号窗口）
    bench_short = pd.Series([100.0, 100.0, 100.0],
                            index=cal[:3], name="B")
    out = forward_returns(_ev("2026-01-07"), close, cal, bench_short, horizons=(1,))
    r = out.iloc[0]
    # 入场 01-08、出场 01-09 都超出基准真实覆盖(止于01-07) → excess 置空，回退用 ret 判 hit
    assert r["excess"] is None
    assert r["hit"] == 1                               # ret>0 回退


def test_tradable_entry_flag(mkt):
    cal, close, bench = mkt
    n = len(cal)
    # 默认无 tradable 矩阵 → flag=None
    out0 = forward_returns(_ev("2026-01-05"), close, cal, bench, horizons=(1,))
    assert out0.iloc[0]["tradable_entry"] is None
    # 入场日(01-06)一字涨停 → 无法建仓 → flag=0，但行仍保留（不丢）
    tradable = pd.DataFrame(True, index=cal, columns=["X"])
    olu = pd.DataFrame(False, index=cal, columns=["X"])
    olu.loc["2026-01-06", "X"] = True
    out1 = forward_returns(_ev("2026-01-05"), close, cal, bench, horizons=(1,),
                           tradable=tradable, open_limit_up=olu)
    assert out1.iloc[0]["tradable_entry"] == 0
    assert out1.iloc[0]["hit"] == 1                    # 收益照算，只是打标不可成交
    # 入场日停牌 → flag=0
    tr2 = pd.DataFrame(True, index=cal, columns=["X"]); tr2.loc["2026-01-06", "X"] = False
    out2 = forward_returns(_ev("2026-01-05"), close, cal, bench, horizons=(1,),
                           tradable=tr2, open_limit_up=pd.DataFrame(False, index=cal, columns=["X"]))
    assert out2.iloc[0]["tradable_entry"] == 0


def test_missing_price_dropped(mkt):
    cal, close, bench = mkt
    close = close.copy()
    close.loc["2026-01-06", "X"] = np.nan              # 入场价缺失
    out = forward_returns(_ev("2026-01-05"), close, cal, bench, horizons=(1,))
    assert len(out) == 0


# --------------------------------------------------------------------------- #
# 3. ticker 映射（数据驱动，用 tmp 假 parquet）
# --------------------------------------------------------------------------- #
def test_map_signal_ticker(tmp_path):
    (tmp_path / "crypto_history").mkdir()
    (tmp_path / "crypto_history" / "BTC-USD.parquet").write_text("x")
    (tmp_path / "stock_history" / "us").mkdir(parents=True)
    (tmp_path / "stock_history" / "us" / "NVDA.parquet").write_text("x")
    (tmp_path / "stock_history" / "a").mkdir(parents=True)
    (tmp_path / "stock_history" / "a" / "sh.600519.parquet").write_text("x")
    d = str(tmp_path)
    assert map_signal_ticker("$BTC", d) == ("crypto", "BTC-USD")
    assert map_signal_ticker("$NVDA", d) == ("us", "NVDA")   # 非加密 → 落美股
    assert map_signal_ticker("600519", d) == ("a", "sh.600519")
    assert map_signal_ticker("$FAKE", d) is None             # 无本地文件 → 丢
    assert map_signal_ticker("999999", d) is None            # 非 0/3/6 开头
    assert map_signal_ticker("hello", d) is None


# --------------------------------------------------------------------------- #
# 4. track 聚合：多标的信号只算一票 + 关键词/方向
# --------------------------------------------------------------------------- #
def test_signal_level_folds_multiticker():
    # signal s1 有两个标的(超额 +10% / -4% → 均值 +3% → hit=1)，s2 一个标的(-2% → hit=0)
    base = {"source": "tw", "source_label": "f", "category": "strategy", "text": "t",
            "score": 5, "short_flag": False, "excess_real": 1}
    df = pd.DataFrame([
        {"signal_id": "s1", "security_id": "A", "metric": 0.10, "ret": 0.10, "author": "u1", **base},
        {"signal_id": "s1", "security_id": "B", "metric": -0.04, "ret": -0.04, "author": "u1", **base},
        {"signal_id": "s2", "security_id": "C", "metric": -0.02, "ret": -0.02, "author": "u2", **base},
    ])
    sig = _signal_level(df)
    assert len(sig) == 2                               # 两条信号
    s1 = sig[sig.signal_id == "s1"].iloc[0]
    assert s1["n_obs"] == 2
    assert s1["metric"] == pytest.approx(0.03)         # (0.10-0.04)/2
    assert s1["hit"] == 1
    # 按 author 分桶：u1 一条信号(n_obs=2)、u2 一条
    by_au = _bucket_stats(sig, "author")
    u1 = by_au[by_au.author == "u1"].iloc[0]
    assert u1["n_signals"] == 1 and u1["n_obs"] == 2
    # hit_rate 全体 = 1/2
    assert _bucket_stats(sig, "source")["hit_rate"].iloc[0] == pytest.approx(0.5)


def test_matched_keywords_and_short():
    kws = matched_keywords("突破 take profit 目标价")
    assert "突破" in kws and "take profit" in kws and "目标价" in kws
    assert is_short("这票要跌停 做空") is True
    assert is_short("all-in 做多突破") is False
