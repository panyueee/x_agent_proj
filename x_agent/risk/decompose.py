"""组合风险分解：σ_p、因子/特质拆分、MCR/CCR、参数化 VaR、跟踪误差。

数学口径（docs/aladdin/05 §3.2）：
  σ_f² = bᵀΣb（b=组合因子暴露，Σ=年化因子协方差）
  σ_s² = Σ w_i²·resid_i²（年化特质方差）
  σ_p  = sqrt(σ_f² + σ_s²)
  因子贡献占比 ccr_k = b_k·(Σb)_k / σ_p²，Σ_k ccr_k = σ_f²/σ_p²
  个股贡献占比 = (w_i·β_iᵀΣb + w_i²·resid_i²) / σ_p²，对全部持仓加总 = 1
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .exposure import ANN_FACTOR, portfolio_exposure

Z_99 = 2.33  # 99% 单尾正态分位（参数化 VaR）


@dataclass
class RiskReport:
    vol_ann: float                    # 组合年化波动
    var99_1d: float                   # 参数化 1 日 99% VaR（2.33·σ_日）
    factor_vol: float                 # 因子部分年化波动
    specific_vol: float               # 特质部分年化波动
    exposures: pd.Series              # factor → beta
    ccr: pd.Series                    # factor → 风险贡献占比（和 = 因子部分占比）
    stock_ccr: pd.Series              # symbol → 风险贡献占比（和 = 1）
    te_ann: float | None = None       # 对基准跟踪误差（250 日实证），无基准为 None
    specific_share: float = field(default=0.0)  # 特质方差占比 = 1 - Σccr


def decompose(weights: pd.Series, betas: pd.DataFrame, fcov: pd.DataFrame,
              resid_vol: pd.Series,
              portfolio_ret: pd.Series | None = None,
              benchmark_ret: pd.Series | None = None,
              te_window: int = 250) -> RiskReport:
    """给定持仓权重、个股 beta、因子协方差与特质波动，输出完整风险分解。

    weights 建议已归一（Σw=1）；betas 缺失的因子列按 0 处理（对齐 fcov 的列）。
    """
    # 对齐：因子集合以 fcov 为准，标的集合以 weights 为准
    factors = list(fcov.columns)
    b_mat = betas.reindex(index=weights.index, columns=factors).fillna(0.0)
    rv = resid_vol.reindex(weights.index).fillna(0.0)
    w = weights.astype(float)

    b = portfolio_exposure(b_mat, w)                    # factor → 组合暴露
    sigma = fcov.to_numpy(dtype=float)
    b_vec = b.to_numpy(dtype=float)

    sigma_b = sigma @ b_vec                             # (Σb)_k
    var_factor = float(b_vec @ sigma_b)                 # bᵀΣb
    var_specific = float((w ** 2 * rv ** 2).sum())      # Σ w²·resid²
    var_total = var_factor + var_specific
    if var_total <= 0:
        raise ValueError("组合方差为 0：权重或风险输入为空")

    vol_ann = float(np.sqrt(var_total))
    ccr = pd.Series(b_vec * sigma_b / var_total, index=factors, name="ccr")

    # 个股贡献 = 因子部分（w_i·β_i∙Σb）+ 特质部分（w_i²resid_i²），除以总方差
    stock_factor_part = (b_mat.to_numpy(dtype=float) @ sigma_b) * w.to_numpy(dtype=float)
    stock_specific_part = (w ** 2 * rv ** 2).to_numpy(dtype=float)
    stock_ccr = pd.Series((stock_factor_part + stock_specific_part) / var_total,
                          index=w.index, name="stock_ccr")

    te_ann = None
    if portfolio_ret is not None and benchmark_ret is not None:
        active = (portfolio_ret - benchmark_ret).dropna().tail(te_window)
        if len(active) >= 60:
            te_ann = float(active.std(ddof=1) * np.sqrt(ANN_FACTOR))

    return RiskReport(
        vol_ann=vol_ann,
        var99_1d=Z_99 * vol_ann / np.sqrt(ANN_FACTOR),
        factor_vol=float(np.sqrt(var_factor)),
        specific_vol=float(np.sqrt(var_specific)),
        exposures=b,
        ccr=ccr,
        stock_ccr=stock_ccr,
        te_ann=te_ann,
        specific_share=var_specific / var_total,
    )
