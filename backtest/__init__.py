# backtest — 自研日频组合回测包（与 x_agent 完全解耦）
# 架构与接口契约见 docs/backtest_design.md；改接口先改文档。

from backtest.data import MarketData, load_market_data, load_benchmark, limit_rate, list_symbols
from backtest.engine import CostModel, BacktestResult, run_backtest
from backtest.metrics import compute_metrics, annual_factor, max_drawdown, annualized_return, sharpe
from backtest.report import render_report
from backtest.strategy import (
    Strategy, MACrossStrategy, MomentumTopN, SignalEventStrategy, STRATEGIES,
)
from backtest.event_study import map_ticker, load_signal_events, event_study

__all__ = [
    "MarketData", "load_market_data", "load_benchmark", "limit_rate", "list_symbols",
    "CostModel", "BacktestResult", "run_backtest",
    "compute_metrics", "annual_factor", "max_drawdown", "annualized_return", "sharpe",
    "render_report",
    "Strategy", "MACrossStrategy", "MomentumTopN", "SignalEventStrategy", "STRATEGIES",
    "map_ticker", "load_signal_events", "event_study",
]
