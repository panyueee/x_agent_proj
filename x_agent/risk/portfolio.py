"""持仓读取与估值：positions 快照 → [symbol, market, weight] 归一化权重。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from backtest.data import MARKET_DIRS


def infer_market(symbol: str, store=None, data_dir: str | Path = "data") -> str | None:
    """标的市场判定：优先查 securities 主表，缺失时按 parquet 文件存在性推断。"""
    if store is not None:
        try:
            sec = store.get_security(symbol)
            if sec and sec.get("market"):
                return sec["market"]
        except Exception:
            pass
    data_dir = Path(data_dir)
    for market in ("a", "us", "hk", "crypto", "etf", "index", "cb", "fx", "futures", "bond"):
        if (data_dir / MARKET_DIRS[market] / f"{symbol}.parquet").exists():
            return market
    return None


def latest_close(symbol: str, market: str, data_dir: str | Path = "data") -> float | None:
    """读该标的 parquet 尾行复权收盘价（估值用）。"""
    from backtest.data import load_market_data
    try:
        md = load_market_data(market, [symbol], data_dir=data_dir)
        px = md.close[symbol].dropna()
        return float(px.iloc[-1]) if len(px) else None
    except Exception:
        return None


def load_positions(store, portfolio_id: str = "demo", asof: str | None = None,
                   data_dir: str | Path = "data") -> pd.DataFrame:
    """组合最近一次快照 → DataFrame[symbol, market, quantity, cost_price, weight]。

    weight 缺失的行用 最新close × quantity 现算市值占比补齐（同币种假设，跨币种
    组合请直接录 weight）；最后整体归一化到 Σw=1。
    """
    rows = store.latest_positions(portfolio_id, asof=asof)
    if not rows:
        raise ValueError(f"组合 {portfolio_id!r} 没有任何持仓快照，"
                         f"先用 scripts/manage_portfolio.py 录入")

    df = pd.DataFrame(rows)
    df["market"] = [infer_market(s, store, data_dir) for s in df["symbol"]]
    unknown = df[df["market"].isna()]["symbol"].tolist()
    if unknown:
        raise ValueError(f"无法识别市场（本地无 parquet 且主表缺失）: {unknown}")

    # weight 缺失 → 按市值补
    if df["weight"].isna().any():
        mv = []
        for _, r in df.iterrows():
            if pd.notna(r["weight"]):
                mv.append(None)
                continue
            px = latest_close(r["symbol"], r["market"], data_dir)
            if px is None or pd.isna(r["quantity"]):
                raise ValueError(f"{r['symbol']} 缺 weight 且无法用 close×quantity 估值")
            mv.append(px * float(r["quantity"]))
        mv_ser = pd.Series(mv, index=df.index, dtype="float64")
        known_w = df["weight"].fillna(0.0).sum()
        if mv_ser.notna().any():
            # 未定权重部分按市值占比分配剩余权重（已有权重的行保持原值）
            residual = max(1.0 - known_w, 0.0)
            share = mv_ser / mv_ser.sum() * (residual if known_w > 0 else 1.0)
            df["weight"] = df["weight"].fillna(share)

    total = df["weight"].sum()
    if total <= 0:
        raise ValueError(f"组合 {portfolio_id!r} 权重之和 <= 0")
    df["weight"] = df["weight"] / total
    return df[["symbol", "market", "quantity", "cost_price", "weight", "date"]]
