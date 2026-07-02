"""绩效指标计算（公式口径参照 ffn，见 docs/backtest_design.md §3.3）。

- 年化收益：几何年化 (nav_end/nav_start)^(freq/期数) - 1
- 最大回撤：nav/cummax - 1 的最小值（负数）
- 夏普：日超额收益 均值/标准差 × √freq；波动为 0 记 0
- 超额收益：几何超额 (1+ra)/(1+rb) - 1

本模块对 result 只做 duck-typing（访问 .nav/.returns/.turnover/.trades/.benchmark），
运行时不 import engine，保持依赖方向 data ← engine ← ... ← metrics 单向。
只依赖 pandas/numpy/标准库。
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:  # 仅供类型标注，运行时不导入 engine
    from backtest.engine import BacktestResult


def annual_factor(index: pd.DatetimeIndex) -> float:
    """由日期索引推断年化因子：加密（7×24 日历）→ 365，股票等交易日历 → 252。

    注：设计文档写"日均间隔 <1.5 天 → 365"，但纯工作日日历的日均间隔
    ≈ 7/5 = 1.4 天，同样落在 1.5 以内，会把股票误判成 365。
    这里改用等价且稳健的判据——索引中是否出现过周六/周日：
    加密日历必然含周末（→365），交易所日历绝不含（→252）。
    """
    if index is None or len(index) < 2:
        return 252.0
    return 365.0 if bool((index.dayofweek >= 5).any()) else 252.0


def max_drawdown(nav: pd.Series) -> tuple[float, pd.Timestamp, pd.Timestamp]:
    """最大回撤（ffn 口径：drawdown = nav/cummax - 1）。

    返回 (回撤值为负数, 峰值日期, 谷值日期)。nav 单调不减时回撤为 0.0，
    峰/谷都落在序列起点（idxmin 对全 0 取首个）。
    """
    if nav is None or len(nav) == 0:
        return 0.0, pd.NaT, pd.NaT
    dd = nav / nav.cummax() - 1.0
    trough = dd.idxmin()
    mdd = float(dd.loc[trough])
    # 峰值 = 谷值之前净值最高的一天
    peak = nav.loc[:trough].idxmax()
    return mdd, peak, trough


def annualized_return(nav: pd.Series, freq: float) -> float:
    """几何年化收益：(nav_end/nav_start)^(freq/期数) - 1，期数 = len(nav)-1。"""
    if nav is None or len(nav) < 2:
        return 0.0
    start, end = float(nav.iloc[0]), float(nav.iloc[-1])
    if start <= 0 or end <= 0:  # 净值非正（爆仓等极端情形）无法取幂，退化为 0
        return 0.0
    n_periods = len(nav) - 1
    return (end / start) ** (freq / n_periods) - 1.0


def sharpe(returns: pd.Series, freq: float, rf: float = 0.0) -> float:
    """夏普比率（ffn 口径）：日超额收益 均值/标准差 × √freq。

    rf 为年化无风险利率，按 rf/freq 折算到单期。波动为 0（含只有一个样本）→ 0。
    """
    if returns is None or len(returns) < 2:
        return 0.0
    excess = returns - rf / freq
    std = float(excess.std())  # pandas 默认 ddof=1，与 ffn 一致
    if not math.isfinite(std) or std == 0.0:
        return 0.0
    return float(excess.mean()) / std * math.sqrt(freq)


def compute_metrics(result: "BacktestResult", freq: float | None = None) -> dict:
    """汇总一次回测的全部绩效指标。

    对 result 做 duck-typing：只要求具备 .nav/.returns/.turnover/.trades/.benchmark。
    返回 keys 与设计文档 §3.3 逐字对齐；无基准时不含 benchmark 相关 keys。
    空仓期（returns 全 0 段）照常计入统计（win_rate 分母、波动等）。
    """
    nav: pd.Series = result.nav
    returns: pd.Series = result.returns
    if freq is None:
        freq = annual_factor(nav.index)

    total_return = float(nav.iloc[-1] / nav.iloc[0] - 1.0) if len(nav) else 0.0
    ann_ret = annualized_return(nav, freq)
    ann_vol = float(returns.std() * math.sqrt(freq)) if len(returns) > 1 else 0.0
    if not math.isfinite(ann_vol):
        ann_vol = 0.0
    shp = sharpe(returns, freq)
    mdd, peak, trough = max_drawdown(nav)
    # 卡玛 = 年化 / |最大回撤|；无回撤时记 0（避免除零）
    calmar = ann_ret / abs(mdd) if mdd != 0.0 else 0.0
    # 胜率：日收益 > 0 的天数占比（空仓 0 收益日计入分母）
    win_rate = float((returns > 0).mean()) if len(returns) else 0.0

    turnover: pd.Series = result.turnover
    avg_turnover = float(turnover.mean()) if turnover is not None and len(turnover) else 0.0

    trades: pd.DataFrame = result.trades
    n_trades = int(len(trades)) if trades is not None else 0
    # trades 可能为空 DataFrame（甚至无 cost 列），求和前显式兜底
    if trades is not None and len(trades) and "cost" in trades.columns:
        total_cost = float(trades["cost"].sum())
    else:
        total_cost = 0.0

    out: dict = {
        "start": nav.index[0] if len(nav) else pd.NaT,
        "end": nav.index[-1] if len(nav) else pd.NaT,
        "n_days": int(len(nav)),
        "total_return": total_return,
        "annual_return": ann_ret,
        "annual_vol": ann_vol,
        "sharpe": shp,
        "max_drawdown": mdd,
        "max_drawdown_peak": peak,
        "max_drawdown_trough": trough,
        "calmar": calmar,
        "win_rate": win_rate,
        "avg_daily_turnover": avg_turnover,
        "n_trades": n_trades,
        "total_cost": total_cost,
    }

    benchmark: pd.Series | None = getattr(result, "benchmark", None)
    if benchmark is not None and len(benchmark.dropna()) >= 2:
        # 基准可能只覆盖部分区间（如沪深300 仅 2021-03 起），
        # 超额收益在"基准有值的重叠区间"上算：组合与基准取同一窗口，几何超额。
        bench = benchmark.dropna()
        b_total = float(bench.iloc[-1] / bench.iloc[0] - 1.0)
        b_ann = annualized_return(bench, freq)
        nav_overlap = nav.reindex(bench.index).dropna()
        ra = annualized_return(nav_overlap, freq) if len(nav_overlap) >= 2 else ann_ret
        out["benchmark_total_return"] = b_total
        out["benchmark_annual_return"] = b_ann
        out["excess_annual_return"] = (1.0 + ra) / (1.0 + b_ann) - 1.0

    return out
