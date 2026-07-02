"""
backtest/data.py 数据层测试（接口冻结版，见 docs/backtest_design.md §3.1 / §4）。

覆盖：
  - 多标的日历并集对齐（升序）；
  - 停牌缺行 → 价格 ffill 且 tradable=False；上市前开头 NaN 保持 NaN 且不可交易；
  - volume=0 的行 → 不可交易（价格仍取真实行情）；
  - A 股 adj_close 全 None（object 列）→ 复权因子取 1，直接用原始 OHLC；
  - us 样例 adj_close 有值 → factor = adj_close/close，OHLC 全部同乘；
  - 涨停标记（0.003 容差）：prev_close 10.0 → close 11.0 主板触发、10.9 不触发；
    开盘即涨停 open_limit_up 同口径；非 A 股市场 limit 矩阵全 False；
  - limit_rate：创业板 300/301 → 0.20、科创板 688 → 0.20、名称含 ST → 0.05、
    北交所 bj → 0.30、主板 → 0.10。name 直接传参，不依赖 data/a_share_names.json。

全部用 pytest tmp_path 下手工造的迷你 parquet（严格按 §1 schema：date 为字符串列、
含 symbol/market 列、RangeIndex），不读 data/ 真实数据、不碰 output/ 下任何文件。

运行：
    .venv/bin/python -m pytest tests/test_backtest_data.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import pytest

# 让 `import backtest.data` 在任意 cwd 下都能工作
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from backtest.data import MARKET_DIRS, limit_rate, load_market_data


# ── 辅助 ─────────────────────────────────────────────────────────────────────

# 2026-01-05（周一）起连续 4 个交易日
D1, D2, D3, D4 = "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"


def _write_parquet(data_dir: Path, market: str, symbol: str,
                   rows: list[dict], adj_factor: float | None = None) -> None:
    """按 §1 统一 schema 写一只标的的迷你 parquet。

    rows 每行至少含 date/close，其余列自动补齐；
    adj_factor=None → adj_close 整列 None（object 列，复刻 A 股 parquet 的真实形态）；
    否则 adj_close = close × adj_factor（复刻 us 有复权价的形态）。
    """
    sub = data_dir / MARKET_DIRS[market]
    sub.mkdir(parents=True, exist_ok=True)
    recs = []
    for r in rows:
        close = float(r["close"])
        recs.append({
            "date": r["date"],                       # 字符串列，如 "2026-01-05"
            "open": float(r.get("open", close)),
            "high": float(r.get("high", close)),
            "low": float(r.get("low", close)),
            "close": close,
            "volume": float(r.get("volume", 10_000)),
            "amount": float(r.get("amount", close * 10_000)),
            "adj_close": (close * adj_factor) if adj_factor is not None else None,
            "symbol": symbol,
            "market": market,
        })
    df = pd.DataFrame(recs)  # RangeIndex
    if adj_factor is None:
        # A 股真实形态：整列 None 的 object 列
        df["adj_close"] = pd.Series([None] * len(df), dtype=object)
    df.to_parquet(sub / f"{symbol}.parquet", index=False)


@pytest.fixture
def a_dir(tmp_path: Path) -> Path:
    """两只 A 股迷你 parquet，覆盖对齐/停牌/volume=0/涨停多种形态。

    sh.600000：D1~D4 全有行；D1→D2 收盘 10.0→11.0（主板 +10% 涨停，且开盘即涨停）；
               D3 volume=0（有行但不可交易）。
    sh.600001：仅 D2、D4 有行 → D1 未上市（开头 NaN）、D3 停牌缺行（ffill）；
               D2 close 10.0 → D4 close 10.9（+9%，不触发涨停）。
    """
    _write_parquet(tmp_path, "a", "sh.600000", [
        {"date": D1, "open": 10.0, "close": 10.0},
        {"date": D2, "open": 11.0, "close": 11.0},               # 开盘即涨停 + 收盘涨停
        {"date": D3, "open": 11.2, "close": 11.5, "volume": 0},  # volume=0 → 不可交易
        {"date": D4, "open": 11.5, "close": 11.5},
    ])
    _write_parquet(tmp_path, "a", "sh.600001", [
        {"date": D2, "open": 10.0, "close": 10.0},
        {"date": D4, "open": 10.2, "close": 10.9},               # +9% < 9.7%，不触发
    ])
    return tmp_path


def _load_a(a_dir: Path):
    return load_market_data("a", ["sh.600000", "sh.600001"], data_dir=a_dir)


# ── 1. 日历并集对齐 ───────────────────────────────────────────────────────────

def test_calendar_is_union_sorted(a_dir):
    """calendar = 所有标的日期的并集，升序 DatetimeIndex。"""
    md = _load_a(a_dir)
    expected = pd.to_datetime([D1, D2, D3, D4])
    assert list(md.calendar) == list(expected)
    assert md.calendar.is_monotonic_increasing
    # 各价格矩阵均对齐到统一日历
    assert list(md.close.index) == list(expected)
    assert set(md.close.columns) == {"sh.600000", "sh.600001"}


# ── 2. 停牌缺行 / 上市前 / volume=0 ──────────────────────────────────────────

def test_suspension_missing_row_ffill_not_tradable(a_dir):
    """sh.600001 D3 无行（停牌）→ 价格从 D2 ffill，tradable=False。"""
    md = _load_a(a_dir)
    t3 = pd.Timestamp(D3)
    assert md.close.loc[t3, "sh.600001"] == pytest.approx(10.0)   # ffill 自 D2
    assert md.open.loc[t3, "sh.600001"] == pytest.approx(10.0)
    assert not bool(md.tradable.loc[t3, "sh.600001"])


def test_leading_nan_before_listing(a_dir):
    """sh.600001 D2 才上市 → D1 保持 NaN（不 ffill 也不 bfill），且不可交易。"""
    md = _load_a(a_dir)
    t1 = pd.Timestamp(D1)
    assert pd.isna(md.close.loc[t1, "sh.600001"])
    assert not bool(md.tradable.loc[t1, "sh.600001"])
    # 有真实行情的日子应可交易
    assert bool(md.tradable.loc[pd.Timestamp(D2), "sh.600001"])
    assert bool(md.tradable.loc[pd.Timestamp(D4), "sh.600001"])


def test_volume_zero_not_tradable(a_dir):
    """sh.600000 D3 有行但 volume=0 → 不可交易；价格仍取该行真实值。"""
    md = _load_a(a_dir)
    t3 = pd.Timestamp(D3)
    assert not bool(md.tradable.loc[t3, "sh.600000"])
    assert md.close.loc[t3, "sh.600000"] == pytest.approx(11.5)
    assert md.volume.loc[t3, "sh.600000"] == 0


# ── 3. 复权处理 ───────────────────────────────────────────────────────────────

def test_adj_close_all_none_factor_is_1(a_dir):
    """A 股 adj_close 整列 None → factor=1，close 就是原始（前复权）价。"""
    md = _load_a(a_dir)
    assert md.close.loc[pd.Timestamp(D1), "sh.600000"] == pytest.approx(10.0)
    assert md.close.loc[pd.Timestamp(D2), "sh.600000"] == pytest.approx(11.0)
    assert md.open.loc[pd.Timestamp(D3), "sh.600000"] == pytest.approx(11.2)


def test_us_adj_close_scales_all_ohlc(tmp_path):
    """us 样例 adj_close 有值 → factor = adj_close/close = 0.5，OHLC 全部同乘。"""
    _write_parquet(tmp_path, "us", "TEST", [
        {"date": D1, "open": 98.0, "high": 101.0, "low": 97.0, "close": 100.0},
        {"date": D2, "open": 105.0, "high": 112.0, "low": 104.0, "close": 110.0},
    ], adj_factor=0.5)
    md = load_market_data("us", ["TEST"], data_dir=tmp_path)
    t1, t2 = pd.Timestamp(D1), pd.Timestamp(D2)
    # close 变为复权价 = 原始 × 0.5
    assert md.close.loc[t1, "TEST"] == pytest.approx(50.0)
    assert md.close.loc[t2, "TEST"] == pytest.approx(55.0)
    # OHLC 同乘同一 factor
    assert md.open.loc[t1, "TEST"] == pytest.approx(49.0)
    assert md.open.loc[t2, "TEST"] == pytest.approx(52.5)
    assert md.high.loc[t1, "TEST"] == pytest.approx(50.5)
    assert md.low.loc[t2, "TEST"] == pytest.approx(52.0)
    # 非 A 股：涨跌停矩阵全 False
    assert not md.limit_up.to_numpy().any()
    assert not md.limit_down.to_numpy().any()
    assert not md.open_limit_up.to_numpy().any()
    assert not md.open_limit_down.to_numpy().any()


# ── 4. 涨跌停标记（仅 A 股） ──────────────────────────────────────────────────

def test_limit_up_triggered_at_10pct(a_dir):
    """主板 prev_close 10.0 → close 11.0（+10% ≥ 10%-0.3% 容差）→ limit_up=True。"""
    md = _load_a(a_dir)
    t2 = pd.Timestamp(D2)
    assert bool(md.limit_up.loc[t2, "sh.600000"])
    # 开盘 11.0 / prev_close 10.0 = +10% → 开盘即涨停
    assert bool(md.open_limit_up.loc[t2, "sh.600000"])
    # 首日无 prev_close，不可能标记涨停
    assert not bool(md.limit_up.loc[pd.Timestamp(D1), "sh.600000"])
    # D2→D3：11.0→11.5（+4.5%）不触发
    assert not bool(md.limit_up.loc[pd.Timestamp(D3), "sh.600000"])


def test_limit_up_not_triggered_at_9pct(a_dir):
    """prev_close 10.0 → close 10.9（+9% < 10%-0.3%）→ 不触发。"""
    md = _load_a(a_dir)
    t4 = pd.Timestamp(D4)
    assert not bool(md.limit_up.loc[t4, "sh.600001"])
    # 开盘 10.2（+2%）也远未及涨停
    assert not bool(md.open_limit_up.loc[t4, "sh.600001"])
    # 全程无跌停
    assert not md.limit_down["sh.600001"].to_numpy().any()


# ── 5. limit_rate 涨跌停幅度 ──────────────────────────────────────────────────

def test_limit_rate_chinext_20pct():
    """创业板 300/301、科创板 688/689 前缀 → 0.20。"""
    assert limit_rate("sz.300750", None) == pytest.approx(0.20)
    assert limit_rate("sz.301111", None) == pytest.approx(0.20)
    assert limit_rate("sh.688981", None) == pytest.approx(0.20)


def test_limit_rate_st_5pct():
    """名称含 ST / *ST → 0.05（name 直接传参，不查 a_share_names.json）。"""
    assert limit_rate("sz.000001", "ST某某") == pytest.approx(0.05)
    assert limit_rate("sh.600000", "*ST海航") == pytest.approx(0.05)


def test_limit_rate_main_board_10pct():
    """主板普通股票 → 0.10；name=None 或普通名称均按代码判定。"""
    assert limit_rate("sh.600519", None) == pytest.approx(0.10)
    assert limit_rate("sz.000001", "平安银行") == pytest.approx(0.10)


def test_limit_rate_bse_30pct():
    """北交所 bj 前缀 → 0.30。"""
    assert limit_rate("bj.830001", None) == pytest.approx(0.30)
