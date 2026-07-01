#!/usr/bin/env python3
"""
加密货币全量历史行情下载：主流币种，2000-01-01 至今，日线（Yahoo 实际从各币上市日起）。

数据源（本机实测可用；需 shell 代理 http_proxy=127.0.0.1:1089）：
  - yfinance，Yahoo 加密符号 <COIN>-USD

设计要点（对齐 scripts/bulk_download_history.py）：
  - 所有网络调用带重试退避 _retry
  - 每只一个 parquet，断点续传（已存在且非空跳过）
  - 空帧直接丢弃（不落占位）；UNI 特殊：UNI7083-USD 空则回退 UNI-USD
  - _log 时间戳日志；--status 列出已存在 parquet 的行数与日期范围

存储：data/crypto_history/<safe_ticker>.parquet
统一列（英文）：date, open, high, low, close, adj_close, volume, ticker, name

用法（务必 .venv/bin/python）：
  .venv/bin/python scripts/download_crypto.py
  .venv/bin/python scripts/download_crypto.py --status
"""
from __future__ import annotations

# SSL 证书（Python 3.14 需显式指向 certifi）
import os
import certifi
os.environ["SSL_CERT_FILE"] = certifi.where()

import argparse
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data" / "crypto_history"
START_DEFAULT = "2000-01-01"
TODAY = datetime.now().strftime("%Y-%m-%d")
COLS = ["date", "open", "high", "low", "close", "adj_close", "volume", "ticker", "name"]

# 币种 -> 名称；顺序即下载顺序
COIN_NAMES: dict[str, str] = {
    "BTC-USD": "Bitcoin",
    "ETH-USD": "Ethereum",
    "BNB-USD": "BNB",
    "SOL-USD": "Solana",
    "XRP-USD": "XRP",
    "ADA-USD": "Cardano",
    "DOGE-USD": "Dogecoin",
    "TRX-USD": "TRON",
    "DOT-USD": "Polkadot",
    "MATIC-USD": "Polygon",
    "LTC-USD": "Litecoin",
    "BCH-USD": "Bitcoin Cash",
    "LINK-USD": "Chainlink",
    "AVAX-USD": "Avalanche",
    "XLM-USD": "Stellar",
    "ATOM-USD": "Cosmos",
    "ETC-USD": "Ethereum Classic",
    "UNI7083-USD": "Uniswap",
    "FIL-USD": "Filecoin",
    "APT-USD": "Aptos",
}
# UNI 主符号为空时的回退符号（同一 name）
UNI_FALLBACK = "UNI-USD"


def _log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _retry(fn, tries: int = 4, base: float = 1.5, what: str = ""):
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
    return ticker.replace("/", "_").replace("\\", "_")


def _out_path(ticker: str) -> Path:
    return DATA_DIR / f"{_safe(ticker)}.parquet"


def _already_done(ticker: str) -> bool:
    p = _out_path(ticker)
    # 断点续传：已存在且非空（>600 字节，排除空占位/损坏）
    return p.exists() and p.stat().st_size > 600


def fetch_yf(ticker: str, start: str) -> pd.DataFrame | None:
    """下载单只加密币，返回规范化 DataFrame；空则 None。"""
    import yfinance as yf

    def _do():
        return yf.download(ticker, start=start, end=TODAY, progress=False,
                           auto_adjust=False, threads=False)
    df = _retry(_do, tries=4, base=1.5, what=ticker)
    if df is None or df.empty:
        return None
    df = df.reset_index()
    # 拍平可能的 MultiIndex 列
    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    df = df.rename(columns={"Date": "date", "Datetime": "date",
                            "Open": "open", "High": "high", "Low": "low",
                            "Close": "close", "Adj Close": "adj_close",
                            "Volume": "volume"})
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    return df


def _save(ticker: str, name: str, df: pd.DataFrame) -> int:
    p = _out_path(ticker)
    p.parent.mkdir(parents=True, exist_ok=True)
    df = df.copy()
    df["ticker"] = ticker
    df["name"] = name
    for c in COLS:
        if c not in df.columns:
            df[c] = pd.NA
    df = df[COLS]
    df.to_parquet(p, index=False)
    return len(df)


def run(start: str, sleep: float) -> None:
    _log(f"=== 加密币下载开始（start={start} end={TODAY}）===")
    total = len(COIN_NAMES)
    ok = skip = dropped = fail = 0
    dropped_list: list[str] = []
    for i, (ticker, name) in enumerate(COIN_NAMES.items(), 1):
        if _already_done(ticker):
            skip += 1
            _log(f"  [{i}/{total}] SKIP {ticker}（已存在）")
            continue
        try:
            df = fetch_yf(ticker, start)
            # UNI 特殊回退
            if (df is None or df.empty) and ticker == "UNI7083-USD":
                _log(f"  {ticker} 空，回退 {UNI_FALLBACK}")
                df = fetch_yf(UNI_FALLBACK, start)
            if df is None or df.empty:
                dropped += 1
                dropped_list.append(f"{ticker}({name}): 空帧")
                _log(f"  [{i}/{total}] DROP {ticker}（空帧）")
            else:
                n = _save(ticker, name, df)
                ok += 1
                _log(f"  [{i}/{total}] OK   {ticker} -> {n} 行（{df['date'].iloc[0]}~{df['date'].iloc[-1]}）")
        except Exception as e:  # noqa: BLE001
            fail += 1
            dropped_list.append(f"{ticker}({name}): 异常 {str(e)[:80]}")
            _log(f"  [{i}/{total}] FAIL {ticker}: {str(e)[:100]}")
        time.sleep(sleep)
    _log(f"=== 完成：OK={ok} SKIP={skip} DROP={dropped} FAIL={fail} / 共{total} ===")
    if dropped_list:
        _log("丢弃/失败清单：")
        for d in dropped_list:
            _log(f"    - {d}")


def show_status() -> None:
    print(f"数据目录：{DATA_DIR}")
    if not DATA_DIR.exists():
        print("  （不存在）")
        return
    files = sorted(DATA_DIR.glob("*.parquet"))
    if not files:
        print("  （无 parquet）")
        return
    for f in files:
        try:
            df = pd.read_parquet(f, columns=["date"])
            n = len(df)
            rng = f"{df['date'].iloc[0]} ~ {df['date'].iloc[-1]}" if n else "空"
        except Exception as e:  # noqa: BLE001
            n, rng = -1, f"读取失败 {str(e)[:40]}"
        print(f"  {f.name:22s} {n:6d} 行  {rng}")
    print(f"共 {len(files)} 个文件")


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
