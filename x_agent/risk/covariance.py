"""EWMA 因子协方差矩阵（年化）。显式权重实现，便于单测手算核对。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .exposure import ANN_FACTOR


def ewma_cov(factor_returns: pd.DataFrame, halflife: int = 90,
             ann_factor: int = ANN_FACTOR) -> pd.DataFrame:
    """EWMA 因子协方差（年化）：权重 w_t ∝ 0.5^{(T-t)/halflife}，加权去均值。

    因子收益里的 NaN（如某行业历史缺失）整行剔除该因子对之外的做法太复杂，
    v1 直接把全 NaN 列剔除、含 NaN 行丢弃（行业因子 2005 起基本无缺口）。
    """
    fr = factor_returns.dropna(axis=1, how="all").dropna()
    if len(fr) < 2:
        raise ValueError("因子收益样本不足（<2 行），无法估协方差")

    n = len(fr)
    lam = 0.5 ** (1.0 / halflife)
    w = lam ** np.arange(n - 1, -1, -1)
    w = w / w.sum()

    x = fr.to_numpy(dtype=float)
    mean = w @ x
    xc = x - mean
    cov = (xc * w[:, None]).T @ xc * ann_factor
    return pd.DataFrame(cov, index=fr.columns, columns=fr.columns)
