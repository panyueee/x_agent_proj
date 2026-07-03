# -*- coding: utf-8 -*-
"""历史危机场景重放（批次二）：情景日期在数据范围内、合成/重放损益手算、防未来函数。"""
import numpy as np
import pandas as pd
import pytest

from x_agent.risk.factors import STYLE_FACTORS
from x_agent.risk.scenarios import (
    SCENARIOS, load_scenario_returns, replay,
)

FACTOR_CACHE = "output/factors/factor_returns_a.parquet"


def _load_factor_returns():
    fr = pd.read_parquet(FACTOR_CACHE)
    fr.index = pd.to_datetime(fr.index)
    return fr


# ---- 1. 情景日期都落在真实数据范围内 ----

def test_scenario_dates_within_factor_history():
    """每个情景的窗口都能在因子收益缓存里切出 >=2 天（起止是真实交易区间）。"""
    fr = _load_factor_returns()
    lo, hi = fr.index[0], fr.index[-1]
    for name, (start, end, bench, _desc) in SCENARIOS.items():
        s, e = pd.to_datetime(start), pd.to_datetime(end)
        assert s < e, f"{name}: 起止顺序错误"
        assert s >= lo, f"{name}: {start} 早于因子史起点 {lo.date()}（应标数据不足）"
        assert e <= hi, f"{name}: {end} 晚于因子史终点 {hi.date()}"
        window = fr.loc[s:e]
        assert len(window) >= 2, f"{name}: 窗口内因子收益不足 2 天"


# ---- 2. 合成腿损益手算 ----

def test_synthetic_pnl_hand_computed():
    """全 synthetic：单标的 β=mkt=1，3 天因子路径 → 复利总损益/回撤/最惨日手算核对。"""
    dates = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])  # 在 2024_microcap 窗口内
    fr = pd.DataFrame(0.0, index=dates, columns=STYLE_FACTORS)
    fr["mkt"] = [0.02, -0.10, 0.05]     # 首日值会被基准日置 0，不影响结果
    betas = pd.DataFrame(0.0, index=["X"], columns=STYLE_FACTORS)
    betas.loc["X", "mkt"] = 1.0
    weights = pd.Series({"X": 1.0})

    res = replay(weights, "2024_microcap", betas, fr, real_returns=None)

    # 合成腿：r = [0(基准日), -0.10, 0.05] → 复利 0.9*1.05-1
    assert res.total_return == pytest.approx(0.9 * 1.05 - 1.0, abs=1e-12)
    assert res.max_daily_loss == pytest.approx(-0.10, abs=1e-12)
    assert res.max_daily_loss_date == "2024-01-03"
    assert res.max_drawdown == pytest.approx(-0.10, abs=1e-12)   # nav=[1,0.9,0.945]
    assert res.coverage == 0.0                                   # 无真实重放
    assert res.per_position.loc["X", "mode"] == "synthetic"
    assert res.per_position.loc["X", "ret"] == pytest.approx(0.9 * 1.05 - 1.0, abs=1e-12)


# ---- 3. 重放腿损益 = 真实收盘价窗口收益 ----

def test_replay_leg_matches_raw_parquet():
    """单只 A 股 replay 腿：情景总收益 == close[master末]/close[master首]-1（真实前复权价）。"""
    from backtest.data import load_market_data

    fr = _load_factor_returns()
    start, end, _bench, _desc = SCENARIOS["2015_crash"]
    master = fr.loc[pd.to_datetime(start):pd.to_datetime(end)].index

    sym = "sh.600519"   # 贵州茅台，2015 有完整历史
    real = load_scenario_returns({"a": [sym]}, start, end, master, data_dir="data")
    assert sym in real.columns, "参考标的应有真实历史（replay 腿）"

    betas = pd.DataFrame(0.0, index=[sym], columns=STYLE_FACTORS)  # replay 腿用不到 β
    res = replay(pd.Series({sym: 1.0}), "2015_crash", betas, fr, real_returns=real)

    md = load_market_data("a", [sym], start=start, end=end, data_dir="data")
    close = md.close[sym].reindex(master).ffill()
    expected = float(close.iloc[-1] / close.iloc[0] - 1.0)

    assert res.per_position.loc[sym, "mode"] == "replay"
    assert res.coverage == pytest.approx(1.0)
    assert res.total_return == pytest.approx(expected, abs=1e-9)
    assert res.benchmark_return is None                          # 未传基准


# ---- 4. 防未来函数：只用窗口内数据 ----

def test_no_lookahead_beyond_window():
    """factor_returns 在窗口后追加剧烈行情，replay 结果不变（只切 [start,end]）。"""
    dates = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
    fr = pd.DataFrame(0.0, index=dates, columns=STYLE_FACTORS)
    fr["mkt"] = [0.01, -0.03, 0.02]
    betas = pd.DataFrame({"mkt": [1.2]}, index=["X"]).reindex(columns=STYLE_FACTORS).fillna(0.0)
    weights = pd.Series({"X": 1.0})

    res_win = replay(weights, "2024_microcap", betas, fr, real_returns=None)

    # 窗口后再塞一个 -50% 的日子（未来数据）
    fr_future = pd.concat([fr, pd.DataFrame(
        {"mkt": [-0.50]}, index=pd.to_datetime(["2024-03-01"])).reindex(
        columns=STYLE_FACTORS).fillna(0.0)])
    res_full = replay(weights, "2024_microcap", betas, fr_future, real_returns=None)

    assert res_full.total_return == pytest.approx(res_win.total_return, abs=1e-12)
    assert res_full.n_days == res_win.n_days == 3
    assert len(res_full.nav) == 3


# ---- 5. 数据不足的情景应显式报错 ----

def test_scenario_before_history_raises():
    """起始早于因子史第一天 → 数据不足，明确抛错（不静默给错数）。"""
    dates = pd.to_datetime(["2020-01-20", "2020-02-01", "2020-03-23"])  # 2020_covid 窗口
    fr = pd.DataFrame(0.0, index=dates, columns=STYLE_FACTORS)
    betas = pd.DataFrame(0.0, index=["X"], columns=STYLE_FACTORS)
    weights = pd.Series({"X": 1.0})
    # 因子史从 2020-01-20 起，跑 2008_gfc（2008 起始）应报数据不足
    with pytest.raises(ValueError, match="数据不足"):
        replay(weights, "2008_gfc", betas, fr, real_returns=None)
