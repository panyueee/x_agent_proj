#!/usr/bin/env python3
"""
全球主要股指 + VIX 恐慌指数历史数据下载：2000-01-01 至今，日线。

数据源：yfinance（本机实测可用，走 shell 代理 http_proxy=127.0.0.1:1089）。

设计要点（对齐 bulk_download_history.py）：
  - 网络调用带指数退避重试（_retry）
  - 每个指数一个 parquet，断点续传（已存在且非空跳过）
  - 时间戳日志（_log）
  - --status 模式列出已下载文件的行数与日期范围

存储：data/index_history/<safe_ticker>.parquet
统一列：date, open, high, low, close, adj_close, volume, ticker, name, category

用法（务必 .venv/bin/python）：
  .venv/bin/python scripts/download_indices.py
  .venv/bin/python scripts/download_indices.py --status
"""
from __future__ import annotations

# SSL 证书（放最顶端，先于任何联网库导入）
import os
import certifi
os.environ["SSL_CERT_FILE"] = certifi.where()

import argparse
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data" / "index_history"
START_DEFAULT = "2000-01-01"
TODAY = datetime.now().strftime("%Y-%m-%d")
COLS = ["date", "open", "high", "low", "close", "adj_close", "volume",
        "ticker", "name", "category"]

# ticker -> 中文名（category 一律 "index"）
INDICES: dict[str, str] = {
    "^GSPC": "标普500",
    "^DJI": "道指",
    "^IXIC": "纳指",
    "^RUT": "罗素2000",
    "^VIX": "VIX恐慌指数",
    "^HSI": "恒生",
    "^HSCE": "国企指数",
    "^N225": "日经225",
    "^KS11": "韩国综合",
    "^FTSE": "富时100",
    "^GDAXI": "德国DAX",
    "^FCHI": "法国CAC",
    "^STOXX50E": "欧洲斯托克50",
    "000001.SS": "上证指数",
    "399001.SZ": "深证成指",
    "399006.SZ": "创业板指",
    "000300.SS": "沪深300",
    "000905.SS": "中证500",
    "000016.SS": "上证50",
    "000688.SS": "科创50",
    "^NSEI": "印度Nifty",
}


def _log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _retry(fn, tries: int = 5, base: float = 2.0, what: str = ""):
    """带指数退避的重试；全失败则抛最后一次异常。"""
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            if i < tries - 1:
                time.sleep(base * (2 ** i))
    raise last if last else RuntimeError(f"retry failed: {what}")


def _safe(ticker: str) -> str:
    """把 ticker 转成安全文件名：^GSPC -> GSPC，000001.SS -> 000001_SS。"""
    s = ticker.lstrip("^")
    for ch in ".^/\\ ":
        s = s.replace(ch, "_")
    return s.strip("_")


def _out_path(ticker: str) -> Path:
    return DATA_DIR / f"{_safe(ticker)}.parquet"


def _already_done(ticker: str) -> bool:
    p = _out_path(ticker)
    return p.exists() and p.stat().st_size > 600


def fetch_yf(ticker: str, start: str) -> pd.DataFrame | None:
    import yfinance as yf

    def _do():
        return yf.download(ticker, start=start, end=TODAY, progress=False,
                           auto_adjust=False, threads=False)
    df = _retry(_do, tries=4, base=1.5, what=ticker)
    if df is None or df.empty:
        return None
    df = df.reset_index()
    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    df = df.rename(columns={"Date": "date", "Open": "open", "High": "high",
                            "Low": "low", "Close": "close",
                            "Adj Close": "adj_close", "Volume": "volume"})
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    return df


def _save(ticker: str, df: pd.DataFrame) -> int:
    p = _out_path(ticker)
    p.parent.mkdir(parents=True, exist_ok=True)
    df = df.copy()
    df["ticker"] = ticker
    df["name"] = INDICES[ticker]
    df["category"] = "index"
    for c in COLS:
        if c not in df.columns:
            df[c] = pd.NA
    df = df[COLS]
    df.to_parquet(p, index=False)
    return len(df)


def run(start: str, sleep: float) -> None:
    _log(f"=== 指数下载开始（start={start} end={TODAY}，共 {len(INDICES)} 个）===")
    ok = skip = empty = fail = 0
    dropped: list[tuple[str, str, str]] = []
    total = len(INDICES)
    for i, (ticker, name) in enumerate(INDICES.items(), 1):
        if _already_done(ticker):
            skip += 1
            _log(f"  [{i}/{total}] SKIP 已存在 {ticker}（{name}）")
            continue
        try:
            df = fetch_yf(ticker, start)
            if df is None or len(df) == 0:
                empty += 1
                dropped.append((ticker, name, "空数据"))
                _log(f"  [{i}/{total}] 空 {ticker}（{name}）—— 丢弃")
            else:
                n = _save(ticker, df)
                ok += 1
                _log(f"  [{i}/{total}] OK {ticker}（{name}）：{n} 行")
        except Exception as e:  # noqa: BLE001
            fail += 1
            dropped.append((ticker, name, f"异常: {str(e)[:80]}"))
            _log(f"  [{i}/{total}] FAIL {ticker}（{name}）：{str(e)[:120]}")
        time.sleep(sleep)
    _log(f"=== 完成：OK={ok} SKIP={skip} 空={empty} FAIL={fail} / 共{total} ===")
    if dropped:
        _log("丢弃清单：")
        for t, nm, why in dropped:
            _log(f"    - {t}（{nm}）：{why}")


def show_status() -> None:
    print(f"数据目录：{DATA_DIR}")
    if not DATA_DIR.exists():
        print("  （空）")
        return
    files = sorted(DATA_DIR.glob("*.parquet"))
    if not files:
        print("  （无 parquet 文件）")
        return
    for f in files:
        try:
            df = pd.read_parquet(f, columns=["date", "ticker", "name"])
            n = len(df)
            if n:
                d0, d1 = df["date"].min(), df["date"].max()
                tk = df["ticker"].iloc[0]
                nm = df["name"].iloc[0]
                print(f"  {f.name:20s} {tk:12s} {nm:12s} {n:6d} 行  {d0} ~ {d1}")
            else:
                print(f"  {f.name:20s} 空")
        except Exception as e:  # noqa: BLE001
            print(f"  {f.name:20s} 读取失败：{str(e)[:60]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=START_DEFAULT)
    ap.add_argument("--sleep", type=float, default=0.5)
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    if args.status:
        show_status()
        return

    run(args.start, args.sleep)
    show_status()


if __name__ == "__main__":
    main()
