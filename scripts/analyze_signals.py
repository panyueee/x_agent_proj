"""信号质量分析脚本（alphalens-reloaded）。

评估 classifier.py 打出的信号分数对后续价格走势的预测力（IC / IR）。

用法：
    python scripts/analyze_signals.py [--days 30] [--output signals_report.html]

输出：
    - 各 ticker 信号 IC（信息系数）均值 / IR
    - 分位数收益分析
    - HTML 报告（可选）
"""
from __future__ import annotations

import argparse
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pandas as pd
import numpy as np

from x_agent.storage import Store


def load_signal_factor(store, lookback_days: int = 30) -> pd.DataFrame:
    """
    从 DB 读取信号数据，构造 alphalens 所需的 factor DataFrame。
    格式：MultiIndex (date, asset)，值为信号分数。
    """
    since = (pd.Timestamp.utcnow() - pd.Timedelta(days=lookback_days)).isoformat()
    rows = store.conn.execute(
        "SELECT t.created_at, s.tickers, s.score "
        "FROM signals s JOIN tweets t ON t.id = s.tweet_id "
        "WHERE t.created_at >= ? AND s.score >= 2 AND s.tickers != '[]'",
        (since,),
    ).fetchall()

    records = []
    for created_at, tickers_json, score in rows:
        date = created_at[:10] if created_at else None
        if not date:
            continue
        try:
            tickers = json.loads(tickers_json or "[]")
        except Exception:
            continue
        for tk in tickers:
            records.append({"date": date, "asset": tk.lstrip("$").upper(), "factor": float(score)})

    if not records:
        print("没有足够的信号数据")
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])
    # 同一天同一资产取最高分
    df = df.groupby(["date", "asset"])["factor"].max().reset_index()
    df = df.set_index(["date", "asset"])
    return df["factor"]


def load_price_returns(store, symbols: list[str]) -> pd.DataFrame:
    """
    从 price_bars 表读取价格，计算日收益率。
    格式：index=date，columns=asset。
    """
    frames = {}
    for sym in symbols:
        rows = store.conn.execute(
            "SELECT timestamp, close FROM price_bars WHERE symbol=? ORDER BY timestamp",
            (sym,),
        ).fetchall()
        if len(rows) < 3:
            continue
        s = pd.Series({r[0][:10]: r[1] for r in rows}, name=sym)
        frames[sym] = s

    if not frames:
        return pd.DataFrame()

    prices = pd.DataFrame(frames)
    prices.index = pd.to_datetime(prices.index)
    returns = prices.pct_change().dropna(how="all")
    return returns


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days",   type=int, default=30, help="回溯天数")
    parser.add_argument("--output", type=str, default="",  help="HTML 报告路径（可选）")
    parser.add_argument("--db",     type=str, default="./output/x_agent.db")
    args = parser.parse_args()

    store = Store(args.db)
    factor = load_signal_factor(store, lookback_days=args.days)
    if factor.empty:
        print("无可用信号，退出")
        return

    assets = factor.index.get_level_values("asset").unique().tolist()
    print(f"发现信号资产 {len(assets)} 个：{assets[:10]}")

    returns = load_price_returns(store, assets)
    if returns.empty:
        print("DB 里没有这些资产的价格历史，无法计算 IC")
        print("\n信号分布统计：")
        print(factor.describe())
        print("\n各资产信号频次：")
        print(factor.groupby(level="asset").count().sort_values(ascending=False).head(20))
        return

    # 尝试用 alphalens 做 IC 分析
    try:
        import alphalens
        factor_data = alphalens.utils.get_clean_factor_and_forward_returns(
            factor,
            returns,
            quantiles=4,
            periods=(1, 5),
        )
        ic = alphalens.performance.factor_information_coefficient(factor_data)
        print("\n=== 信号 IC 分析 ===")
        print(f"IC 均值（1日）: {ic['1D'].mean():.4f}")
        print(f"IC 均值（5日）: {ic['5D'].mean():.4f}")
        print(f"IR（1日）: {ic['1D'].mean() / ic['1D'].std():.4f}" if ic['1D'].std() > 0 else "IR: N/A")

        if args.output:
            alphalens.tears.create_full_tear_sheet(factor_data, output_notebook=False)
            print(f"报告已生成（Notebook 格式，需 Jupyter 打开）")
        else:
            print("\n分位数收益（平均）：")
            mean_ret, _ = alphalens.performance.mean_return_by_quantile(factor_data)
            print(mean_ret)

    except Exception as e:
        print(f"alphalens 分析失败（可能价格数据不足）: {e}")
        print("\n基础信号统计：")
        print(factor.describe())
        print("\n资产维度：")
        print(factor.groupby(level="asset").agg(["count", "mean", "std"]).sort_values("mean", ascending=False).head(20))


if __name__ == "__main__":
    main()
