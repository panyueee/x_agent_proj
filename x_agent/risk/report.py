"""组合风险日报编排：持仓 → 因子收益 → beta → 协方差 → 分解 → markdown + 落库。

"收盘冻结 + 计算图缓存"（docs/aladdin/03 §15 / README Top3）：
  - build_factor_returns 的 parquet 缓存 = 因子收益冻结；
  - 每个报告日的 因子协方差 + 持仓 beta/resid 冻结在 output/factors/frozen/<date>/，
    重跑同日报告（或将来白天临时查询）只做矩阵小运算，不再碰全量数据。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from backtest.data import load_benchmark, load_market_data

from .covariance import ewma_cov
from .decompose import RiskReport, decompose
from .exposure import estimate_betas
from .factors import GICS_11, STYLE_FACTORS, build_factor_returns, load_sector_map
from .portfolio import load_positions
from .scenarios import SCENARIOS, results_to_frame, run_all

BENCHMARK = "000300_SS"          # 2021-03 起；更早区间批次二再处理
BETA_LOOKBACK_DAYS = 800         # 加载持仓行情的自然日回看（250 交易日窗口 + 余量）


def _load_holding_returns(positions: pd.DataFrame, end: str,
                          data_dir: str | Path) -> pd.DataFrame:
    """按市场分组加载持仓行情 → date×symbol 日收益宽表（各市场日历取并集）。"""
    start = (pd.to_datetime(end) - pd.Timedelta(days=BETA_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    frames = []
    for market, grp in positions.groupby("market"):
        md = load_market_data(market, sorted(grp["symbol"]), start=start, end=end,
                              data_dir=data_dir)
        ret = md.close.pct_change(fill_method=None).where(md.tradable)
        frames.append(ret)
    calendar = pd.DatetimeIndex(sorted(set().union(*(f.index for f in frames))))
    return pd.concat([f.reindex(calendar) for f in frames], axis=1)


def _frozen_dir(cache_dir: str | Path, date: str) -> Path:
    return Path(cache_dir) / "frozen" / date


def compute_risk(store, portfolio_id: str = "demo", date: str | None = None,
                 data_dir: str | Path = "data",
                 cache_dir: str | Path = "output/factors",
                 db_path: str = "output/x_agent.db",
                 halflife: int = 90, force: bool = False):
    """计算一天一组合的完整风险分解。

    返回 (RiskReport, positions, date_str, per_position: DataFrame)。
    """
    # 持仓：优先取报告日之前的最近快照（历史可回放）；快照晚于报告日
    # （如今天录仓、数据只到上个交易日）时退回最新快照并提示
    try:
        positions = load_positions(store, portfolio_id, asof=date, data_dir=data_dir)
    except ValueError:
        if date is None:
            raise
        positions = load_positions(store, portfolio_id, asof=None, data_dir=data_dir)
        print(f"[riskreport] 注意：{date} 前无持仓快照，使用最新快照 "
              f"{positions['date'].iloc[0]}")
    weights = positions.set_index("symbol")["weight"]

    fr = build_factor_returns("a", data_dir=data_dir, cache_dir=cache_dir,
                              db_path=db_path, force=force)
    if date is None:
        date = fr.index[-1].strftime("%Y-%m-%d")
    fr = fr.loc[:pd.to_datetime(date)]
    if len(fr) < 260:
        raise ValueError(f"截至 {date} 的因子收益不足 260 天，无法估计 beta/协方差")

    frozen = _frozen_dir(cache_dir, date)
    fcov_path = frozen / "fcov.parquet"
    betas_path = frozen / f"betas_{portfolio_id}.parquet"
    resid_path = frozen / f"resid_{portfolio_id}.parquet"

    stock_ret = _load_holding_returns(positions, date, data_dir)

    cache_ok = False
    if not force and fcov_path.exists() and betas_path.exists() and resid_path.exists():
        fcov = pd.read_parquet(fcov_path)
        betas = pd.read_parquet(betas_path)
        resid_vol = pd.read_parquet(resid_path)["resid_vol"]
        # 同日调仓换了标的 → 缓存作废重算（冻结缓存按"标的集合不变"才有效）
        cache_ok = set(weights.index).issubset(set(betas.index))
    if not cache_ok:
        sector_map = load_sector_map(db_path)
        betas, resid_vol = estimate_betas(stock_ret, fr, sector_map=sector_map)
        fcov = ewma_cov(fr, halflife=halflife)
        frozen.mkdir(parents=True, exist_ok=True)
        fcov.to_parquet(fcov_path)
        betas.to_parquet(betas_path)
        resid_vol.to_frame().to_parquet(resid_path)

    # 组合与基准日收益（TE 用）：对齐到持仓收益日历，缺失记 0（跨市场日历差异的简化）
    port_ret = stock_ret.fillna(0.0).mul(weights.reindex(stock_ret.columns).fillna(0.0)).sum(axis=1)
    bench_ret = None
    try:
        bench_px = load_benchmark(BENCHMARK, data_dir=data_dir)
        bench_ret = bench_px.pct_change().reindex(port_ret.index).dropna()
    except FileNotFoundError:
        pass

    report = decompose(weights, betas, fcov, resid_vol,
                       portfolio_ret=port_ret, benchmark_ret=bench_ret)

    per_position = positions.set_index("symbol").copy()
    per_position["beta_mkt"] = betas["mkt"].reindex(per_position.index)
    per_position["resid_vol"] = resid_vol.reindex(per_position.index)
    per_position["risk_pct"] = report.stock_ccr.reindex(per_position.index)
    return report, positions, date, per_position


def _fmt_pct(x, digits: int = 2) -> str:
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:.{digits}f}%"


def _render_markdown(report: RiskReport, positions: pd.DataFrame, date: str,
                     portfolio_id: str, per_position: pd.DataFrame,
                     names: dict[str, str]) -> str:
    lines = [
        f"# 组合风险日报 — {portfolio_id} @ {date}",
        "",
        "> 方法：4 风格因子（mkt/size/mom/vol，A 股全市场多空构造）+ 11 GICS 行业超额因子；",
        "> 250 日 OLS 暴露回归 + EWMA(半衰期90日) 因子协方差；口径细节见 docs/aladdin/05 §3。",
        "",
        "## 核心指标",
        "",
        "| 指标 | 数值 |",
        "| --- | ---: |",
        f"| 组合年化波动 σ_p | **{_fmt_pct(report.vol_ann)}** |",
        f"| 1 日 99% VaR（参数化） | {_fmt_pct(report.var99_1d)} |",
        f"| 因子波动 / 特质波动 | {_fmt_pct(report.factor_vol)} / {_fmt_pct(report.specific_vol)} |",
        f"| 特质方差占比 | {_fmt_pct(report.specific_share, 1)} |",
        f"| 对 {BENCHMARK} 跟踪误差（250日实证） | {_fmt_pct(report.te_ann)} |",
        "",
        "## 因子暴露",
        "",
        "| 因子 | 暴露 β | 风险贡献占比 |",
        "| --- | ---: | ---: |",
    ]
    style_rows = [(f, report.exposures.get(f, 0.0), report.ccr.get(f, 0.0))
                  for f in STYLE_FACTORS]
    industry_rows = [(f, report.exposures.get(f, 0.0), report.ccr.get(f, 0.0))
                     for f in GICS_11 if abs(report.exposures.get(f, 0.0)) > 1e-9]
    for f, b, c in style_rows + industry_rows:
        lines.append(f"| {f} | {b:+.3f} | {_fmt_pct(c, 1)} |")
    lines += [
        f"| （特质） | — | {_fmt_pct(report.specific_share, 1)} |",
        "",
        "## 风险贡献 Top 因子",
        "",
    ]
    top_f = report.ccr.abs().sort_values(ascending=False).head(5).index
    for f in top_f:
        lines.append(f"- **{f}**：贡献 {_fmt_pct(report.ccr[f], 1)}（暴露 {report.exposures[f]:+.3f}）")
    lines += ["", "## 持仓与个股风险贡献", "",
              "| 标的 | 名称 | 市场 | 权重 | β_mkt | 特质波动 | 风险贡献 |",
              "| --- | --- | --- | ---: | ---: | ---: | ---: |"]
    pp = per_position.sort_values("risk_pct", ascending=False)
    for sym, r in pp.iterrows():
        nm = names.get(sym, "")
        lines.append(
            f"| {sym} | {nm} | {r['market']} | {_fmt_pct(r['weight'], 1)} "
            f"| {r['beta_mkt']:+.2f} | {_fmt_pct(r['resid_vol'], 1)} "
            f"| {_fmt_pct(r['risk_pct'], 1)} |")
    lines += [
        "",
        "## 口径与局限（v1）",
        "",
        "- 因子基于 A 股全市场构造；美股/加密持仓的暴露是对同一组因子的回归近似，仅取交易日重叠部分；",
        "- Size 用 60 日均成交额代理（fundamentals 历史市值积累后切换）；行业分类用当前值近似历史；",
        "- VaR 为参数化 2.33σ，非历史模拟（批次二危机重放补历史视角）；",
        "- 跨市场日历未对齐日按收益 0 处理，TE 对含加密组合略有低估；",
        "- 持仓中的 ETF 按普通标的回归，穿透留提案 5。",
        "",
    ]
    return "\n".join(lines)


def _prepare_betas(store, portfolio_id: str, date: str | None,
                   data_dir: str | Path, cache_dir: str | Path, db_path: str):
    """加载持仓 + 全历史因子收益 + 当前窗口 beta（复用批次一 exposure）。

    返回 (positions, betas, factor_returns, names)。情景重放的公共前置。
    """
    positions = load_positions(store, portfolio_id, asof=date, data_dir=data_dir)
    fr = build_factor_returns("a", data_dir=data_dir, cache_dir=cache_dir, db_path=db_path)
    beta_end = (date or fr.index[-1].strftime("%Y-%m-%d"))
    stock_ret = _load_holding_returns(positions, beta_end, data_dir)
    sector_map = load_sector_map(db_path)
    betas, _ = estimate_betas(stock_ret, fr.loc[:pd.to_datetime(beta_end)],
                              sector_map=sector_map)
    names: dict[str, str] = {}
    for sym in positions["symbol"]:
        sec = store.get_security(sym)
        if sec and sec.get("name"):
            names[sym] = sec["name"]
    return positions, betas, fr, names


def _fmt_signed_pct(x, digits: int = 1) -> str:
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:+.{digits}f}%"


def _render_scenarios_markdown(results, positions, portfolio_id: str,
                               names: dict[str, str]) -> str:
    summary = results_to_frame(results)
    lines = [
        f"# 历史危机场景重放 — {portfolio_id}",
        "",
        "> 个人版压力测试（批次二 / 提案 2）：把当前组合整体放进历史危机窗口，"
        "权重固定不再平衡，逐日复利。",
        "> 方法与局限见 `x_agent/risk/scenarios.py` 头注 / docs/aladdin/05 §4。"
        f" 演示组合：{portfolio_id}（{len(positions)} 只跨市场持仓，单个 demo，示意性质）。",
        "",
        "## 情景对照总表",
        "",
        "| 情景 | 窗口 | 交易日 | 组合总损益 | 最大回撤 | 最惨单日 | 基准 | 真实重放覆盖 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, r in summary.iterrows():
        lines.append(
            f"| **{name}** {r['desc']} | {r['start']}~{r['end']} | {int(r['n_days'])} "
            f"| **{_fmt_signed_pct(r['total_return'])}** | {_fmt_signed_pct(r['max_drawdown'])} "
            f"| {_fmt_signed_pct(r['max_daily_loss'])}（{r['worst_day']}） "
            f"| {_fmt_signed_pct(r['benchmark'])} | {r['coverage'] * 100:.0f}% |")

    if len(summary):
        worst = summary["total_return"].idxmin()
        wr = summary.loc[worst]
        lines += [
            "",
            f"**最伤情景：{worst}** —— {wr['desc']}，组合总损益 "
            f"{_fmt_signed_pct(wr['total_return'])}，最大回撤 {_fmt_signed_pct(wr['max_drawdown'])}。",
            "",
        ]

    lines += ["## 各情景明细", ""]
    for r in results:
        lines += [
            f"### {r.name} — {r.desc}",
            "",
            f"窗口 {r.start} ~ {r.end}（{r.n_days} 交易日）；组合总损益 "
            f"**{_fmt_signed_pct(r.total_return)}**，最大回撤 {_fmt_signed_pct(r.max_drawdown)}，"
            f"最惨单日 {_fmt_signed_pct(r.max_daily_loss)}（{r.max_daily_loss_date}）；"
            f"基准 {_fmt_signed_pct(r.benchmark_return)}；真实重放覆盖 {r.coverage * 100:.0f}%。",
            "",
            "逐持仓损益贡献（`*`=synthetic β 合成，无真实历史，保守低估）：",
            "",
            "| 标的 | 名称 | 模式 | 权重 | 情景收益 | 对组合贡献 |",
            "| --- | --- | --- | ---: | ---: | ---: |",
        ]
        for sym, row in r.per_position.iterrows():
            star = "*" if row["mode"] == "synthetic" else ""
            lines.append(
                f"| {sym}{star} | {names.get(sym, '')} | {row['mode']} "
                f"| {row['weight'] * 100:.0f}% | {_fmt_signed_pct(row['ret'])} "
                f"| {_fmt_signed_pct(row['contrib'])} |")
        losers = r.factor_attr[r.factor_attr < 0].head(3)
        if len(losers):
            attr = "、".join(f"{f}（{_fmt_signed_pct(v)}）" for f, v in losers.items())
            lines += ["", f"系统性因子主因（线性近似，仅参考）：{attr}。", ""]
        else:
            lines.append("")

    lines += [
        "## 口径与局限",
        "",
        "- 权重固定不再平衡（Aladdin 即时压力测试同款简化）；起始日为基准日（NAV=1）。",
        "- replay 腿：真实收盘价按 A 股窗口日历 ffill 后 pct_change，跨市场休市日涨跌并入下一 A 交易日。",
        "- synthetic 腿（`*`）：ret=β·因子收益，忽略特质波动 → 系统性低估极端损失；coverage 为真实重放权重占比。",
        "- 窗口中途上市的标的上市前记平收益（如 sz.300750 在 2018 窗口中段 IPO）。",
        "- 因子归因为纯系统性线性近似，不与组合总损益对账；逐持仓贡献才是主口径。",
        f"- 单个 demo 组合（{portfolio_id}），示意性质；β 用当前窗口估计（"
        "'当前组合遭遇历史危机'的假设，非历史时点持仓）。",
        "",
    ]
    return "\n".join(lines)


def render_scenario_report(store, portfolio_id: str = "demo", date: str | None = None,
                           scenario: str = "all",
                           data_dir: str | Path = "data",
                           cache_dir: str | Path = "output/factors",
                           db_path: str = "output/x_agent.db",
                           out_dir: str | Path = "output/risk") -> Path:
    """端到端历史危机重放：持仓 → beta → 全情景 → markdown（output/risk/scenarios_<pf>.md）。"""
    positions, betas, fr, names = _prepare_betas(
        store, portfolio_id, date, data_dir, cache_dir, db_path)
    scen_list = None if scenario == "all" else [scenario]
    if scen_list and scenario not in SCENARIOS:
        raise KeyError(f"未知情景 {scenario!r}，可选 all / {list(SCENARIOS)}")
    results = run_all(store, portfolio_id, betas, fr, positions,
                      data_dir=data_dir, scenarios=scen_list)
    if not results:
        raise ValueError("没有可跑的情景（都因数据不足被跳过）")
    md = _render_scenarios_markdown(results, positions, portfolio_id, names)
    from x_agent.report_qa import provenance_footer, qa_and_warn
    md += provenance_footer("本项目风险引擎 x_agent.risk（因子路径 factor_returns_a.parquet）",
                            disclaimer=False)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"scenarios_{portfolio_id}.md"
    out_path.write_text(md, encoding="utf-8")
    qa_and_warn(md, "scenario", portfolio_id)
    return out_path


def render_risk_report(store, portfolio_id: str = "demo", date: str | None = None,
                       data_dir: str | Path = "data",
                       cache_dir: str | Path = "output/factors",
                       db_path: str = "output/x_agent.db",
                       out_dir: str | Path = "output/risk",
                       force: bool = False) -> Path:
    """端到端：计算 → 渲染 markdown → 写 output/risk/<date>_<portfolio>.md → 落 risk_snapshots。

    同时刷新 output/risk_report.md（最新一份的固定路径，digest/人看都方便）。
    """
    report, positions, date, per_position = compute_risk(
        store, portfolio_id, date=date, data_dir=data_dir,
        cache_dir=cache_dir, db_path=db_path, force=force)

    # 名称：securities 主表优先
    names: dict[str, str] = {}
    for sym in positions["symbol"]:
        sec = store.get_security(sym)
        if sec and sec.get("name"):
            names[sym] = sec["name"]

    md = _render_markdown(report, positions, date, portfolio_id, per_position, names)
    from x_agent.report_qa import provenance_footer, qa_and_warn
    md += provenance_footer(f"本项目风险引擎 x_agent.risk（快照 {date}）", disclaimer=False)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{date}_{portfolio_id}.md"
    out_path.write_text(md, encoding="utf-8")
    # 固定路径的"最新报告"副本
    Path(out_dir).parent.joinpath("risk_report.md").write_text(md, encoding="utf-8")
    qa_and_warn(md, "risk", portfolio_id)

    top10 = report.stock_ccr.sort_values(ascending=False).head(10)
    store.save_risk_snapshot({
        "portfolio_id": portfolio_id,
        "date": date,
        "vol_ann": report.vol_ann,
        "var99_1d": report.var99_1d,
        "te_ann": report.te_ann,
        "factor_vol": report.factor_vol,
        "specific_vol": report.specific_vol,
        "exposures": {k: round(float(v), 6) for k, v in report.exposures.items()},
        "risk_contrib": {k: round(float(v), 6) for k, v in report.ccr.items()},
        "stock_contrib": [
            {"symbol": s, "name": names.get(s, ""), "pct": round(float(v), 6)}
            for s, v in top10.items()
        ],
    })
    return out_path
