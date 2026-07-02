# -*- coding: utf-8 -*-
"""策略层：Strategy 基类 + 三个示例策略。

接口冻结于 docs/backtest_design.md §3.4，禁止 import x_agent。

核心纪律（防未来函数）：generate_weights 返回的权重矩阵中，行 t 只准用
t 及以前的数据（rolling / shift 自然满足；禁止 center=True、禁止全样本统计）。
引擎会统一执行 weights.shift(1) 在 t+1 日成交——策略内部不要再 shift。
"""
from __future__ import annotations

import abc
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from .event_study import load_signal_events

if TYPE_CHECKING:  # 仅类型标注用，避免运行期依赖 data.py
    from .data import MarketData


class Strategy(abc.ABC):
    """策略基类：声明标的池 + 产出 date×symbol 目标权重矩阵。"""

    name: str = "strategy"
    market: str = "a"

    def symbols(self) -> list[str]:
        """需要加载的标的列表（子类覆盖）。"""
        return []

    @abc.abstractmethod
    def generate_weights(self, data: "MarketData") -> pd.DataFrame:
        """返回 date×symbol 目标权重；行 t 只准用 t 及以前的数据（引擎负责 shift）。"""
        ...


class MACrossStrategy(Strategy):
    """双均线择时（单标的）：fast 均线 > slow 均线满仓，否则空仓。"""

    name = "ma_cross"

    def __init__(self, symbol: str, market: str = "a", fast: int = 20, slow: int = 60):
        if fast >= slow:
            raise ValueError(f"fast({fast}) 必须小于 slow({slow})")
        self.symbol = symbol
        self.market = market
        self.fast = fast
        self.slow = slow

    def symbols(self) -> list[str]:
        return [self.symbol]

    def generate_weights(self, data: "MarketData") -> pd.DataFrame:
        close = data.close[self.symbol]
        # rolling 均线只用 t 及以前数据；窗口不足时为 NaN → 比较为 False → 权重 0
        fast_ma = close.rolling(self.fast).mean()
        slow_ma = close.rolling(self.slow).mean()
        w = (fast_ma > slow_ma).astype(float)
        weights = pd.DataFrame(0.0, index=data.close.index, columns=data.close.columns)
        weights[self.symbol] = w
        return weights


class MomentumTopN(Strategy):
    """动量轮动：lookback 收益排序取 Top-N 等权，每 rebalance_days（按行号取模）调一次。"""

    name = "momentum"

    def __init__(self, symbols: list[str], market: str = "crypto",
                 lookback: int = 20, top_n: int = 5, rebalance_days: int = 5):
        if not symbols:
            raise ValueError("symbols 不能为空")
        if lookback < 1 or top_n < 1 or rebalance_days < 1:
            raise ValueError("lookback/top_n/rebalance_days 必须 >= 1")
        self._symbols = list(symbols)
        self.market = market
        self.lookback = lookback
        self.top_n = top_n
        self.rebalance_days = rebalance_days

    def symbols(self) -> list[str]:
        return list(self._symbols)

    def generate_weights(self, data: "MarketData") -> pd.DataFrame:
        close = data.close
        # 行 t 的动量 = close[t]/close[t-lookback] - 1，只依赖历史；
        # 上市不足 lookback 的标的此处为 NaN → 不入选
        mom = close / close.shift(self.lookback) - 1.0

        n_days, n_syms = close.shape
        w = np.zeros((n_days, n_syms))
        current = np.zeros(n_syms)  # 当前持有权重（非调仓日保持不变）
        col_pos = {c: i for i, c in enumerate(close.columns)}

        for i in range(n_days):
            if i % self.rebalance_days == 0:  # 调仓日：重算 Top-N 等权
                row = mom.iloc[i].dropna()
                current = np.zeros(n_syms)
                if len(row) > 0:  # 全 NaN 日（信号还算不出来）→ 全 0
                    top = row.nlargest(min(self.top_n, len(row)))
                    for sym in top.index:
                        current[col_pos[sym]] = 1.0 / len(top)
            w[i] = current

        return pd.DataFrame(w, index=close.index, columns=close.columns)


class SignalEventStrategy(Strategy):
    """signals 事件策略：信号日起持有 hold_days 个交易日，多信号重叠时并集内等权。"""

    name = "signal_event"

    def __init__(self, db_path: str = "output/x_agent.db", market: str = "crypto",
                 hold_days: int = 5, min_score: int = 3):
        if hold_days < 1:
            raise ValueError("hold_days 必须 >= 1")
        self.db_path = db_path
        self.market = market
        self.hold_days = hold_days
        self.min_score = min_score
        # 构造时一次性读事件（只读连接），symbols()/generate_weights 共用
        self.events = load_signal_events(db_path, market=market, min_score=min_score)

    def symbols(self) -> list[str]:
        return sorted(self.events["symbol"].unique().tolist())

    def generate_weights(self, data: "MarketData") -> pd.DataFrame:
        cal = data.calendar
        cols = data.close.columns
        active = np.zeros((len(cal), len(cols)), dtype=bool)
        col_pos = {c: i for i, c in enumerate(cols)}

        if len(self.events) > 0:
            # 事件日归一到日历：信号日当日或其后第一个交易日；越界丢弃
            pos_arr = cal.searchsorted(pd.DatetimeIndex(self.events["date"]))
            for p, sym in zip(pos_arr, self.events["symbol"]):
                if p >= len(cal) or sym not in col_pos:
                    continue
                # 信号日 t 起持有 hold_days 个交易日：行 t..t+hold_days-1 有效
                # （引擎再 shift(1)，实际持仓落在 t+1..t+hold_days）
                active[p:p + self.hold_days, col_pos[sym]] = True

        counts = active.sum(axis=1)
        w = np.zeros(active.shape)
        held = counts > 0
        # 并集内等权，权重和 = 1；池子空的日期全 0
        w[held] = active[held] / counts[held, None]
        return pd.DataFrame(w, index=cal, columns=cols)


STRATEGIES: dict[str, type[Strategy]] = {
    "ma_cross": MACrossStrategy,
    "momentum": MomentumTopN,
    "signal_event": SignalEventStrategy,
}
