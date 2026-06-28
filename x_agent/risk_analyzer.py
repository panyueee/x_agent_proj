"""风险分析模块（Riskfolio-Lib）。

在 PyPortfolioOpt 的 max_sharpe 基础上增加风险约束：
  - CVaR（条件在险价值）最小化
  - 最大回撤约束
  - 波动率约束

需要的数据：price_bars 表中 ≥ 60 个交易日的收盘价历史。
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Optional

import numpy as np
import pandas as pd


def _load_returns(store, symbols: list[str], min_rows: int = 60) -> pd.DataFrame:
    """从 price_bars 读取日收益率 DataFrame，列=symbol，行=日期。"""
    frames = {}
    for sym in symbols:
        rows = store.conn.execute(
            "SELECT timestamp, close FROM price_bars WHERE symbol=? ORDER BY timestamp",
            (sym,),
        ).fetchall()
        if len(rows) < min_rows:
            continue
        s = pd.Series({r[0][:10]: float(r[1]) for r in rows}, name=sym)
        frames[sym] = s

    if not frames:
        return pd.DataFrame()

    prices = pd.DataFrame(frames)
    prices.index = pd.to_datetime(prices.index)
    returns = prices.pct_change().dropna(how="all")
    return returns


def run_risk_optimizer(store, cfg: dict, method: str = "CVaR") -> Optional[dict]:
    """
    用 Riskfolio-Lib 跑风险约束组合优化。

    method: 'CVaR' | 'MV' | 'MDD'（最小化对应风险度量）
    返回 dict: weights / method / risk_metrics
    需要 ≥ 60 行价格历史，否则返回 None。
    """
    try:
        import riskfolio as rp
    except ImportError:
        print("[risk] riskfolio-lib 未安装，跳过")
        return None

    fin_cfg = cfg.get("finance", {})
    if not fin_cfg.get("enabled"):
        return None

    symbols = []
    for item in fin_cfg.get("a_shares", []):
        symbols.append(item["code"])
    for item in fin_cfg.get("us_stocks", []):
        symbols.append(item["symbol"])
    for item in fin_cfg.get("crypto", []):
        symbols.append(item["symbol"].replace("/", ""))

    returns = _load_returns(store, symbols, min_rows=60)
    if returns.empty or returns.shape[1] < 2:
        missing = [s for s in symbols if s not in returns.columns]
        print(f"[risk] 价格历史不足 60 行，跳过风险优化。缺数据品种: {missing}")
        return None

    try:
        port = rp.Portfolio(returns=returns)
        port.assets_stats(method_mu="hist", method_cov="ledoit")

        model = "Classic"
        rm    = method          # CVaR / MV / MDD
        obj   = "MinRisk"
        hist  = True
        rf    = 0.0
        l     = 0

        w = port.optimization(model=model, rm=rm, obj=obj, rf=rf, l=l, hist=hist)
        if w is None or w.empty:
            return None

        weights = {str(col): float(w.loc[col, "weights"]) for col in w.index}

        # 计算关键风险指标
        ret_vec   = (returns * pd.Series(weights)).sum(axis=1)
        vol_ann   = float(ret_vec.std() * np.sqrt(252))
        cum       = (1 + ret_vec).cumprod()
        drawdown  = (cum / cum.cummax() - 1)
        max_dd    = float(drawdown.min())
        sorted_r  = ret_vec.sort_values()
        cvar_95   = float(sorted_r.iloc[:max(1, int(len(sorted_r) * 0.05))].mean())
        sharpe    = float(ret_vec.mean() / ret_vec.std() * np.sqrt(252)) if ret_vec.std() > 0 else 0.0

        risk_metrics = {
            "vol_ann":  round(vol_ann,  4),
            "max_dd":   round(max_dd,   4),
            "cvar_95":  round(cvar_95,  4),
            "sharpe":   round(sharpe,   4),
            "n_assets": returns.shape[1],
            "n_days":   len(returns),
        }
        print(
            f"[risk] {method} 优化完成 | vol={vol_ann:.1%} max_dd={max_dd:.1%} "
            f"CVaR95={cvar_95:.2%} Sharpe={sharpe:.2f}"
        )
        return {"weights": weights, "method": f"riskfolio_{method}", "risk_metrics": risk_metrics}

    except Exception as e:
        print(f"[risk] Riskfolio 优化失败: {e}")
        return None


def compute_risk_report(store, cfg: dict) -> dict:
    """
    对当前所有监控品种计算风险指标快照（不做优化），直接返回 dict。
    可用于摘要展示，无最低数据量要求（< 60 行时降级为可用指标）。
    """
    fin_cfg = cfg.get("finance", {})
    symbols = []
    for item in fin_cfg.get("a_shares", []):
        symbols.append(item["code"])
    for item in fin_cfg.get("us_stocks", []):
        symbols.append(item["symbol"])
    for item in fin_cfg.get("crypto", []):
        symbols.append(item["symbol"].replace("/", ""))

    returns = _load_returns(store, symbols, min_rows=5)
    if returns.empty:
        return {}

    report = {}
    for col in returns.columns:
        r = returns[col].dropna()
        if len(r) < 3:
            continue
        vol   = float(r.std() * np.sqrt(252))
        cum   = (1 + r).cumprod()
        dd    = float((cum / cum.cummax() - 1).min())
        total = float(cum.iloc[-1] - 1)
        report[col] = {"vol_ann": round(vol, 4), "max_dd": round(dd, 4), "total_ret": round(total, 4), "n_days": len(r)}

    return report
