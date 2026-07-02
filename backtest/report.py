"""回测报告渲染：markdown + PNG（设计文档 §3.6，接口冻结）。

- markdown：标题、参数/区间摘要、指标表（百分数格式化）、月度收益表（可选）、
  前 10 大交易（按成交额）、extra_sections 逐节追加、PNG 图引用（同目录相对路径）。
- PNG：matplotlib Agg 后端，上下两幅——上幅净值曲线（组合主色 + 基准灰色，带图例）、
  下幅回撤灰色面积图。配色克制；中文字体可能缺失，图内文字一律用英文避免豆腐块。
- 只依赖 pandas/matplotlib/标准库，对 result 做 duck-typing，不 import engine / x_agent。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import matplotlib

matplotlib.use("Agg")  # 必须在 pyplot 之前设定，服务器/无显示环境可用
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import PercentFormatter  # noqa: E402

import pandas as pd  # noqa: E402

if TYPE_CHECKING:  # 仅类型标注，运行时不导入 engine
    from backtest.engine import BacktestResult

# 克制配色：组合一条主色线，基准/回撤用中性灰做背景语境
_COLOR_MAIN = "#4477aa"   # 组合净值主色
_COLOR_GREY = "#999999"   # 基准线 & 回撤面积

# 指标表的展示顺序与格式：(key, 中文标签, 格式类型)
_METRIC_ROWS: list[tuple[str, str, str]] = [
    ("start", "起始日", "date"),
    ("end", "结束日", "date"),
    ("n_days", "交易日数", "int"),
    ("total_return", "总收益", "pct"),
    ("annual_return", "年化收益", "pct"),
    ("annual_vol", "年化波动", "pct"),
    ("sharpe", "夏普比率", "num"),
    ("max_drawdown", "最大回撤", "pct"),
    ("max_drawdown_peak", "回撤峰值日", "date"),
    ("max_drawdown_trough", "回撤谷值日", "date"),
    ("calmar", "卡玛比率", "num"),
    ("win_rate", "日胜率", "pct"),
    ("avg_daily_turnover", "日均换手", "pct"),
    ("n_trades", "成交笔数", "int"),
    ("total_cost", "总交易成本", "money"),
    ("benchmark_total_return", "基准总收益", "pct"),
    ("benchmark_annual_return", "基准年化", "pct"),
    ("excess_annual_return", "年化超额(几何)", "pct"),
]

_MONTHLY_MAX_ROWS = 36  # 月度收益表最多展示的月数，更早的截断


def _fmt(value, kind: str) -> str:
    """按格式类型渲染单个指标值；NaT/None 统一显示 '-'。"""
    if value is None or (isinstance(value, float) and pd.isna(value)) or value is pd.NaT:
        return "-"
    if kind == "pct":
        return f"{value:.2%}"
    if kind == "num":
        return f"{value:.2f}"
    if kind == "int":
        return f"{int(value)}"
    if kind == "money":
        return f"{value:,.2f}"
    if kind == "date":
        ts = pd.Timestamp(value)
        return "-" if pd.isna(ts) else ts.strftime("%Y-%m-%d")
    return str(value)


def _metrics_table(metrics: dict) -> str:
    """指标表：按固定顺序输出，缺失的 key（如无基准）自动跳过。"""
    lines = ["| 指标 | 数值 |", "|---|---|"]
    for key, label, kind in _METRIC_ROWS:
        if key in metrics:
            lines.append(f"| {label} | {_fmt(metrics[key], kind)} |")
    return "\n".join(lines)


def _monthly_table(nav: pd.Series) -> str | None:
    """月度收益表：nav 按月末重采样取尾值算环比；不足两个月返回 None（跳过该节）。"""
    if nav is None or len(nav) < 2:
        return None
    monthly = nav.resample("ME").last()
    if len(monthly) < 2:
        return None
    rets = monthly.pct_change()
    # 首月收益补齐：相对回测起点净值
    rets.iloc[0] = monthly.iloc[0] / nav.iloc[0] - 1.0

    note = ""
    if len(rets) > _MONTHLY_MAX_ROWS:
        note = f"\n\n> 共 {len(rets)} 个月，仅展示最近 {_MONTHLY_MAX_ROWS} 个月。"
        rets = rets.iloc[-_MONTHLY_MAX_ROWS:]

    lines = ["| 月份 | 收益 |", "|---|---|"]
    for ts, r in rets.items():
        lines.append(f"| {ts.strftime('%Y-%m')} | {_fmt(r, 'pct')} |")
    return "\n".join(lines) + note


def _top_trades_table(trades: pd.DataFrame, top: int = 10) -> str:
    """前 N 大交易（按成交额 amount 降序）；无成交时给出说明。"""
    if trades is None or len(trades) == 0 or "amount" not in trades.columns:
        return "（本次回测无成交记录）"
    cols = ["date", "symbol", "side", "shares", "price", "amount", "cost"]
    df = trades.sort_values("amount", ascending=False).head(top)
    lines = ["| 日期 | 标的 | 方向 | 股数 | 价格 | 成交额 | 费用 |", "|---|---|---|---|---|---|---|"]
    for _, row in df.iterrows():
        vals = []
        for c in cols:
            v = row.get(c)
            if c == "date":
                vals.append(_fmt(v, "date"))
            elif c in ("amount", "cost"):
                vals.append(_fmt(v, "money"))
            elif c == "price":
                vals.append("-" if v is None or pd.isna(v) else f"{v:.4g}")
            elif c == "shares":
                vals.append("-" if v is None or pd.isna(v) else f"{v:g}")
            else:
                vals.append("-" if v is None else str(v))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def _plot_png(result: "BacktestResult", png_path: Path) -> None:
    """上下两幅：净值曲线（组合主色 + 基准灰色，图例）/ 回撤灰色面积图。

    图内文字全部用英文（Net Value / Drawdown / Portfolio / Benchmark），
    避免运行环境缺中文字体渲染成豆腐块。
    """
    nav: pd.Series = result.nav
    fig, (ax_nav, ax_dd) = plt.subplots(
        2, 1, figsize=(10, 6.5), sharex=True,
        gridspec_kw={"height_ratios": [2.2, 1.0], "hspace": 0.12},
    )

    # 上幅：净值 vs 基准
    ax_nav.plot(nav.index, nav.values, color=_COLOR_MAIN, lw=1.8, label="Portfolio")
    benchmark = getattr(result, "benchmark", None)
    if benchmark is not None and len(benchmark.dropna()):
        b = benchmark.dropna()
        ax_nav.plot(b.index, b.values, color=_COLOR_GREY, lw=1.4, label="Benchmark")
    ax_nav.set_title("Net Value", fontsize=11, loc="left")
    ax_nav.legend(frameon=False, fontsize=9, loc="best")

    # 下幅：回撤面积（nav/cummax - 1）
    dd = nav / nav.cummax() - 1.0
    ax_dd.fill_between(dd.index, dd.values, 0.0, color=_COLOR_GREY, alpha=0.45, lw=0)
    ax_dd.plot(dd.index, dd.values, color=_COLOR_GREY, lw=0.8)
    ax_dd.set_title("Drawdown", fontsize=11, loc="left")
    ax_dd.yaxis.set_major_formatter(PercentFormatter(xmax=1.0, decimals=0))

    # 网格/边框做背景处理，突出数据本身
    for ax in (ax_nav, ax_dd):
        ax.grid(True, alpha=0.25, lw=0.6)
        ax.spines[["top", "right"]].set_visible(False)
        ax.tick_params(labelsize=9)

    fig.autofmt_xdate()
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def render_report(result: "BacktestResult", metrics: dict, *, out_dir: str | Path = "output/backtest",
                  run_name: str | None = None, plot: bool = True,
                  extra_sections: dict[str, str] | None = None) -> Path:
    """生成 {out_dir}/{run_name}.md（plot=True 时另存同名 .png），返回 md 路径。

    run_name 缺省为 "{result.name}_{当天日期}"；out_dir 不存在时自动创建。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if run_name is None:
        run_name = f"{result.name}_{datetime.now():%Y%m%d}"

    md_path = out_dir / f"{run_name}.md"
    png_name = f"{run_name}.png"

    nav: pd.Series = result.nav
    start = _fmt(metrics.get("start", nav.index[0] if len(nav) else None), "date")
    end = _fmt(metrics.get("end", nav.index[-1] if len(nav) else None), "date")

    parts: list[str] = [f"# 回测报告：{result.name}", ""]

    # 参数 / 区间摘要
    parts += [
        "## 概要",
        "",
        f"- 策略名称：{result.name}",
        f"- 回测区间：{start} ~ {end}（共 {metrics.get('n_days', len(nav))} 个交易日）",
        f"- 初始资金：{_fmt(getattr(result, 'initial_capital', None), 'money')}",
        f"- 基准：{'有' if getattr(result, 'benchmark', None) is not None else '无'}",
        f"- 生成时间：{datetime.now():%Y-%m-%d %H:%M}",
        "",
    ]

    # 指标表
    parts += ["## 绩效指标", "", _metrics_table(metrics), ""]

    # PNG 图引用（同目录相对路径）
    if plot:
        _plot_png(result, out_dir / png_name)
        parts += ["## 净值与回撤", "", f"![Net Value & Drawdown]({png_name})", ""]

    # 月度收益表（可选：不足两个月自动跳过）
    monthly = _monthly_table(nav)
    if monthly is not None:
        parts += ["## 月度收益", "", monthly, ""]

    # 前 10 大交易
    parts += ["## 前 10 大交易（按成交额）", "", _top_trades_table(getattr(result, "trades", None)), ""]

    # 附加小节（如事件研究表），逐节追加
    if extra_sections:
        for title, body in extra_sections.items():
            parts += [f"## {title}", "", body, ""]

    md_path.write_text("\n".join(parts), encoding="utf-8")
    return md_path
