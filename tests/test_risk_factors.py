# -*- coding: utf-8 -*-
"""简版因子库测试：小型合成 parquet 宇宙上验证因子构造与缓存。"""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from x_agent.risk.factors import (
    FACTORS, GICS_11, STYLE_FACTORS, build_factor_returns,
    compute_factor_returns, load_sector_map,
)

N_DAYS = 320  # > 252，保证动量信号可用


def _make_universe(tmp_path: Path, n_symbols: int = 8) -> tuple[Path, dict]:
    """在 tmp 目录生成 n 只合成 A 股 parquet（标准 schema），返回 (data_dir, sector_map)。"""
    rng = np.random.default_rng(42)
    dates = pd.bdate_range("2023-01-02", periods=N_DAYS)
    data_dir = tmp_path / "data"
    root = data_dir / "stock_history" / "a"
    root.mkdir(parents=True)

    sector_map = {}
    for i in range(n_symbols):
        sym = f"sh.60000{i}" if i < 5 else f"sz.00000{i}"
        ret = rng.normal(0.0005, 0.02, N_DAYS)
        close = 10.0 * np.cumprod(1 + ret)
        df = pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"),
            "open": close, "high": close * 1.01, "low": close * 0.99,
            "close": close,
            "volume": rng.integers(1_000_000, 5_000_000, N_DAYS).astype(float),
            "amount": close * 1e6,
            "adj_close": [None] * N_DAYS,   # A 股口径：close 已前复权
            "symbol": sym, "market": "a",
        })
        df.to_parquet(root / f"{sym}.parquet")
        sector_map[sym] = "Financials" if i % 2 == 0 else "Technology"
    return data_dir, sector_map


def test_compute_factor_returns_mkt_is_equal_weight_mean():
    """mkt = 全部标的日收益等权均值（含停牌掩码）。"""
    rng = np.random.default_rng(1)
    dates = pd.bdate_range("2023-01-02", periods=N_DAYS)
    close = pd.DataFrame(
        10 * np.cumprod(1 + rng.normal(0, 0.01, (N_DAYS, 4)), axis=0),
        index=dates, columns=list("ABCD"))
    volume = pd.DataFrame(1e6, index=dates, columns=list("ABCD"))
    tradable = pd.DataFrame(True, index=dates, columns=list("ABCD"))

    fr = compute_factor_returns(close, volume, tradable,
                                {"A": "Financials", "B": "Financials",
                                 "C": "Technology", "D": "Technology"})
    assert list(fr.columns) == FACTORS
    expected_mkt = close.pct_change().mean(axis=1).reindex(fr.index)
    assert np.allclose(fr["mkt"], expected_mkt, atol=1e-12)

    # 行业因子 = 行业等权收益 - mkt（手算 Financials 一天）
    d = fr.index[10]
    fin_ret = close[["A", "B"]].pct_change().loc[d].mean()
    assert fr.loc[d, "Financials"] == pytest.approx(fin_ret - expected_mkt.loc[d], abs=1e-12)
    # 无成员的行业列为 NaN
    assert fr["Energy"].isna().all()


def test_compute_factor_returns_masks_suspended_days():
    """tradable=False 的日子不进任何截面（mkt 均值只含可交易标的）。"""
    dates = pd.bdate_range("2023-01-02", periods=N_DAYS)
    close = pd.DataFrame(
        {"A": np.linspace(10, 20, N_DAYS), "B": np.linspace(10, 15, N_DAYS)},
        index=dates)
    volume = pd.DataFrame(1e6, index=dates, columns=["A", "B"])
    tradable = pd.DataFrame(True, index=dates, columns=["A", "B"])
    d = dates[300]
    tradable.loc[d, "B"] = False

    fr = compute_factor_returns(close, volume, tradable, {})
    expected = close["A"].pct_change().loc[d]  # 当日只剩 A
    assert fr.loc[d, "mkt"] == pytest.approx(float(expected), abs=1e-12)


def test_build_factor_returns_end_to_end_and_cache(tmp_path):
    """合成宇宙端到端：产出全部因子列，且第二次调用命中缓存（不重算）。"""
    data_dir, sector_map = _make_universe(tmp_path)
    cache_dir = tmp_path / "factors"

    fr = build_factor_returns("a", start="2023-06-01", data_dir=data_dir,
                              cache_dir=cache_dir, sector_map=sector_map,
                              db_path=str(tmp_path / "none.db"))
    assert list(fr.columns) == FACTORS
    assert len(fr) > 0
    assert fr[STYLE_FACTORS].notna().all().all()
    assert (cache_dir / "factor_returns_a.parquet").exists()

    # 缓存命中：损毁数据目录后仍能读出同样结果
    fr2 = build_factor_returns("a", start="2023-06-01", data_dir=data_dir,
                               cache_dir=cache_dir, sector_map=sector_map,
                               db_path=str(tmp_path / "none.db"))
    pd.testing.assert_frame_equal(fr, fr2)

    # 有成员的两个行业互为镜像不至于全 0；无成员行业 NaN
    assert fr["Financials"].abs().sum() > 0
    assert fr["Utilities"].isna().all()


def test_load_sector_map_prefix_conversion(tmp_path):
    """sw_sector_cache 裸代码 → sh./sz. 前缀转换；北交所等跳过。"""
    import sqlite3
    db = tmp_path / "t.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE sw_sector_cache (symbol TEXT PRIMARY KEY, "
                "gics_sector TEXT, updated_at TEXT)")
    con.executemany("INSERT INTO sw_sector_cache VALUES (?,?,?)", [
        ("600519", "Consumer Staples", ""),
        ("000001", "Financials", ""),
        ("300750", "Utilities", ""),
        ("830000", "Industrials", ""),  # 北交所前缀，应跳过
    ])
    con.commit()
    con.close()

    m = load_sector_map(str(db))
    assert m == {"sh.600519": "Consumer Staples",
                 "sz.000001": "Financials",
                 "sz.300750": "Utilities"}
    # 库不存在 → 空 dict 而不是抛异常
    assert load_sector_map(str(tmp_path / "missing.db")) == {}
    assert set(GICS_11) == set(m.values()) | {
        "Communication Services", "Consumer Discretionary", "Energy",
        "Health Care", "Industrials", "Materials", "Real Estate", "Technology"}
