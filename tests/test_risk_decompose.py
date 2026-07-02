# -*- coding: utf-8 -*-
"""风险分解核心数学的手算验证：ewma 协方差、beta 回归、方差分解恒等式。"""
import numpy as np
import pandas as pd
import pytest

from x_agent.risk.covariance import ewma_cov
from x_agent.risk.decompose import Z_99, decompose
from x_agent.risk.exposure import ANN_FACTOR, estimate_betas, portfolio_exposure
from x_agent.risk.factors import FACTORS, STYLE_FACTORS


def test_ewma_cov_hand_computed():
    """3 个观测、halflife=1 的 EWMA 协方差逐项手算核对。"""
    fr = pd.DataFrame({"f1": [0.01, -0.02, 0.03], "f2": [0.00, 0.01, -0.01]})
    got = ewma_cov(fr, halflife=1, ann_factor=1)

    lam = 0.5
    w = np.array([lam ** 2, lam ** 1, lam ** 0], dtype=float)
    w = w / w.sum()
    x = fr.to_numpy()
    mean = w @ x
    xc = x - mean
    expected = (xc * w[:, None]).T @ xc

    assert np.allclose(got.to_numpy(), expected, atol=1e-12)
    # 对称 + 对角非负
    assert np.allclose(got.to_numpy(), got.to_numpy().T)
    assert (np.diag(got.to_numpy()) >= 0).all()


def test_estimate_betas_recovers_known_coefficients():
    """无噪声线性构造的股票收益，回归应精确还原 beta（容差 1e-6）。"""
    rng = np.random.default_rng(7)
    n = 300
    idx = pd.bdate_range("2024-01-01", periods=n)
    styles = pd.DataFrame(rng.normal(0, 0.01, (n, 4)), index=idx, columns=STYLE_FACTORS)
    fr = styles.reindex(columns=FACTORS).fillna(0.0)

    true_beta = {"mkt": 1.2, "size": -0.5, "mom": 0.3, "vol": 0.0}
    stock = sum(true_beta[f] * styles[f] for f in STYLE_FACTORS) + 0.0001
    stock_ret = pd.DataFrame({"sh.600000": stock})

    betas, resid_vol = estimate_betas(stock_ret, fr,
                                      sector_map={"sh.600000": "Financials"})
    for f in STYLE_FACTORS:
        assert betas.loc["sh.600000", f] == pytest.approx(true_beta[f], abs=1e-6)
    assert betas.loc["sh.600000", "Financials"] == 1.0
    assert resid_vol["sh.600000"] == pytest.approx(0.0, abs=1e-8)


def test_estimate_betas_short_history_fallback():
    """样本不足 min_periods 时兜底：mkt beta=1，其余 0。"""
    idx = pd.bdate_range("2024-01-01", periods=50)
    fr = pd.DataFrame(0.001, index=idx, columns=FACTORS)
    stock_ret = pd.DataFrame({"NEW": pd.Series(0.002, index=idx)})
    betas, _ = estimate_betas(stock_ret, fr, min_periods=120)
    assert betas.loc["NEW", "mkt"] == 1.0
    assert betas.loc["NEW", STYLE_FACTORS[1:]].abs().sum() == 0.0


def test_decompose_two_stock_hand_computed():
    """2 只股票的玩具组合：σ_p、VaR、CCR 与手算值一致（容差 1e-6）。"""
    factors = ["mkt", "size"]
    fcov = pd.DataFrame([[0.04, 0.01], [0.01, 0.02]], index=factors, columns=factors)
    betas = pd.DataFrame([[1.0, 0.5], [0.8, -0.2]],
                         index=["A", "B"], columns=factors)
    resid_vol = pd.Series({"A": 0.20, "B": 0.30})
    weights = pd.Series({"A": 0.6, "B": 0.4})

    rpt = decompose(weights, betas, fcov, resid_vol)

    # 手算：b = wᵀβ
    b = np.array([0.6 * 1.0 + 0.4 * 0.8, 0.6 * 0.5 + 0.4 * (-0.2)])  # [0.92, 0.22]
    sigma = fcov.to_numpy()
    var_f = float(b @ sigma @ b)
    var_s = 0.6 ** 2 * 0.20 ** 2 + 0.4 ** 2 * 0.30 ** 2
    var_p = var_f + var_s

    assert rpt.vol_ann == pytest.approx(np.sqrt(var_p), abs=1e-9)
    assert rpt.factor_vol == pytest.approx(np.sqrt(var_f), abs=1e-9)
    assert rpt.specific_vol == pytest.approx(np.sqrt(var_s), abs=1e-9)
    assert rpt.var99_1d == pytest.approx(Z_99 * np.sqrt(var_p / ANN_FACTOR), abs=1e-9)
    # 暴露
    assert rpt.exposures["mkt"] == pytest.approx(b[0], abs=1e-9)
    assert rpt.exposures["size"] == pytest.approx(b[1], abs=1e-9)
    # CCR 手算：ccr_k = b_k (Σb)_k / var_p
    sigma_b = sigma @ b
    for k, f in enumerate(factors):
        assert rpt.ccr[f] == pytest.approx(b[k] * sigma_b[k] / var_p, abs=1e-9)


def test_decompose_contributions_sum_to_portfolio_variance():
    """恒等式：Σ_k ccr + 特质占比 = 1；Σ_i stock_ccr = 1（即贡献加总=组合方差）。"""
    factors = ["mkt", "size", "mom"]
    rng = np.random.default_rng(11)
    a = rng.normal(0, 0.1, (3, 3))
    fcov = pd.DataFrame(a @ a.T + np.eye(3) * 0.01, index=factors, columns=factors)
    betas = pd.DataFrame(rng.normal(0, 1, (4, 3)),
                         index=list("WXYZ"), columns=factors)
    resid_vol = pd.Series(rng.uniform(0.1, 0.4, 4), index=list("WXYZ"))
    weights = pd.Series([0.4, 0.3, 0.2, 0.1], index=list("WXYZ"))

    rpt = decompose(weights, betas, fcov, resid_vol)
    assert rpt.ccr.sum() + rpt.specific_share == pytest.approx(1.0, abs=1e-9)
    assert rpt.stock_ccr.sum() == pytest.approx(1.0, abs=1e-9)
    # 贡献占比 × 组合方差 = 各自的方差贡献，加总还原组合方差
    var_p = rpt.vol_ann ** 2
    assert (rpt.stock_ccr * var_p).sum() == pytest.approx(var_p, abs=1e-9)


def test_decompose_tracking_error():
    """TE = 主动收益的年化标准差。"""
    idx = pd.bdate_range("2024-01-01", periods=300)
    rng = np.random.default_rng(3)
    port = pd.Series(rng.normal(0.0005, 0.01, 300), index=idx)
    bench = pd.Series(rng.normal(0.0004, 0.01, 300), index=idx)

    fcov = pd.DataFrame([[0.04]], index=["mkt"], columns=["mkt"])
    betas = pd.DataFrame([[1.0]], index=["A"], columns=["mkt"])
    rpt = decompose(pd.Series({"A": 1.0}), betas, fcov, pd.Series({"A": 0.1}),
                    portfolio_ret=port, benchmark_ret=bench, te_window=250)
    expected = (port - bench).dropna().tail(250).std(ddof=1) * np.sqrt(ANN_FACTOR)
    assert rpt.te_ann == pytest.approx(float(expected), abs=1e-12)


def test_portfolio_exposure_weighted_sum():
    betas = pd.DataFrame([[1.0, 0.0], [0.0, 1.0]], index=["A", "B"],
                         columns=["mkt", "size"])
    b = portfolio_exposure(betas, pd.Series({"A": 0.7, "B": 0.3}))
    assert b["mkt"] == pytest.approx(0.7)
    assert b["size"] == pytest.approx(0.3)
