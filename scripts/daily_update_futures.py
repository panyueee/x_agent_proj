#!/usr/bin/env python3
"""
期货主力连续合约日线每日增量更新（国内 akshare + 全球 yfinance）。

对 data/futures_history/{cn,global}/ 下已有的每个品种，拉最近 N 天(默认20，覆盖节假日)，
合并进原 parquet，按 date 去重(保留最新)、排序。品种/ticker 从 parquet 内的列读取，稳。

配 cron 每日调用（务必 .venv/bin/python）：
  .venv/bin/python scripts/daily_update_futures.py
  .venv/bin/python scripts/daily_update_futures.py --market cn --limit 5
"""
from __future__ import annotations
import argparse, os
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd

os.environ["SSL_CERT_FILE"] = __import__("certifi").where()
ROOT = Path(__file__).parent.parent
FDATA = ROOT / "data" / "futures_history"
TODAY = datetime.now()
BATCH = 60


def _log(m): print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {m}", flush=True)


def merge_save(path, new, keys):
    if new is None or new.empty:
        return 0
    try:
        old = pd.read_parquet(path)
    except Exception:
        old = pd.DataFrame(columns=list(new.columns))
    both = pd.concat([old, new], ignore_index=True)
    both = both.dropna(subset=["date"]).drop_duplicates(subset=["date"], keep="last").sort_values("date")
    both.to_parquet(path, index=False)
    return len(new)


def update_cn(days, limit):
    import akshare as ak
    d = FDATA / "cn"
    if not d.exists():
        _log("cn: 无目录"); return
    files = [f for f in os.listdir(d) if f.endswith(".parquet")]
    if limit: files = files[:limit]
    start = (TODAY - timedelta(days=days)).strftime("%Y%m%d")
    end = TODAY.strftime("%Y%m%d")
    _log(f"cn: {len(files)} 品种，akshare 拉 {start}~{end}")
    upd = 0
    ren = {"日期": "date", "开盘价": "open", "最高价": "high", "最低价": "low",
           "收盘价": "close", "成交量": "volume", "持仓量": "open_interest", "动态结算价": "settle"}
    for i, f in enumerate(files, 1):
        sym = f[:-8]
        try:
            h = ak.futures_main_sina(symbol=sym, start_date=start, end_date=end)
            if h is None or h.empty:
                continue
            h = h.rename(columns=ren)
            h["date"] = pd.to_datetime(h["date"]).dt.strftime("%Y-%m-%d")
            # 补齐原 parquet 的静态列（symbol/name/exchange/category）
            old = pd.read_parquet(d / f)
            for col in ("symbol", "name", "exchange", "category"):
                if col in old.columns:
                    h[col] = old[col].iloc[0] if len(old) else sym
            cols = [c for c in old.columns if c in h.columns]
            if merge_save(d / f, h[cols], ["date"]):
                upd += 1
        except Exception:
            continue
        if i % 20 == 0:
            _log(f"  cn {i}/{len(files)}，更新 {upd}")
    _log(f"cn 完成：更新 {upd} 品种")


def update_global(days, limit):
    import yfinance as yf
    d = FDATA / "global"
    if not d.exists():
        _log("global: 无目录"); return
    files = [f for f in os.listdir(d) if f.endswith(".parquet")]
    if limit: files = files[:limit]
    # ticker 从 parquet 内列读取，回退用文件名反推(CL_F->CL=F)
    tick2file, meta = {}, {}
    for f in files:
        try:
            df0 = pd.read_parquet(d / f, columns=None)
            tk = df0["ticker"].iloc[0] if "ticker" in df0.columns and len(df0) else None
        except Exception:
            tk = None
        if not tk:
            stem = f[:-8]
            tk = stem[:-2] + "=F" if stem.endswith("_F") else stem
        tick2file[tk] = f
    ticks = list(tick2file)
    _log(f"global: {len(ticks)} 品种，yfinance 拉最近 {days} 天")
    upd = 0
    for i in range(0, len(ticks), BATCH):
        batch = ticks[i:i + BATCH]
        try:
            df = yf.download(batch, period=f"{days}d", group_by="ticker",
                             auto_adjust=False, threads=True, progress=False)
        except Exception as e:
            _log(f"  批失败: {str(e)[:50]}"); continue
        for tk in batch:
            try:
                sub = (df[tk] if len(batch) > 1 else df).reset_index()
                sub.columns = [c[0] if isinstance(c, tuple) else c for c in sub.columns]
                sub = sub.rename(columns={"Date": "date", "Open": "open", "High": "high", "Low": "low",
                                          "Close": "close", "Adj Close": "adj_close", "Volume": "volume"})
                sub = sub.dropna(subset=["close"])
                if sub.empty:
                    continue
                sub["date"] = pd.to_datetime(sub["date"]).dt.strftime("%Y-%m-%d")
                path = d / tick2file[tk]
                old = pd.read_parquet(path)
                for col in ("ticker", "name", "category"):
                    if col in old.columns:
                        sub[col] = old[col].iloc[0] if len(old) else tk
                cols = [c for c in old.columns if c in sub.columns]
                if merge_save(path, sub[cols], ["date"]):
                    upd += 1
            except Exception:
                continue
    _log(f"global 完成：更新 {upd} 品种")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=["cn", "global", "all"], default="all")
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    _log(f"=== 期货每日增量更新开始 (market={args.market}) ===")
    if args.market in ("cn", "all"):
        try: update_cn(args.days, args.limit)
        except Exception as e: _log(f"cn 异常: {str(e)[:80]}")
    if args.market in ("global", "all"):
        try: update_global(args.days, args.limit)
        except Exception as e: _log(f"global 异常: {str(e)[:80]}")
    _log("=== 期货每日增量更新结束 ===")


if __name__ == "__main__":
    main()
