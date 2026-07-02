"""
backtest/metrics.py 绩效指标测试 + backtest/event_study.py map_ticker 映射测试
（接口冻结版，见 docs/backtest_design.md §3.3 / §3.5 / §4）。

已知净值序列 nav = [1.0, 1.1, 0.99, 1.089] 手算验证：
  逐日收益 = [+10%, -10%, +10%]（1.1×0.9=0.99，0.99×1.1=1.089）
  - total_return   = 0.089
  - max_drawdown   = 0.99/1.1 - 1 = -0.10，峰=第2天（1.1），谷=第3天（0.99）
  - sharpe         = mean/std(ddof=1，ffn/pandas 口径)×sqrt(252)
  - annual_return  = 几何口径 (1+0.089)^(252/3) - 1（3 个持有期）
  - calmar         = annual_return / |max_drawdown|
  - win_rate       = 上涨日/总日数（returns 含首日 fillna 的 0，空仓零收益日计入分母）
  - annual_factor  = 加密连续自然日历 → 365；工作日日历 → 252
  - 波动为 0       → sharpe 记 0
  - map_ticker     = "$BTC"→("crypto","BTC-USD")、6 位 A 股代码按 6→sh / 0,3→sz、
                     无法映射 → None

全部手工构造，不读 data/、不碰 output/、不连任何数据库。

运行：
    .venv/bin/python -m pytest tests/test_backtest_metrics.py -v
"""
from __future__ import annotations

import math
import os
import sys

import pandas as pd
import pytest

# 让 `import backtest.metrics` 在任意 cwd 下都能工作
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from backtest.engine import BacktestResult
from backtest.event_study import map_ticker
from backtest.metrics import (
    annual_factor,
    annualized_return,
    compute_metrics,
    max_drawdown,
    sharpe,
)


# ── 辅助 ─────────────────────────────────────────────────────────────────────

# 4 个工作日：2026-01-05（周一）~ 2026-01-08（周四）
IDX = pd.bdate_range("2026-01-05", periods=4)
# 逐日收益 +10% / -10% / +10%
NAV = pd.Series([1.0, 1.1, 0.99, 1.089], index=IDX, name="nav")


def _fake_result(nav: pd.Series) -> BacktestResult:
    """按冻结字段手工拼一个最小 BacktestResult 供 compute_metrics 使用。"""
    idx = nav.index
    zeros = pd.DataFrame(0.0, index=idx, columns=["X"])
    trades = pd.DataFrame(
        columns=["date", "symbol", "side", "shares", "price", "amount", "cost"])
    return BacktestResult(
        name="metrics_test",
        nav=nav,
        returns=nav.pct_change().fillna(0.0),
        positions=zeros,
        weights=zeros.copy(),
        cash=pd.Series(0.0, index=idx),
        turnover=pd.Series(0.0, index=idx),
        trades=trades,
        benchmark=None,
        initial_capital=1_000_000.0,
    )


# ── 1. 最大回撤（含峰谷日期） ─────────────────────────────────────────────────

def test_max_drawdown_value_and_dates():
    """cummax = [1, 1.1, 1.1, 1.1]，dd = nav/cummax-1 = [0, 0, -0.10, -0.01]
    → 最大回撤 -0.10（负数口径），峰=第 2 天（1.1），谷=第 3 天（0.99）。"""
    dd, peak, trough = max_drawdown(NAV)
    assert dd == pytest.approx(-0.10)
    assert peak == IDX[1]
    assert trough == IDX[2]


# ── 2. 年化收益（几何口径） ───────────────────────────────────────────────────

def test_annualized_return_geometric():
    """几何年化：4 个净值点 = 3 个持有期，
    annual = (1.089)^(252/3) - 1。"""
    expected = 1.089 ** (252.0 / 3.0) - 1.0
    assert annualized_return(NAV, 252.0) == pytest.approx(expected, rel=1e-9)


# ── 3. 夏普 ───────────────────────────────────────────────────────────────────

def test_sharpe_hand_calc():
    """手算（ffn 口径：pandas std，即样本标准差 ddof=1）：
    returns = [0.1, -0.1, 0.1]
    mean = 0.1/3 ≈ 0.033333
    std  = sqrt(((0.1-m)² + (-0.1-m)² + (0.1-m)²)/2) ≈ 0.115470
    sharpe = mean/std × sqrt(252) ≈ 4.582576。"""
    rets = pd.Series([0.1, -0.1, 0.1], index=IDX[1:])
    m = 0.1 / 3.0
    sd = math.sqrt(((0.1 - m) ** 2 + (-0.1 - m) ** 2 + (0.1 - m) ** 2) / 2.0)
    expected = m / sd * math.sqrt(252.0)
    assert sharpe(rets, 252.0) == pytest.approx(expected, rel=1e-6)
    # 数值钉死，防止公式和手算同时改错
    assert sharpe(rets, 252.0) == pytest.approx(4.582576, rel=1e-5)


def test_sharpe_zero_volatility_is_zero():
    """波动为 0（全 0 或恒定收益）→ sharpe 记 0，不得除零报错/返回 inf。"""
    flat0 = pd.Series([0.0, 0.0, 0.0], index=IDX[1:])
    assert sharpe(flat0, 252.0) == 0.0
    const = pd.Series([0.01, 0.01, 0.01], index=IDX[1:])
    assert sharpe(const, 252.0) == 0.0


# ── 4. annual_factor ─────────────────────────────────────────────────────────

def test_annual_factor_crypto_daily_365():
    """加密日历 = 连续自然日（含周末）→ 365。"""
    crypto_idx = pd.date_range("2026-01-01", periods=30, freq="D")
    assert annual_factor(crypto_idx) == pytest.approx(365.0)


def test_annual_factor_business_days_252():
    """工作日日历 → 252。
    注意：工作日平均间隔 ≈ 7/5 = 1.4 天 < 1.5，若实现按字面
    "均值 < 1.5 → 365" 会把工作日历误判成加密，本用例钉死冻结语义
    （工作日/交易日日历必须得到 252）。"""
    bd_idx = pd.bdate_range("2026-01-05", periods=30)
    assert annual_factor(bd_idx) == pytest.approx(252.0)


# ── 5. compute_metrics 汇总 ──────────────────────────────────────────────────

def test_compute_metrics_known_nav():
    """对 NAV 显式传 freq=252 汇总验证（避免 annual_factor 推断干扰）。"""
    res = _fake_result(NAV)
    m = compute_metrics(res, freq=252.0)

    # 区间与天数
    assert m["n_days"] == 4
    assert pd.Timestamp(m["start"]) == IDX[0]
    assert pd.Timestamp(m["end"]) == IDX[-1]

    # 总收益 / 回撤（含峰谷日期）
    assert m["total_return"] == pytest.approx(0.089)
    assert m["max_drawdown"] == pytest.approx(-0.10)
    assert pd.Timestamp(m["max_drawdown_peak"]) == IDX[1]
    assert pd.Timestamp(m["max_drawdown_trough"]) == IDX[2]

    # 几何年化与卡玛
    expected_annual = 1.089 ** (252.0 / 3.0) - 1.0
    assert m["annual_return"] == pytest.approx(expected_annual, rel=1e-9)
    assert m["calmar"] == pytest.approx(expected_annual / 0.10, rel=1e-9)

    # 胜率：returns = [0(首日 fillna), +0.1, -0.1, +0.1]，
    # 上涨 2 天 / 共 4 天 = 0.5（空仓/零收益日计入分母，见设计文档 §3.3）
    assert m["win_rate"] == pytest.approx(0.5)

    # 无交易、无基准
    assert m["n_trades"] == 0
    assert "benchmark_total_return" not in m


# ── 6. event_study.map_ticker ────────────────────────────────────────────────

def test_map_ticker_crypto_symbol():
    """$ 前缀加密符号 → yfinance 风格 crypto 代码。"""
    assert map_ticker("$BTC") == ("crypto", "BTC-USD")
    assert map_ticker("$ETH") == ("crypto", "ETH-USD")


def test_map_ticker_a_share_codes():
    """6 位数字 A 股代码：6 开头 → sh，0/3 开头 → sz。"""
    assert map_ticker("600519") == ("a", "sh.600519")
    assert map_ticker("000001") == ("a", "sz.000001")
    assert map_ticker("300750") == ("a", "sz.300750")


def test_map_ticker_unmappable_returns_none():
    """无法映射的输入 → None（不抛异常）。"""
    assert map_ticker("垃圾") is None
