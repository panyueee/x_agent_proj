"""
backtest/engine.py 引擎测试（接口冻结版，见 docs/backtest_design.md §3.2 / §4）。

覆盖（每个用例都附手算注释）：
  (a) T+1：t 日给出权重，t+1 日以 open 成交，t 日不产生任何交易；
  (b) 成本：佣金 max(amount×0.00025, 5)、卖出加印花税 0.001，现金轨迹验证到分；
  (c) lot 取整：100 股一手向下取整；
  (d) 涨停日（limit_up / open_limit_up 置 True）买单被跳过，持仓不变、不排队；
  (e) 停牌（tradable=False）卖单被跳过，保留持仓，恢复后可卖出；
  (f) 权重行和 > 1 → ValueError；
  (g) 负权重 → ValueError；
  (h) trade_at="close" 用收盘价成交。

MarketData 直接手工构造（import backtest.data 的 dataclass），4~6 天 × 1~2 标的，
价格取整好手算。CostModel 全部显式传参（多数用例 slippage_rate=0 简化手算），
不依赖 for_market 默认值。不读 data/、不碰 output/。

运行：
    .venv/bin/python -m pytest tests/test_backtest_engine.py -v
"""
from __future__ import annotations

import os
import sys

import pandas as pd
import pytest

# 让 `import backtest.engine` 在任意 cwd 下都能工作
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from backtest.data import MarketData
from backtest.engine import CostModel, run_backtest


# ── 辅助 ─────────────────────────────────────────────────────────────────────

# 2026-01-05（周一）起连续 4 个交易日
DATES4 = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]
T1, T2, T3, T4 = (pd.Timestamp(d) for d in DATES4)


def _mkt(dates: list[str], symbols: list[str], close_rows: list[list[float]],
         open_rows: list[list[float]] | None = None, market: str = "a") -> MarketData:
    """手工构造 MarketData：默认全可交易、无涨跌停；测试可再改单元格。"""
    idx = pd.DatetimeIndex(pd.to_datetime(dates))
    close = pd.DataFrame(close_rows, index=idx, columns=symbols, dtype=float)
    open_ = (close.copy() if open_rows is None
             else pd.DataFrame(open_rows, index=idx, columns=symbols, dtype=float))
    false = pd.DataFrame(False, index=idx, columns=symbols)
    return MarketData(
        market=market,
        calendar=idx,
        open=open_,
        high=close.copy(),
        low=close.copy(),
        close=close,
        volume=pd.DataFrame(1_000_000.0, index=idx, columns=symbols),
        tradable=pd.DataFrame(True, index=idx, columns=symbols),
        limit_up=false.copy(),
        limit_down=false.copy(),
        open_limit_up=false.copy(),
        open_limit_down=false.copy(),
    )


def _zero_cost(lot_size: int = 100) -> CostModel:
    """零费用模型：专注验证撮合/取整逻辑。"""
    return CostModel(commission_rate=0.0, min_commission=0.0,
                     stamp_tax_rate=0.0, slippage_rate=0.0, lot_size=lot_size)


def _weights(md: MarketData) -> pd.DataFrame:
    """与 MarketData 同形状的全 0 权重矩阵，测试内逐格填。"""
    return pd.DataFrame(0.0, index=md.calendar, columns=md.close.columns)


# ── (a) T+1：t 日权重 → t+1 日以 open 成交 ────────────────────────────────────

def test_t_plus_1_trade_at_next_open():
    """手算：
    T1 权重 X=1.0 → 引擎 shift 后 T2 以 open[T2]=11 成交。
    T2 估值 V = cash 1,000,000（尚无持仓），目标股数
      floor(1,000,000 / 11 / 100) × 100 = floor(909.09) × 100 = 90,900 股，
    成交额 90,900 × 11 = 999,900，现金余 100。
    T3/T4 open=12：V = 100 + 90,900×12 = 1,090,900，
      floor(1,090,900/12/100)×100 = 90,900 → 与持仓相同，不再交易。
    """
    md = _mkt(DATES4, ["X", "Y"],
              close_rows=[[10, 10], [12, 10], [12, 10], [12, 10]],
              open_rows=[[10, 10], [11, 10], [12, 10], [12, 10]])
    w = _weights(md)
    w.loc[T1:, "X"] = 1.0  # T1 起持续满仓 X
    res = run_backtest(md, w, initial_capital=1_000_000.0,
                       cost_model=_zero_cost(), trade_at="open")

    # T1 当天绝不成交：现金原封不动、持仓为 0、净值 1.0
    assert res.cash.loc[T1] == pytest.approx(1_000_000.0)
    assert res.positions.loc[T1, "X"] == pytest.approx(0.0)
    assert res.nav.loc[T1] == pytest.approx(1.0)

    # 全程只有 T2 一笔买入，价格是 T2 的 open=11（而非 T1 收盘或 T2 收盘）
    assert len(res.trades) == 1
    tr = res.trades.iloc[0]
    assert pd.Timestamp(tr["date"]) == T2
    assert tr["symbol"] == "X"
    assert tr["side"] == "buy"
    assert tr["shares"] == pytest.approx(90_900)
    assert tr["price"] == pytest.approx(11.0)
    assert tr["amount"] == pytest.approx(999_900.0)

    # T2 日终按 close=12 估值：nav = (100 + 90,900×12)/1,000,000 = 1.0909
    assert res.cash.loc[T2] == pytest.approx(100.0)
    assert res.positions.loc[T2, "X"] == pytest.approx(1_090_800.0)
    assert res.nav.loc[T2] == pytest.approx(1.0909)


# ── (b) 成本：佣金 + 最低 5 元 + 卖出印花税 ───────────────────────────────────

def test_cost_model_unit_formulas():
    """手算：
    buy_cost(100,000) = max(100,000×0.00025, 5) = max(25, 5) = 25；
    buy_cost(10,000)  = max(2.5, 5) = 5（最低佣金兜底）；
    sell_cost(100,000) = 25 + 100,000×0.001 = 125；
    sell_cost(10,000)  = 5 + 10 = 15。
    """
    cm = CostModel(commission_rate=0.00025, min_commission=5.0,
                   stamp_tax_rate=0.001, slippage_rate=0.0, lot_size=100)
    assert cm.buy_cost(100_000.0) == pytest.approx(25.0)
    assert cm.buy_cost(10_000.0) == pytest.approx(5.0)
    assert cm.sell_cost(100_000.0) == pytest.approx(125.0)
    assert cm.sell_cost(10_000.0) == pytest.approx(15.0)


def test_cash_trajectory_with_costs():
    """手算现金轨迹（价格恒为 10，滑点 0）：
    初始现金 200,000。
    T1 权重 X=0.5 → T2 买入：目标市值 100,000，
      股数 floor(100,000/10/100)×100 = 10,000 股，amount = 100,000，
      佣金 max(100,000×0.00025, 5) = 25 → 现金 200,000-100,000-25 = 99,975.00。
    T2 权重 X=0 → T3 清仓卖出 10,000 股，amount = 100,000，
      成本 = 佣金 25 + 印花税 100,000×0.001=100 → 125，
      现金 99,975+100,000-125 = 199,850.00。
    """
    md = _mkt(DATES4[:3], ["X"], close_rows=[[10], [10], [10]])
    cm = CostModel(commission_rate=0.00025, min_commission=5.0,
                   stamp_tax_rate=0.001, slippage_rate=0.0, lot_size=100)
    w = _weights(md)
    w.loc[T1, "X"] = 0.5
    w.loc[T2, "X"] = 0.0
    res = run_backtest(md, w, initial_capital=200_000.0,
                       cost_model=cm, trade_at="open")

    assert res.cash.loc[T1] == pytest.approx(200_000.0, abs=0.01)
    assert res.cash.loc[T2] == pytest.approx(99_975.0, abs=0.01)
    assert res.cash.loc[T3] == pytest.approx(199_850.0, abs=0.01)
    # nav 同步验证：T2 = (99,975+100,000)/200,000 = 0.999875；T3 = 0.99925
    assert res.nav.loc[T2] == pytest.approx(0.999875)
    assert res.nav.loc[T3] == pytest.approx(0.99925)
    # 交易明细的 cost 列
    buys = res.trades[res.trades["side"] == "buy"]
    sells = res.trades[res.trades["side"] == "sell"]
    assert len(buys) == 1 and len(sells) == 1
    assert buys.iloc[0]["cost"] == pytest.approx(25.0)
    assert sells.iloc[0]["cost"] == pytest.approx(125.0)


# ── (c) lot 取整 ──────────────────────────────────────────────────────────────

def test_lot_size_floor():
    """手算：价格 7、现金 100,000、满仓：
    100,000/7 = 14,285.71 股 → floor(142.857)×100 = 14,200 股，
    amount = 14,200×7 = 99,400，现金余 600。
    """
    md = _mkt(DATES4[:2], ["X"], close_rows=[[7], [7]])
    w = _weights(md)
    w.loc[T1, "X"] = 1.0
    res = run_backtest(md, w, initial_capital=100_000.0,
                       cost_model=_zero_cost(lot_size=100), trade_at="open")
    tr = res.trades.iloc[0]
    assert tr["shares"] == pytest.approx(14_200)
    assert tr["amount"] == pytest.approx(99_400.0)
    assert res.cash.loc[T2] == pytest.approx(600.0)


# ── (d) 涨停日买单跳过 ────────────────────────────────────────────────────────

def test_limit_up_buy_skipped():
    """T1 权重 X=1.0 → 本应 T2 买入，但 T2 涨停（limit_up/open_limit_up=True）
    → 买单跳过、持仓不变、现金不动、无交易记录。
    T2 权重仍 1.0 → T3 涨停打开，以 open[T3]=11 正常买入：
    floor(1,000,000/11/100)×100 = 90,900 股。跳过不排队，但后续信号照常执行。
    """
    md = _mkt(DATES4[:3], ["X"], close_rows=[[10], [11], [11]])
    md.limit_up.loc[T2, "X"] = True
    md.open_limit_up.loc[T2, "X"] = True
    w = _weights(md)
    w.loc[T1:, "X"] = 1.0
    res = run_backtest(md, w, initial_capital=1_000_000.0,
                       cost_model=_zero_cost(), trade_at="open")

    # T2 无任何成交，持仓仍为 0，现金原样
    assert res.positions.loc[T2, "X"] == pytest.approx(0.0)
    assert res.cash.loc[T2] == pytest.approx(1_000_000.0)
    assert not (pd.to_datetime(res.trades["date"]) == T2).any()
    # T3 恢复后正常买入
    t3_trades = res.trades[pd.to_datetime(res.trades["date"]) == T3]
    assert len(t3_trades) == 1
    assert t3_trades.iloc[0]["side"] == "buy"
    assert t3_trades.iloc[0]["shares"] == pytest.approx(90_900)


# ── (e) 停牌保留持仓 ──────────────────────────────────────────────────────────

def test_suspension_keeps_position():
    """价格恒为 10、零费用：
    T1 权重 1.0 → T2 买入 100,000 股（现金 0）。
    T2 权重 0 → 本应 T3 清仓，但 T3 tradable=False → 卖单跳过、持仓原样保留。
    T3 权重 0 → T4 复牌，卖出 100,000 股 → 现金回到 1,000,000。
    """
    md = _mkt(DATES4, ["X"], close_rows=[[10], [10], [10], [10]])
    md.tradable.loc[T3, "X"] = False
    w = _weights(md)
    w.loc[T1, "X"] = 1.0
    # T2 起权重归 0（默认即 0）
    res = run_backtest(md, w, initial_capital=1_000_000.0,
                       cost_model=_zero_cost(), trade_at="open")

    # T2 买入后满仓
    assert res.positions.loc[T2, "X"] == pytest.approx(1_000_000.0)
    assert res.cash.loc[T2] == pytest.approx(0.0)
    # T3 停牌：卖单被跳过，持仓保留，无 T3 交易记录
    assert res.positions.loc[T3, "X"] == pytest.approx(1_000_000.0)
    assert res.cash.loc[T3] == pytest.approx(0.0)
    assert not (pd.to_datetime(res.trades["date"]) == T3).any()
    # T4 复牌清仓
    assert res.positions.loc[T4, "X"] == pytest.approx(0.0)
    assert res.cash.loc[T4] == pytest.approx(1_000_000.0)
    # 全程价格不变、零费用 → 净值恒 1.0
    assert res.nav.loc[T4] == pytest.approx(1.0)


# ── (f)(g) 非法权重报错 ───────────────────────────────────────────────────────

def test_row_sum_over_1_raises():
    """某行权重和 1.2 > 1+1e-6 → ValueError（杠杆未建模）。"""
    md = _mkt(DATES4[:2], ["X", "Y"], close_rows=[[10, 10], [10, 10]])
    w = _weights(md)
    w.loc[T1, "X"] = 0.6
    w.loc[T1, "Y"] = 0.6
    with pytest.raises(ValueError):
        run_backtest(md, w, cost_model=_zero_cost())


def test_negative_weight_raises():
    """负权重（做空未建模）→ ValueError。"""
    md = _mkt(DATES4[:2], ["X", "Y"], close_rows=[[10, 10], [10, 10]])
    w = _weights(md)
    w.loc[T1, "X"] = -0.5
    with pytest.raises(ValueError):
        run_backtest(md, w, cost_model=_zero_cost())


# ── (h) trade_at="close" ─────────────────────────────────────────────────────

def test_trade_at_close_uses_close_price():
    """手算：T1 权重 1.0 → T2 以 close[T2]=12（而非 open[T2]=11）成交：
    floor(1,000,000/12/100)×100 = floor(833.33)×100 = 83,300 股，
    amount = 83,300×12 = 999,600，现金余 400。
    """
    md = _mkt(DATES4[:2], ["X"],
              close_rows=[[10], [12]],
              open_rows=[[10], [11]])
    w = _weights(md)
    w.loc[T1, "X"] = 1.0
    res = run_backtest(md, w, initial_capital=1_000_000.0,
                       cost_model=_zero_cost(), trade_at="close")
    tr = res.trades.iloc[0]
    assert pd.Timestamp(tr["date"]) == T2
    assert tr["price"] == pytest.approx(12.0)
    assert tr["shares"] == pytest.approx(83_300)
    assert res.cash.loc[T2] == pytest.approx(400.0)
