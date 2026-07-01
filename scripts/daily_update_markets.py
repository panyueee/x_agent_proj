#!/usr/bin/env python3
"""
新市场数据每日增量更新：crypto/指数/汇率/债券/ETF/REITs/FRED宏观/可转债。
增量拉最近 N 天 → 合并进各 parquet(按 date 去重保留最新)。配 cron 每日跑。
用法：.venv/bin/python scripts/daily_update_markets.py [--market all|crypto|index|fx|bond|etf|reits|fred|cb] [--days N] [--limit N]
"""
from __future__ import annotations
import argparse, os, threading, time
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd

os.environ["SSL_CERT_FILE"] = __import__("certifi").where()
ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"
TODAY = datetime.now()


def log(m): print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {m}", flush=True)


def call_timeout(fn, timeout=25):
    box = {}
    def run():
        try: box["r"] = fn()
        except Exception as e: box["e"] = e
    t = threading.Thread(target=run, daemon=True); t.start(); t.join(timeout)
    if t.is_alive(): return None
    return box.get("r")


def merge_save(path, new):
    if new is None or len(new) == 0:
        return 0
    try: old = pd.read_parquet(path)
    except Exception: old = pd.DataFrame(columns=list(new.columns))
    both = pd.concat([old, new], ignore_index=True)
    dcol = "date" if "date" in both.columns else both.columns[0]
    both = both.dropna(subset=[dcol]).drop_duplicates(subset=[dcol], keep="last").sort_values(dcol)
    both.to_parquet(path, index=False)
    return len(new)


# ── yfinance 批量类（crypto/index/fx/us债收益率）──────────────────────────────
def update_yf(subdir, days, limit, tick_col="ticker"):
    import yfinance as yf
    d = DATA / subdir
    if not d.exists(): log(f"{subdir}: 无目录"); return
    files = [f for f in os.listdir(d) if f.endswith(".parquet")]
    if limit: files = files[:limit]
    tick2file = {}
    for f in files:
        try:
            df0 = pd.read_parquet(d / f)
            tk = df0[tick_col].iloc[0] if tick_col in df0.columns and len(df0) else f[:-8]
        except Exception: tk = f[:-8]
        tick2file[tk] = f
    ticks = list(tick2file)
    log(f"{subdir}: {len(ticks)} 只，yfinance 拉最近 {days} 天")
    upd = 0
    for i in range(0, len(ticks), 120):
        batch = ticks[i:i+120]
        df = call_timeout(lambda: yf.download(batch, period=f"{days}d", group_by="ticker",
                          auto_adjust=False, threads=True, progress=False), timeout=90)
        if df is None: continue
        for tk in batch:
            try:
                sub = (df[tk] if len(batch) > 1 else df).reset_index()
                sub.columns = [c[0] if isinstance(c, tuple) else c for c in sub.columns]
                sub = sub.rename(columns={"Date":"date","Open":"open","High":"high","Low":"low",
                                          "Close":"close","Adj Close":"adj_close","Volume":"volume"})
                sub = sub.dropna(subset=["close"])
                if sub.empty: continue
                sub["date"] = pd.to_datetime(sub["date"]).dt.strftime("%Y-%m-%d")
                old = pd.read_parquet(d / tick2file[tk])
                for c in old.columns:
                    if c not in sub.columns and c not in ("date","open","high","low","close","adj_close","volume"):
                        sub[c] = old[c].iloc[0] if len(old) else tk
                if merge_save(d / tick2file[tk], sub[[c for c in old.columns if c in sub.columns]]): upd += 1
            except Exception: continue
    log(f"{subdir} 完成：更新 {upd}")


# ── 债券 ──────────────────────────────────────────────────────────────────────
def update_bond(days):
    import akshare as ak
    d = DATA / "bond_history"
    start = (TODAY - timedelta(days=days)).strftime("%Y%m%d")
    df = call_timeout(lambda: ak.bond_zh_us_rate(start_date=start), timeout=40)
    if df is not None and len(df):
        df = df.rename(columns={"日期": "date"}); df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        merge_save(d / "cn_us_treasury_yields.parquet", df); log("bond: 中美国债收益率 已更")
    update_yf("bond_history", days, None)  # us_yield_*.parquet (yfinance ^TNX等)，其中非yf的会被跳过


# ── ETF（新浪）────────────────────────────────────────────────────────────────
def update_etf(days, limit):
    import akshare as ak
    d = DATA / "etf_history"
    if not d.exists(): return
    files = [f for f in os.listdir(d) if f.endswith(".parquet")]
    if limit: files = files[:limit]
    upd = 0
    for f in files:
        code = f.split("_")[0]
        sym = ("sh" if code[0] in "56" else "sz") + code
        df = call_timeout(lambda: ak.fund_etf_hist_sina(symbol=sym), timeout=25)
        if df is None or len(df) == 0: continue
        df = df.rename(columns={"date":"date","open":"open","high":"high","low":"low","close":"close","volume":"volume"})
        df = df.tail(days + 10)
        old = pd.read_parquet(d / f)
        for c in ("symbol","name"):
            if c in old.columns: df[c] = old[c].iloc[0] if len(old) else code
        if merge_save(d / f, df[[c for c in old.columns if c in df.columns]]): upd += 1
        time.sleep(0.2)
    log(f"etf 完成：更新 {upd}")


# ── REITs（新浪 K线）─────────────────────────────────────────────────────────
def update_reits(days, limit):
    d = DATA / "reits_history" / "history"
    if not d.exists(): return
    import requests
    S = requests.Session(); S.trust_env = False
    files = [f for f in os.listdir(d) if f.endswith(".parquet")]
    if limit: files = files[:limit]
    upd = 0
    for f in files:
        sym = f[:-8]  # 如 sh508000
        try:
            url = f"https://finance.sina.com.cn/realstock/company/{sym}/hisdata/klc_kl.js"
            # 用与 download_reits 相同的新浪K线端点（简化：直接读 old，尾部拉不到就跳过）
        except Exception: pass
    log(f"reits: 增量走新浪K线(复用download_reits逻辑); 简化版跳过, 由全量脚本 --resume 补")


# ── FRED（重下 CSV，宏观会修订）──────────────────────────────────────────────
def update_fred(limit):
    import requests
    d = DATA / "macro_history" / "fred"
    if not d.exists(): return
    S = requests.Session()  # 保留代理达 FRED
    files = [f for f in os.listdir(d) if f.endswith(".parquet")]
    if limit: files = files[:limit]
    upd = 0
    for f in files:
        sid = f[:-8]
        try:
            r = S.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}", timeout=30)  # 无自定义UA
            if r.status_code != 200: continue
            import io
            df = pd.read_csv(io.StringIO(r.text))
            df.columns = ["date", "value"] + list(df.columns[2:])
            df["series_id"] = sid
            old = pd.read_parquet(d / f)
            if "name" in old.columns: df["name"] = old["name"].iloc[0] if len(old) else sid
            merge_save(d / f, df[[c for c in old.columns if c in df.columns]]); upd += 1
        except Exception: continue
        time.sleep(0.1)
    log(f"fred 完成：更新 {upd}")


def update_cb(days, limit):
    import akshare as ak
    d = DATA / "cb_history"
    if not d.exists(): return
    log("cb: 可转债增量(best-effort, 新浪单只); 略过复杂枚举, 由全量脚本续传补")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="all",
                    choices=["all","crypto","index","fx","bond","etf","reits","fred","cb"])
    ap.add_argument("--days", type=int, default=15)
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    log(f"=== 新市场每日增量更新 (market={a.market}) ===")
    m = a.market
    if m in ("all","crypto"): update_yf("crypto_history", a.days, a.limit)
    if m in ("all","index"):  update_yf("index_history", a.days, a.limit)
    if m in ("all","fx"):     update_yf("fx_history", a.days, a.limit)
    if m in ("all","bond"):   update_bond(a.days)
    if m in ("all","etf"):    update_etf(a.days, a.limit)
    if m in ("all","fred"):   update_fred(a.limit)
    if m in ("all","reits"):  update_reits(a.days, a.limit)
    if m in ("all","cb"):     update_cb(a.days, a.limit)
    log("=== 完成 ===")


if __name__ == "__main__":
    main()
