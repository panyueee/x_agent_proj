#!/usr/bin/env python3
"""
各国股票市场日线数据每日增量更新（A股/港股/美股）。

对 data/stock_history/{a,hk,us}/ 下已有的每只股票，拉取最近 N 天(默认7，覆盖节假日缺口)，
合并进原 parquet，按 date 去重(保留最新)、排序。用 yfinance 批量下载(每批~120只)提速。

设计为每日 cron 调用（务必 .venv/bin/python）：
  .venv/bin/python scripts/daily_update.py                # 全市场增量
  .venv/bin/python scripts/daily_update.py --market us --days 5
  .venv/bin/python scripts/daily_update.py --market a --limit 20   # 测试

ticker 映射：A股 sh.600000->600000.SS / sz.000001->000001.SZ；港股/美股文件名即 yahoo ticker。
"""
from __future__ import annotations
import argparse, os, sys
from datetime import datetime
from pathlib import Path
import pandas as pd

os.environ["SSL_CERT_FILE"] = __import__("certifi").where()
ROOT = Path(__file__).parent.parent
DATA = ROOT / "data" / "stock_history"
COLS = ["date", "open", "high", "low", "close", "volume", "amount", "adj_close"]
BATCH = 120


def _log(m): print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {m}", flush=True)


def to_ticker(market, stem):
    if market == "a":
        ex, code = stem.split(".")
        return f"{code}.{'SS' if ex == 'sh' else 'SZ'}"
    return stem  # hk: 0700.HK ; us: AAPL —— 文件名即 ticker


def merge_save(path, new):
    """把 new 合并进 path 的 parquet，按 date 去重排序。"""
    if new is None or new.empty:
        return 0
    try:
        old = pd.read_parquet(path)
    except Exception:
        old = pd.DataFrame(columns=COLS + ["symbol", "market"])
    both = pd.concat([old, new], ignore_index=True)
    both = both.dropna(subset=["date"]).drop_duplicates(subset=["date"], keep="last")
    both = both.sort_values("date")
    both.to_parquet(path, index=False)
    return len(new)


def update_market(market, days, limit):
    import yfinance as yf
    d = DATA / market
    if not d.exists():
        _log(f"{market}: 无数据目录，跳过"); return
    files = [f for f in os.listdir(d) if f.endswith(".parquet")]
    if limit:
        files = files[:limit]
    _log(f"{market}: {len(files)} 只，拉最近 {days} 天增量")
    # ticker -> parquet 文件名
    stem2file = {f[:-8]: f for f in files}
    tick2stem = {to_ticker(market, s): s for s in stem2file}
    ticks = list(tick2stem)

    updated = rows_added = 0
    for i in range(0, len(ticks), BATCH):
        batch = ticks[i:i + BATCH]
        try:
            df = yf.download(batch, period=f"{days}d", group_by="ticker",
                             auto_adjust=False, threads=True, progress=False)
        except Exception as e:
            _log(f"  批 {i}-{i+len(batch)} 失败: {str(e)[:60]}"); continue
        for tk in batch:
            try:
                sub = df[tk] if len(batch) > 1 else df
                sub = sub.reset_index()
                sub.columns = [c[0] if isinstance(c, tuple) else c for c in sub.columns]
                sub = sub.rename(columns={"Date": "date", "Open": "open", "High": "high",
                                          "Low": "low", "Close": "close", "Adj Close": "adj_close",
                                          "Volume": "volume"})
                sub = sub.dropna(subset=["close"])
                if sub.empty:
                    continue
                sub["date"] = pd.to_datetime(sub["date"]).dt.strftime("%Y-%m-%d")
                stem = tick2stem[tk]
                sub["symbol"] = stem; sub["market"] = market
                for c in COLS:
                    if c not in sub.columns:
                        sub[c] = pd.NA
                n = merge_save(d / stem2file[stem], sub[COLS + ["symbol", "market"]])
                if n:
                    updated += 1; rows_added += n
            except Exception:
                continue
        _log(f"  {market}: 已处理 {min(i+BATCH,len(ticks))}/{len(ticks)}，更新 {updated} 只")
    _log(f"{market} 完成：更新 {updated} 只，合并 {rows_added} 行（含重复去重前）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=["a", "hk", "us", "all"], default="all")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    _log(f"=== 每日增量更新开始 (market={args.market}, days={args.days}) ===")
    for m in (["a", "hk", "us"] if args.market == "all" else [args.market]):
        try:
            update_market(m, args.days, args.limit)
        except Exception as e:
            _log(f"{m} 市场异常: {str(e)[:80]}")
    _log("=== 每日增量更新结束 ===")


if __name__ == "__main__":
    main()
