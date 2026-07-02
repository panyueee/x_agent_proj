# -*- coding: utf-8 -*-
"""回测 CLI 入口（docs/backtest_design.md §3.7）。

用法示例：
    .venv/bin/python scripts/run_backtest.py --strategy ma_cross --market a \
        --symbols sz.000890 --start 2018-01-01 --fast 20 --slow 60
    .venv/bin/python scripts/run_backtest.py --strategy momentum --market crypto \
        --symbols all --lookback 20 --top-n 5
    .venv/bin/python scripts/run_backtest.py --strategy signal_event --market crypto \
        --hold-days 5 --min-score 3

流程：构造策略 → load_market_data → generate_weights → run_backtest
      → compute_metrics → render_report，关键指标打印到 stdout。
signal_event 策略额外跑 event_study，结果表附加进报告。
"""
from __future__ import annotations

import argparse
import os
import sys

# 项目根加入 sys.path，保证 backtest 包可导入（与 scripts/ 下其他脚本一致）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from backtest.data import load_market_data, load_benchmark, list_symbols
from backtest.engine import run_backtest
from backtest.metrics import compute_metrics
from backtest.report import render_report
from backtest.strategy import STRATEGIES, MACrossStrategy, MomentumTopN, SignalEventStrategy
from backtest.event_study import event_study

# 各市场的缺省基准；未列出的市场不设基准
DEFAULT_BENCHMARK = {"a": "000300_SS", "crypto": "BTC-USD", "us": "GSPC"}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="日频组合回测 CLI（backtest 包）")
    p.add_argument("--strategy", required=True, choices=sorted(STRATEGIES),
                   help="策略名：ma_cross / momentum / signal_event")
    p.add_argument("--market", default="a",
                   help="市场：a / us / hk / crypto / etf 等（默认 a）")
    p.add_argument("--symbols", nargs="+", default=None,
                   help="标的列表（空格分隔）；填 all 表示该市场全池（list_symbols）")
    p.add_argument("--start", default=None, help="回测起始日 YYYY-MM-DD")
    p.add_argument("--end", default=None, help="回测结束日 YYYY-MM-DD")
    p.add_argument("--benchmark", default=None,
                   help="基准标的名（缺省：a→000300_SS, crypto→BTC-USD, us→GSPC；填 none 不设）")
    p.add_argument("--trade-at", default="open", choices=["open", "close"],
                   help="T+1 日成交价：open 或 close（默认 open）")
    p.add_argument("--capital", type=float, default=1_000_000.0,
                   help="初始资金（默认 1000000）")
    p.add_argument("--run-name", default=None, help="报告名（默认由策略与参数生成）")
    p.add_argument("--no-plot", action="store_true", help="不生成净值/回撤图")
    # ma_cross 参数
    p.add_argument("--fast", type=int, default=20, help="ma_cross：快均线窗口（默认 20）")
    p.add_argument("--slow", type=int, default=60, help="ma_cross：慢均线窗口（默认 60）")
    # momentum 参数
    p.add_argument("--lookback", type=int, default=20, help="momentum：动量回看天数（默认 20）")
    p.add_argument("--top-n", type=int, default=5, help="momentum：持仓数 Top-N（默认 5）")
    p.add_argument("--rebalance-days", type=int, default=5,
                   help="momentum：调仓间隔（交易日，默认 5）")
    # signal_event 参数
    p.add_argument("--hold-days", type=int, default=5,
                   help="signal_event：信号日起持有的交易日数（默认 5）")
    p.add_argument("--min-score", type=int, default=3,
                   help="signal_event：最低信号分（默认 3）")
    p.add_argument("--db", default="output/x_agent.db",
                   help="signal_event：signals 库路径（只读，默认 output/x_agent.db）")
    return p.parse_args()


def resolve_symbols(args: argparse.Namespace) -> list[str]:
    """解析 --symbols：支持空格分隔多个、逗号分隔、以及 all 全池。"""
    if not args.symbols:
        return []
    syms: list[str] = []
    for token in args.symbols:
        syms.extend(s for s in token.split(",") if s)
    if len(syms) == 1 and syms[0].lower() == "all":
        return list_symbols(args.market)
    return syms


def build_strategy(args: argparse.Namespace):
    """按 CLI 参数构造策略实例。"""
    syms = resolve_symbols(args)
    if args.strategy == "ma_cross":
        if len(syms) != 1:
            raise SystemExit("ma_cross 需要且仅需要一个 --symbols 标的")
        return MACrossStrategy(syms[0], market=args.market,
                               fast=args.fast, slow=args.slow)
    if args.strategy == "momentum":
        if not syms:
            raise SystemExit("momentum 需要 --symbols（标的列表或 all）")
        return MomentumTopN(syms, market=args.market, lookback=args.lookback,
                            top_n=args.top_n, rebalance_days=args.rebalance_days)
    if args.strategy == "signal_event":
        return SignalEventStrategy(db_path=args.db, market=args.market,
                                   hold_days=args.hold_days, min_score=args.min_score)
    raise SystemExit(f"未知策略: {args.strategy}")


def df_to_markdown(df: pd.DataFrame, float_fmt: str = "{:.4f}") -> str:
    """把 DataFrame 渲染成 markdown 表格（不依赖 tabulate）。"""
    idx_name = df.index.name or ""
    header = [str(idx_name)] + [str(c) for c in df.columns]
    lines = ["| " + " | ".join(header) + " |",
             "|" + "|".join(["---"] * len(header)) + "|"]
    for i in range(len(df)):
        cells = [str(df.index[i])]
        for c in df.columns:  # 按列取值，保留整数列 dtype（iterrows 会整行升成 float）
            v = df[c].iloc[i]
            if isinstance(v, float):
                cells.append("nan" if pd.isna(v) else float_fmt.format(v))
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    strategy = build_strategy(args)

    # 1) 加载行情
    symbols = strategy.symbols()
    if args.strategy == "signal_event":
        # signals 里的 $XXX 一律按加密映射为 XXX-USD，其中不少是美股 cashtag，
        # 本地无行情文件 → 在 CLI 层剔除（冻结接口 map_ticker/load_signal_events 不动）
        available = set(list_symbols(args.market))
        missing = sorted(s for s in symbols if s not in available)
        if missing:
            print(f"警告: {len(missing)} 个信号标的无本地行情，已剔除（如 {', '.join(missing[:5])} ...）")
            strategy.events = strategy.events[
                strategy.events["symbol"].isin(available)].reset_index(drop=True)
            symbols = strategy.symbols()
    if not symbols:
        raise SystemExit("标的池为空（signal_event 可能是筛选后无事件），无法回测")
    if args.strategy == "signal_event" and args.start is None and len(strategy.events):
        # 未显式指定起点时收窄到最早事件前一周，避免加载十几年全历史导致
        # 净值长期平坦、年化/夏普被稀释失真
        args.start = (strategy.events["date"].min() - pd.Timedelta("7D")).strftime("%Y-%m-%d")
        print(f"提示: signal_event 未指定 --start，自动收窄到最早事件前一周: {args.start}")
    data = load_market_data(args.market, symbols, start=args.start, end=args.end)

    # 2) 基准（none 表示显式不设）
    bench_name = args.benchmark or DEFAULT_BENCHMARK.get(args.market)
    benchmark = None
    if bench_name and bench_name.lower() != "none":
        benchmark = load_benchmark(bench_name, start=args.start, end=args.end)

    # 3) 生成权重 → 回测（引擎内部统一 shift(1)，策略不 shift）
    weights = strategy.generate_weights(data)
    run_name = args.run_name or f"{args.strategy}_{args.market}"
    result = run_backtest(data, weights, initial_capital=args.capital,
                          trade_at=args.trade_at, benchmark=benchmark, name=run_name)

    # 4) 指标
    metrics = compute_metrics(result)

    # 5) signal_event 额外跑事件研究，塞进报告附加段落
    extra_sections = None
    if args.strategy == "signal_event":
        es = event_study(strategy.events, data, benchmark=benchmark)
        extra_sections = {"信号事件研究": df_to_markdown(es)}

    # 6) 报告
    md_path = render_report(result, metrics, run_name=run_name,
                            plot=not args.no_plot, extra_sections=extra_sections)

    # 7) 关键指标打印到 stdout
    print(f"策略        : {args.strategy} ({args.market})")
    print(f"区间        : {metrics.get('start')} ~ {metrics.get('end')}  ({metrics.get('n_days')} 天)")
    print(f"总收益      : {metrics.get('total_return', float('nan')):.2%}")
    print(f"年化收益    : {metrics.get('annual_return', float('nan')):.2%}")
    print(f"最大回撤    : {metrics.get('max_drawdown', float('nan')):.2%}")
    print(f"夏普比率    : {metrics.get('sharpe', float('nan')):.2f}")
    if "excess_annual_return" in metrics:
        print(f"年化超额    : {metrics['excess_annual_return']:.2%}")
    print(f"报告路径    : {md_path}")


if __name__ == "__main__":
    main()
