#!/usr/bin/env python3
"""
补齐 A 股缺失部分：baostock 下到一定量后会限流/挂起，剩余股票改用 yfinance(.SS/.SZ, 走 yahoo)。
文件名沿用 baostock 符号(sh.600019.parquet)、schema 统一，与已下的无缝衔接、断点续传。
用法：.venv/bin/python scripts/fill_a_shares_yf.py
"""
import os, sys, time
from datetime import datetime
from pathlib import Path
import pandas as pd

os.environ["SSL_CERT_FILE"] = __import__("certifi").where()
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
ADIR = ROOT / "data" / "stock_history" / "a"
COLS = ["date", "open", "high", "low", "close", "volume", "amount", "adj_close"]
TODAY = datetime.now().strftime("%Y-%m-%d")


def _log(m): print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


def yf_symbol(bs_sym):   # sh.600019 -> 600019.SS ; sz.000001 -> 000001.SZ
    ex, code = bs_sym.split(".")
    return f"{code}.{'SS' if ex == 'sh' else 'SZ'}"


def fetch(bs_sym, yf):
    for attempt in range(3):
        try:
            df = yf.download(yf_symbol(bs_sym), start="2000-01-01", end=TODAY,
                             progress=False, auto_adjust=False, threads=False)
            if df is not None and not df.empty:
                df = df.reset_index()
                df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
                df = df.rename(columns={"Date": "date", "Open": "open", "High": "high",
                                        "Low": "low", "Close": "close", "Adj Close": "adj_close",
                                        "Volume": "volume"})
                df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
                return df
            return None
        except Exception:
            time.sleep(1.5 * (2 ** attempt))
    return None


def save(bs_sym, df):
    p = ADIR / f"{bs_sym}.parquet"
    if df is None or len(df) == 0:
        df = pd.DataFrame(columns=COLS)
    df = df.copy()
    df["symbol"] = bs_sym; df["market"] = "a"
    for c in COLS:
        if c not in df.columns:
            df[c] = pd.NA
    df[COLS + ["symbol", "market"]].to_parquet(p, index=False)
    return len(df)


def main():
    import yfinance as yf
    syms = [s.strip() for s in open(ROOT / "data/stock_history/_symbols_a.txt") if s.strip()]
    done = set(f[:-8] for f in os.listdir(ADIR))
    undone = [s for s in syms if s not in done]
    _log(f"A股缺 {len(undone)} 只，改用 yfinance 补齐")
    ok = empty = 0
    for i, s in enumerate(undone, 1):
        try:
            df = fetch(s, yf)
            n = save(s, df)
            if n > 0: ok += 1
            else: empty += 1
            if i % 50 == 0:
                _log(f"  {i}/{len(undone)} OK={ok} 空={empty} | {s}:{n}行")
        except Exception as e:
            empty += 1
            _log(f"  {s} 失败: {str(e)[:60]}")
        time.sleep(0.25)
    total = len(os.listdir(ADIR))
    _log(f"完成：新增有数据 {ok}，空 {empty}。A股 parquet 总数 {total}/{len(syms)}")


if __name__ == "__main__":
    main()
