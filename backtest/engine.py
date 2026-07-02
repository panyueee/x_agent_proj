# -*- coding: utf-8 -*-
"""回测引擎：CostModel + BacktestResult + run_backtest。

设计依据 docs/backtest_design.md §3.2（接口冻结，签名逐字对齐）。

核心约定（防未来函数）：
    weights 行 t 表示"基于 t 日收盘可得信息计算的目标权重"，
    引擎内部统一执行 weights.shift(1)，在 t+1 日以 trade_at 价格成交。
    策略永远不需要也不允许自己 shift。

对 MarketData 采用 duck-typing（直接访问 .calendar/.open/.close/.tradable/
.limit_up/.limit_down/.open_limit_up/.open_limit_down 属性），
运行时不 import backtest.data，保证两模块可并行开发、单独测试。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:  # 仅供类型检查，运行时不导入
    from backtest.data import MarketData

__all__ = ["CostModel", "BacktestResult", "run_backtest"]

# trades DataFrame 的列名（冻结，逐字对齐设计文档）
_TRADE_COLUMNS = ["date", "symbol", "side", "shares", "price", "amount", "cost"]


@dataclass
class CostModel:
    """交易成本模型（费率均为小数，如 0.00025 = 万 2.5）。"""

    commission_rate: float = 0.00025   # 双边佣金
    min_commission: float = 5.0        # 单笔最低佣金
    stamp_tax_rate: float = 0.001      # 印花税，仅卖出
    slippage_rate: float = 0.001       # 滑点：买入抬价、卖出压价（成交价按比例调整）
    lot_size: int = 100                # 一手股数；0 表示允许小数股

    @classmethod
    def for_market(cls, market: str) -> "CostModel":
        """按市场返回默认费用参数（见设计文档 §3.2 费用参数表）。

        | market | 佣金   | 最低佣金 | 印花税(卖) | 滑点   | lot |
        | a      | 万 2.5 | ¥5      | 千 1      | 千 1   | 100 |
        | hk     | 万 2.5 | 5       | 千 1(近似) | 千 1   | 100 |
        | us     | 万 0.5 | 0       | 0         | 万 5   | 1   |
        | crypto | 千 1   | 0       | 0         | 万 5   | 0   |
        | 其他    | 同 us |
        """
        if market == "a":
            return cls(commission_rate=0.00025, min_commission=5.0,
                       stamp_tax_rate=0.001, slippage_rate=0.001, lot_size=100)
        if market == "hk":
            return cls(commission_rate=0.00025, min_commission=5.0,
                       stamp_tax_rate=0.001, slippage_rate=0.001, lot_size=100)
        if market == "crypto":
            return cls(commission_rate=0.001, min_commission=0.0,
                       stamp_tax_rate=0.0, slippage_rate=0.0005, lot_size=0)
        # us 及其他市场：万 0.5 佣金、无最低、无印花税、万 5 滑点、单股为 1 手
        return cls(commission_rate=0.00005, min_commission=0.0,
                   stamp_tax_rate=0.0, slippage_rate=0.0005, lot_size=1)

    def buy_cost(self, amount: float) -> float:
        """买入费用 = max(amount*commission_rate, min_commission)。"""
        return max(amount * self.commission_rate, self.min_commission)

    def sell_cost(self, amount: float) -> float:
        """卖出费用 = 佣金(含最低) + amount*stamp_tax_rate。"""
        return max(amount * self.commission_rate, self.min_commission) + amount * self.stamp_tax_rate


@dataclass
class BacktestResult:
    """回测结果容器（字段冻结，逐字对齐设计文档）。"""

    name: str
    nav: pd.Series                # 逐日净值，起点 1.0（按日终 close 估值）
    returns: pd.Series            # nav.pct_change().fillna(0)
    positions: pd.DataFrame       # date×symbol 日终持仓市值
    weights: pd.DataFrame         # date×symbol 日终实际权重
    cash: pd.Series
    turnover: pd.Series           # 当日成交额合计 / 当日组合市值
    trades: pd.DataFrame          # columns: date, symbol, side("buy"/"sell"), shares, price, amount, cost
    benchmark: pd.Series | None   # 对齐到 nav.index 并归一到 1.0 的基准净值
    initial_capital: float = field(default=1_000_000.0)


def _prepare_weights(weights: pd.DataFrame, calendar: pd.DatetimeIndex,
                     columns: pd.Index) -> pd.DataFrame:
    """校验并对齐目标权重矩阵。

    - 索引统一转为 DatetimeIndex；
    - 负权重、行和 > 1+1e-6 → ValueError（校验在 reindex 之前做，
      这样即便某列不在行情数据里也能拦住明显错误的输入）；
    - reindex 到 calendar×columns，未覆盖的日期/标的按 0（不持仓）处理。
    """
    w = weights.copy()
    if not isinstance(w.index, pd.DatetimeIndex):
        w.index = pd.to_datetime(w.index)
    w = w.astype(float).fillna(0.0)

    if (w.to_numpy() < -1e-12).any():
        raise ValueError("weights 出现负值：本引擎只支持多头目标权重（0 <= w）")
    row_sum = w.sum(axis=1)
    bad = row_sum[row_sum > 1 + 1e-6]
    if len(bad) > 0:
        raise ValueError(
            f"weights 行和超过 1（首个违例 {bad.index[0].date()}: {bad.iloc[0]:.6f}）；"
            "行和 <1 视为留现金，>1 不允许"
        )

    # 对齐到交易日历与行情标的全集；NaN→0 表示"不持有"
    return w.reindex(index=calendar, columns=columns).fillna(0.0)


def run_backtest(data: MarketData, weights: pd.DataFrame, *,
                 initial_capital: float = 1_000_000.0,
                 cost_model: CostModel | None = None,     # None → CostModel.for_market(data.market)
                 trade_at: str = "open",                  # "open" | "close"：T+1 日的成交价
                 benchmark: pd.Series | None = None,
                 name: str = "backtest") -> BacktestResult:
    """目标权重驱动的 T+1 日频回测。

    逐日会计流程（对 calendar 每日 t，见设计文档 §3.2 七步）：
      1. target = shifted_weights.loc[t]（引擎统一 shift(1) 防未来函数）
      2. 成交价 px = open[t] 或 close[t]；估值 V = cash + Σ shares×px（NaN 用 ffill close）
      3. 目标股数 = floor(target*V / (px*(1+slippage)) / lot) * lot；target=0 清仓含零股
      4. 先卖后买；买单现金不足按比例缩减
      5. 撮合限制：停牌跳过；买遇涨停/卖遇跌停跳过（保留持仓，不排队）
      6. 成交：买入现金减 amount+buy_cost，卖出现金加 amount-sell_cost（amount 按含滑点价计）
      7. 日终以 close[t]（ffill 价）估值记 nav/positions/weights/turnover
    """
    if trade_at not in ("open", "close"):
        raise ValueError(f'trade_at 只能为 "open" 或 "close"，收到 {trade_at!r}')
    if cost_model is None:
        cost_model = CostModel.for_market(data.market)

    calendar: pd.DatetimeIndex = data.calendar
    symbols = data.close.columns
    n_days, n_sym = len(calendar), len(symbols)

    # ---- 权重对齐 + 引擎统一 shift(1)：t 日信号 → t+1 日成交 ----
    w = _prepare_weights(weights, calendar, symbols)
    shifted = w.shift(1).fillna(0.0)

    # ---- 预取 numpy 矩阵（symbol 维度向量化，外层日循环用 python for）----
    trade_px_mat = (data.open if trade_at == "open" else data.close).to_numpy(dtype=float)
    close_fill_mat = data.close.ffill().to_numpy(dtype=float)   # 日终/兜底估值价（数据层已 ffill，这里再兜一次底）
    tradable_mat = data.tradable.to_numpy(dtype=bool)
    # 涨跌停判定用哪组矩阵由 trade_at 决定：open 成交看开盘一字板，close 成交看收盘涨跌停
    if trade_at == "open":
        limit_up_mat = data.open_limit_up.to_numpy(dtype=bool)
        limit_down_mat = data.open_limit_down.to_numpy(dtype=bool)
    else:
        limit_up_mat = data.limit_up.to_numpy(dtype=bool)
        limit_down_mat = data.limit_down.to_numpy(dtype=bool)
    target_mat = shifted.to_numpy(dtype=float)

    lot = cost_model.lot_size
    slip = cost_model.slippage_rate
    comm = cost_model.commission_rate
    min_comm = cost_model.min_commission
    stamp = cost_model.stamp_tax_rate

    # ---- 账户状态 ----
    shares = np.zeros(n_sym, dtype=float)          # 当前持仓股数
    cash = float(initial_capital)

    nav_arr = np.empty(n_days, dtype=float)
    cash_arr = np.empty(n_days, dtype=float)
    turnover_arr = np.zeros(n_days, dtype=float)
    positions_mat = np.zeros((n_days, n_sym), dtype=float)
    weights_mat = np.zeros((n_days, n_sym), dtype=float)
    trade_rows: list[tuple] = []                   # (date, symbol, side, shares, price, amount, cost)

    for i in range(n_days):
        date = calendar[i]
        target = target_mat[i]
        px = trade_px_mat[i]
        px_nan = np.isnan(px)

        # -- 步骤 2：以成交时点价格估值（px 为 NaN 的持仓用 ffill close 兜底）--
        val_px = np.where(px_nan, close_fill_mat[i], px)
        pos_val_intraday = np.where(shares != 0, shares * val_px, 0.0)
        v_total = cash + pos_val_intraday.sum()

        # -- 可交易性：停牌 / 成交价缺失 一律不可交易（保留持仓，不排队）--
        can_trade = tradable_mat[i] & ~px_nan
        # 买入方向额外受涨停限制，卖出方向受跌停限制（rqalpha price_limit 语义）
        can_buy = can_trade & ~limit_up_mat[i]
        can_sell = can_trade & ~limit_down_mat[i]

        # -- 步骤 3：目标股数（按买入含滑点价折算，lot 取整；target=0 清仓含零股）--
        buy_adj_px = px * (1.0 + slip)
        with np.errstate(invalid="ignore", divide="ignore"):
            raw_shares = target * v_total / buy_adj_px
        raw_shares = np.where(np.isfinite(raw_shares), raw_shares, 0.0)
        if lot > 0:
            target_shares = np.floor(raw_shares / lot) * lot
        else:
            target_shares = raw_shares
        # target=0 → 目标股数严格为 0（卖出全部持仓，含不足一手的零股）
        target_shares = np.where(target <= 0.0, 0.0, target_shares)

        delta = target_shares - shares
        day_amount = 0.0  # 当日成交额合计（买+卖），用于 turnover

        # -- 步骤 4/5/6：先卖后买 --
        # 卖出：delta<0 且允许卖出；不可卖的跳过该标的（保留原持仓）
        sell_mask = (delta < -1e-12) & can_sell
        if sell_mask.any():
            sell_px = px * (1.0 - slip)                      # 卖出压价
            sell_sh = -delta
            for j in np.flatnonzero(sell_mask):
                sh = float(sell_sh[j])
                p = float(sell_px[j])
                amount = sh * p
                if amount <= 0.0:
                    continue
                cost = max(amount * comm, min_comm) + amount * stamp
                cash += amount - cost
                shares[j] -= sh
                day_amount += amount
                trade_rows.append((date, symbols[j], "sell", sh, p, amount, cost))

        # 买入：delta>0 且允许买入；现金不足时按比例缩减买单（再按 lot 取整）
        buy_mask = (delta > 1e-12) & can_buy
        if buy_mask.any():
            idx = np.flatnonzero(buy_mask)
            orig_sh = delta[idx].astype(float)
            exec_px = buy_adj_px[idx]                        # 买入抬价
            scale = 1.0
            buy_sh = orig_sh
            for _ in range(20):                              # 缩减→取整→复核，至多迭代 20 次
                buy_sh = orig_sh * scale
                if lot > 0:
                    buy_sh = np.floor(buy_sh / lot) * lot
                amounts = buy_sh * exec_px
                costs = np.where(amounts > 0.0,
                                 np.maximum(amounts * comm, min_comm), 0.0)
                need = float(amounts.sum() + costs.sum())
                if need <= cash + 1e-9:
                    break
                if need <= 0.0:
                    buy_sh = np.zeros_like(orig_sh)
                    break
                scale *= max(cash, 0.0) / need
            else:
                # 极端情况下（最低佣金撑住总费用）放弃全部买单
                buy_sh = np.zeros_like(orig_sh)
            for k, j in enumerate(idx):
                sh = float(buy_sh[k])
                if sh <= 0.0:
                    continue
                p = float(exec_px[k])
                amount = sh * p
                cost = max(amount * comm, min_comm)
                cash -= amount + cost
                shares[j] += sh
                day_amount += amount
                trade_rows.append((date, symbols[j], "buy", sh, p, amount, cost))

        # -- 步骤 7：日终以 close（ffill 价）估值 --
        eod_px = close_fill_mat[i]
        pos_val = np.where(shares != 0, shares * eod_px, 0.0)
        total_val = cash + pos_val.sum()

        positions_mat[i] = pos_val
        weights_mat[i] = pos_val / total_val if total_val > 0 else 0.0
        cash_arr[i] = cash
        nav_arr[i] = total_val / initial_capital             # 起点相对 initial_capital 归一
        turnover_arr[i] = day_amount / total_val if total_val > 0 else 0.0

    # ---- 组装结果 ----
    nav = pd.Series(nav_arr, index=calendar, name="nav")
    returns = nav.pct_change().fillna(0.0)
    returns.name = "returns"
    positions = pd.DataFrame(positions_mat, index=calendar, columns=symbols)
    actual_weights = pd.DataFrame(weights_mat, index=calendar, columns=symbols)
    cash_series = pd.Series(cash_arr, index=calendar, name="cash")
    turnover = pd.Series(turnover_arr, index=calendar, name="turnover")
    trades = pd.DataFrame(trade_rows, columns=_TRADE_COLUMNS)

    bm_aligned: pd.Series | None = None
    if benchmark is not None:
        # 对齐 nav.index；首个有效值归一到 1.0（基准区间不足时前段保持 NaN）
        bm_aligned = benchmark.reindex(nav.index).ffill()
        first = bm_aligned.first_valid_index()
        if first is not None:
            bm_aligned = bm_aligned / bm_aligned.loc[first]

    return BacktestResult(
        name=name,
        nav=nav,
        returns=returns,
        positions=positions,
        weights=actual_weights,
        cash=cash_series,
        turnover=turnover,
        trades=trades,
        benchmark=bm_aligned,
        initial_capital=initial_capital,
    )
