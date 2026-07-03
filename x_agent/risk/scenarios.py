"""历史危机场景重放（个人版压力测试，批次二 / 提案 2）。

把"当前组合"整体丢进历史危机窗口，看逐日损益、总损益、最大回撤、最惨单日。

方法（取舍见 docs/aladdin/05-personal-roadmap.md §4）：
  - 情景库 SCENARIOS：起止日对本地 index_history 校准到真实交易日；
  - 真实因子收益路径：从 output/factors/factor_returns_a.parquet 切窗口；
  - 权重固定为当前权重、不再平衡，逐日复利；
  - 每只持仓两种模式：
      · replay   —— 窗口内有真实行情：收盘价 reindex 到 A 股窗口日历 → ffill →
                    pct_change（跨市场休市日的涨跌被并入下一 A 交易日，路径守恒）；
      · synthetic—— 当年未上市/无数据：ret_t = β · factor_ret_t（特质项取 0，
                    系统性低估极端损失，报告对该权重标 coverage）；
  - 起始日为基准日（NAV=1，当日收益记 0），两种腿一致，replay 腿总收益精确等于
    close[end]/close[start]-1，与指数校准口径对齐。

局限：
  - synthetic 忽略特质波动 → 保守低估个股极端损失；
  - 窗口中途上市的标的（如 sz.300750 在 2018 窗口中段 IPO）replay 腿上市前为平（收益 0）；
  - 因子归因（b·Σfr）是纯系统性线性近似，replay 腿（真实收益）不落在 A 股因子空间内，
    故因子归因不与组合总损益对账，仅作系统性主因参考；逐持仓贡献才是"谁最伤"的主口径。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.data import load_benchmark, load_market_data

from .exposure import portfolio_exposure

# name: (start, end, benchmark, 描述)。起止日已对 data/index_history 校准（真实交易日）。
SCENARIOS: dict[str, tuple[str, str, str, str]] = {
    "2008_gfc":      ("2008-01-14", "2008-11-04", "000001_SS", "全球金融危机（上证 -69%）"),
    "2015_crash":    ("2015-06-12", "2015-08-26", "000001_SS", "A 股杠杆股灾（上证 -43%）"),
    "2016_fuse":     ("2015-12-31", "2016-01-28", "000001_SS", "熔断（上证 -25%）"),
    "2018_tradewar": ("2018-01-24", "2018-10-18", "000001_SS", "中美贸易战阴跌（上证 -30%）"),
    "2020_covid":    ("2020-01-20", "2020-03-23", "000001_SS", "疫情熔断（含美股联动，上证 -14%）"),
    "2022_fed":      ("2021-12-13", "2022-04-26", "000300_SS", "美联储激进加息（沪深300 -26%）"),
    "2024_microcap": ("2024-01-02", "2024-02-07", "000001_SS", "微盘股流动性危机（上证 -5%）"),
}


@dataclass
class ScenarioResult:
    name: str
    desc: str
    start: str
    end: str
    n_days: int
    total_return: float                 # 组合情景总收益（复利）
    max_daily_loss: float               # 最惨单日收益
    max_daily_loss_date: str | None
    max_drawdown: float                 # 情景窗口内最大回撤
    benchmark_return: float | None      # 同窗口基准指数收益（sanity gate）
    coverage: float                     # replay 模式（真实重放）权重占比
    per_position: pd.DataFrame          # symbol, mode, weight, ret, contrib
    factor_attr: pd.Series              # factor → 系统性损益近似（b_f·Σfr_f）
    nav: pd.Series = field(default=None, repr=False)  # 逐日净值路径


# ---- 内部工具 ----

def _prep_returns(series_by_col: pd.DataFrame, master: pd.DatetimeIndex) -> pd.DataFrame:
    """价格宽表 → 对齐 master 日历 → ffill → pct_change；首日置 0（基准日）。

    首日之后仍为 NaN 的（上市前的前导 NaN）填 0 = 上市前平价（不参与损益）。
    """
    px = series_by_col.reindex(master).ffill()
    ret = px.pct_change(fill_method=None)
    ret.iloc[0] = 0.0
    return ret.fillna(0.0)


def load_scenario_returns(symbols_by_market: dict[str, list[str]],
                          start: str, end: str, master: pd.DatetimeIndex,
                          data_dir: str | Path = "data") -> pd.DataFrame:
    """加载各持仓在窗口内的真实日收益（价格法，见 §4 / advisor #1）。

    返回 date(master)×symbol 收益宽表，只含窗口内有真实数据的标的；
    完全无数据（当年未上市）的标的不出现在结果里 → 调用方走 synthetic。
    """
    # 窗口前多留 10 个自然日，保证首个交易日的 pct_change 有前一收盘价可算
    pad_start = (pd.to_datetime(start) - pd.Timedelta(10, unit="D")).strftime("%Y-%m-%d")
    cols = []
    for market, syms in symbols_by_market.items():
        try:
            md = load_market_data(market, sorted(syms), start=pad_start, end=end,
                                  data_dir=data_dir)
        except Exception:
            continue
        close = md.close
        for sym in syms:
            if sym not in close.columns:
                continue
            s = close[sym]
            # 窗口内是否有真实数据（上市）
            if s.reindex(master).notna().any():
                cols.append(s.rename(sym))
    if not cols:
        return pd.DataFrame(index=master)
    wide = pd.concat(cols, axis=1)
    return _prep_returns(wide, master)


def _max_drawdown(nav: pd.Series) -> float:
    return float((nav / nav.cummax() - 1.0).min())


def _cum(ret: pd.Series) -> float:
    return float((1.0 + ret).prod() - 1.0)


# ---- 核心：单情景重放 ----

def replay(weights: pd.Series, scenario: str, betas: pd.DataFrame,
           factor_returns: pd.DataFrame,
           real_returns: pd.DataFrame | None = None,
           benchmark_ret: pd.Series | None = None) -> ScenarioResult:
    """把 weights 组合放进 scenario 窗口，返回 ScenarioResult。

    - factor_returns：全历史 date×factor（内部只切 [start, end]，防未来函数）；
    - betas：symbol×factor 因子暴露（复用批次一 exposure.estimate_betas，当前窗口估计）；
    - real_returns：可选，date×symbol 已备好的窗口真实日收益（价格法，首日 0）。
      缺省时全部走 synthetic（供单测手算）；某 symbol 不在其列 → 该腿 synthetic；
    - benchmark_ret：可选，master 日历上的基准日收益（首日 0），仅做对照。
    """
    if scenario not in SCENARIOS:
        raise KeyError(f"未知情景 {scenario!r}，可选：{list(SCENARIOS)}")
    start, end, bench_name, desc = SCENARIOS[scenario]

    fr_all = factor_returns.copy()
    fr_all.index = pd.to_datetime(fr_all.index)
    if pd.to_datetime(start) < fr_all.index[0]:
        raise ValueError(
            f"情景 {scenario} 起始 {start} 早于因子收益起点 "
            f"{fr_all.index[0].date()}（数据不足）")
    fr = fr_all.loc[pd.to_datetime(start):pd.to_datetime(end)]
    if len(fr) < 2:
        raise ValueError(f"情景 {scenario} 窗口内因子收益不足 2 天")
    master = fr.index
    factors = [c for c in fr.columns if c in betas.columns]

    w = weights.astype(float)
    w = w / w.sum()
    real_returns = real_returns if real_returns is not None else pd.DataFrame(index=master)
    if not real_returns.empty:
        real_returns = real_returns.reindex(master)
        real_returns.iloc[0] = 0.0
        real_returns = real_returns.fillna(0.0)

    leg_rets: dict[str, pd.Series] = {}
    modes: dict[str, str] = {}
    for sym in w.index:
        if sym in real_returns.columns:
            leg_rets[sym] = real_returns[sym]
            modes[sym] = "replay"
        else:
            b = betas.reindex([sym], columns=factors).fillna(0.0).iloc[0]
            r = fr[factors].mul(b, axis=1).sum(axis=1)
            r.iloc[0] = 0.0                      # 基准日与 replay 腿一致
            leg_rets[sym] = r
            modes[sym] = "synthetic"

    leg = pd.DataFrame(leg_rets)                 # master × symbol
    port_ret = leg.mul(w.reindex(leg.columns), axis=1).sum(axis=1)
    nav = (1.0 + port_ret).cumprod()

    per_rows = []
    for sym in w.index:
        pr = _cum(leg[sym])
        per_rows.append({
            "symbol": sym, "mode": modes[sym], "weight": float(w[sym]),
            "ret": pr, "contrib": float(w[sym]) * pr,
        })
    per_position = pd.DataFrame(per_rows).set_index("symbol")

    # 系统性因子归因（线性近似，仅参考）：组合暴露 b_f × 窗口累计因子收益
    b_port = portfolio_exposure(betas.reindex(columns=factors).fillna(0.0), w)
    factor_attr = (b_port * fr[factors].sum(axis=0)).rename("factor_attr")

    coverage = float(w[[modes[s] == "replay" for s in w.index]].sum())
    bench_return = _cum(benchmark_ret) if benchmark_ret is not None else None

    return ScenarioResult(
        name=scenario, desc=desc, start=start, end=end, n_days=len(master),
        total_return=_cum(port_ret),
        max_daily_loss=float(port_ret.min()),
        max_daily_loss_date=str(port_ret.idxmin().date()),
        max_drawdown=_max_drawdown(nav),
        benchmark_return=bench_return,
        coverage=coverage,
        per_position=per_position.sort_values("contrib"),
        factor_attr=factor_attr.sort_values(),
        nav=nav,
    )


# ---- 编排：全情景 ----

def run_all(store, portfolio_id: str, betas: pd.DataFrame,
            factor_returns: pd.DataFrame, positions: pd.DataFrame,
            data_dir: str | Path = "data",
            scenarios: list[str] | None = None) -> list[ScenarioResult]:
    """对一个组合跑全部（或指定）情景，返回 ScenarioResult 列表。

    每情景独立加载该窗口的真实行情 + 基准。数据不足的情景（起始早于因子史）跳过并打印。
    """
    weights = positions.set_index("symbol")["weight"]
    symbols_by_market: dict[str, list[str]] = {}
    for _, r in positions.iterrows():
        symbols_by_market.setdefault(r["market"], []).append(r["symbol"])

    names = list(scenarios or SCENARIOS.keys())
    results: list[ScenarioResult] = []
    for name in names:
        start, end, bench_name, _ = SCENARIOS[name]
        fr_all = pd.to_datetime(factor_returns.index)
        if pd.to_datetime(start) < fr_all[0]:
            print(f"[scenario] 跳过 {name}：{start} 早于因子史 {fr_all[0].date()}（数据不足）")
            continue
        master = factor_returns.loc[pd.to_datetime(start):pd.to_datetime(end)].index
        real = load_scenario_returns(symbols_by_market, start, end, master, data_dir)
        bench_ret = None
        try:
            bpx = load_benchmark(bench_name, data_dir=data_dir)
            bench_ret = _prep_returns(bpx.to_frame("b"), master)["b"]
        except Exception:
            pass
        results.append(replay(weights, name, betas, factor_returns,
                              real_returns=real, benchmark_ret=bench_ret))
    return results


def results_to_frame(results: list[ScenarioResult]) -> pd.DataFrame:
    """情景结果 → 汇总表（供报告/CLI 打印）。"""
    return pd.DataFrame([{
        "scenario": r.name, "desc": r.desc, "start": r.start, "end": r.end,
        "n_days": r.n_days, "total_return": r.total_return,
        "max_drawdown": r.max_drawdown, "max_daily_loss": r.max_daily_loss,
        "worst_day": r.max_daily_loss_date, "benchmark": r.benchmark_return,
        "coverage": r.coverage,
    } for r in results]).set_index("scenario")
