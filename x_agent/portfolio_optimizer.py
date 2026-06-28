"""组合权重优化模块（PyPortfolioOpt）。

从 DB 读取近期高分信号中的股票代码，结合价格历史，
用 Black-Litterman 模型将信号分数转为仓位权重建议。

无信号时退化为等权组合；数据不足时跳过优化直接返回等权。
"""
from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict
from typing import Optional

import numpy as np
import pandas as pd


def _fetch_price_history(symbols: list[str], store) -> pd.DataFrame:
    """
    从 price_bars 表读取各品种的历史收盘价，返回 DataFrame（index=日期，columns=symbol）。
    只取有足够历史的品种（至少 20 个交易日数据点）。
    """
    frames = {}
    for sym in symbols:
        rows = store.conn.execute(
            "SELECT timestamp, close FROM price_bars WHERE symbol=? ORDER BY timestamp",
            (sym,),
        ).fetchall()
        if len(rows) < 5:
            continue
        s = pd.Series({r[0][:10]: r[1] for r in rows}, name=sym)
        frames[sym] = s
    if not frames:
        return pd.DataFrame()
    df = pd.DataFrame(frames)
    df.index = pd.to_datetime(df.index)
    return df.dropna(how="all")


def _signal_views(store, symbols: list[str], lookback_hours: int = 72) -> dict[str, float]:
    """
    从 signals 表统计近期各 ticker 的平均信号分（0–10），
    归一化为 Black-Litterman absolute view（预期超额收益）。
    """
    since = (dt.datetime.utcnow() - dt.timedelta(hours=lookback_hours)).isoformat()
    rows = store.conn.execute(
        "SELECT s.tickers, s.score FROM signals s "
        "JOIN tweets t ON t.id = s.tweet_id "
        "WHERE t.created_at >= ? AND s.score >= 3",
        (since,),
    ).fetchall()

    ticker_scores: dict[str, list[float]] = defaultdict(list)
    for tickers_json, score in rows:
        try:
            tickers = json.loads(tickers_json or "[]")
        except Exception:
            continue
        for tk in tickers:
            tk_clean = tk.lstrip("$").upper()
            if tk_clean in symbols:
                ticker_scores[tk_clean].append(float(score))

    views = {}
    for sym in symbols:
        scores = ticker_scores.get(sym, [])
        if scores:
            avg = sum(scores) / len(scores)
            # 分数 5 → 0 超额，10 → +5% 预期年化超额，0 → -5%
            views[sym] = (avg - 5) / 100
    return views


def run_optimizer(store, cfg: dict) -> Optional[dict]:
    """
    主入口：读取配置里所有监控资产，跑 Black-Litterman 优化，返回权重 dict。
    失败时返回 None。
    """
    try:
        from pypfopt import BlackLittermanModel, EfficientFrontier, risk_models, expected_returns
    except ImportError:
        print("[portfolio] pypfopt 未安装，跳过")
        return None

    fin_cfg = cfg.get("finance", {})
    if not fin_cfg.get("enabled"):
        return None

    # 收集所有监控品种的 symbol
    symbols = []
    for item in fin_cfg.get("a_shares", []):
        symbols.append(item["code"])
    for item in fin_cfg.get("us_stocks", []):
        symbols.append(item["symbol"])
    for item in fin_cfg.get("crypto", []):
        symbols.append(item["symbol"])   # 保持 BTC/USDT 格式与 price_bars 一致

    if not symbols:
        return None

    prices = _fetch_price_history(symbols, store)
    if prices.empty or len(prices) < 10:
        print(f"[portfolio] 价格历史不足（{len(prices)} 行），使用等权组合")
        n = len(symbols)
        return {"weights": {s: round(1 / n, 4) for s in symbols}, "method": "equal_weight"}

    # 剔除数据不够的品种
    valid = [c for c in prices.columns if prices[c].notna().sum() >= 10]
    prices = prices[valid]
    if len(valid) < 2:
        return None

    try:
        mu = expected_returns.mean_historical_return(prices, frequency=252)
        S  = risk_models.sample_cov(prices, frequency=252)

        views = _signal_views(store, list(prices.columns))
        if views:
            bl = BlackLittermanModel(S, pi=mu, absolute_views=views)
            ret_bl = bl.bl_returns()
            ef = EfficientFrontier(ret_bl, S)
        else:
            ef = EfficientFrontier(mu, S)

        ef.max_sharpe(risk_free_rate=0.03)
        weights = ef.clean_weights()
        method = "black_litterman" if views else "max_sharpe"
        print(f"[portfolio] 优化完成（{method}），{len(weights)} 个品种，信号观点 {len(views)} 条")
        return {"weights": dict(weights), "method": method, "views": views}

    except Exception as e:
        print(f"[portfolio] 优化失败: {e}，使用等权")
        n = len(valid)
        return {"weights": {s: round(1 / n, 4) for s in valid}, "method": "equal_weight"}
