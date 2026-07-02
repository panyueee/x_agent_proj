"""持仓因子暴露：逐标的对风格因子做时序 OLS 回归（250 日窗口）。

行业哑变量不进回归，直接查 sector_map 赋 1（避免多重共线，见路线图 §3.2）；
非 A 股 / 无行业分类的标的行业暴露全 0。返回同时附带年化特质波动。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .factors import FACTORS, GICS_11, STYLE_FACTORS

ANN_FACTOR = 252  # 年化交易日数


def estimate_betas(stock_returns: pd.DataFrame, factor_returns: pd.DataFrame,
                   window: int = 250, min_periods: int = 120,
                   sector_map: dict[str, str] | None = None,
                   ) -> tuple[pd.DataFrame, pd.Series]:
    """逐持仓 OLS 时序回归 → (betas: symbol×factor, resid_vol: symbol → 年化特质波动)。

    - 回归只用风格因子（mkt/size/mom/vol）+ 截距，取窗口末段最近 window 个有效样本；
    - 有效样本 < min_periods 的标的：mkt beta 记 1、其余 0，resid_vol 用自身波动近似
      （新股/短历史的保守兜底，报告侧应提示）；
    - 行业暴露由 sector_map 直接赋 1。
    """
    sector_map = sector_map or {}
    styles = factor_returns[STYLE_FACTORS]

    betas = pd.DataFrame(0.0, index=stock_returns.columns, columns=FACTORS)
    resid_vol = pd.Series(0.0, index=stock_returns.columns, name="resid_vol")

    for sym in stock_returns.columns:
        joined = pd.concat([stock_returns[sym].rename("y"), styles], axis=1).dropna()
        joined = joined.tail(window)
        if len(joined) < min_periods:
            betas.loc[sym, "mkt"] = 1.0
            resid_vol[sym] = float(joined["y"].std(ddof=1) or 0.0) * np.sqrt(ANN_FACTOR) \
                if len(joined) > 2 else 0.0
        else:
            y = joined["y"].to_numpy()
            x = np.column_stack([joined[STYLE_FACTORS].to_numpy(),
                                 np.ones(len(joined))])
            coef, *_ = np.linalg.lstsq(x, y, rcond=None)
            betas.loc[sym, STYLE_FACTORS] = coef[:-1]
            resid = y - x @ coef
            resid_vol[sym] = float(np.std(resid, ddof=1)) * np.sqrt(ANN_FACTOR)

        sec = sector_map.get(sym)
        if sec in GICS_11:
            betas.loc[sym, sec] = 1.0

    return betas, resid_vol


def portfolio_exposure(betas: pd.DataFrame, weights: pd.Series) -> pd.Series:
    """组合暴露 b = Σ w_i × beta_i（按索引对齐，缺失权重按 0）。"""
    w = weights.reindex(betas.index).fillna(0.0)
    return betas.mul(w, axis=0).sum(axis=0)
