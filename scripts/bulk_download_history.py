#!/usr/bin/env python3
"""
全量历史股票数据下载：A股 / 港股 / 美股，2000-01-01 至今，日线。

数据源（针对本机网络实测可用；eastmoney push2/push2his 在此环境被墙、本地代理间歇性抽风）：
  - A股：baostock —— 代码表 query_stock_basic(含退市股, type=1)，历史前复权 adjustflag=2
  - 港股：yfinance（<code>.HK），代码表 akshare.stock_hk_spot（sina）
  - 美股：yfinance，代码表 nasdaqtrader.com 公共符号目录

设计要点（无人值守过夜）：
  - 所有网络调用带重试退避；代码表抓到后缓存到磁盘只抓一次（之后续传不再抓表）
  - 每只一个 parquet，断点续传（已存在且非空跳过）；空/退市无数据落空占位避免反复重试
  - 单市场列表失败不拖垮其他市场

存储：data/stock_history/{a|hk|us}/<symbol>.parquet
统一列：date, open, high, low, close, volume, amount, adj_close, symbol, market

用法（务必 .venv/bin/python）：
  .venv/bin/python scripts/bulk_download_history.py --market all
  .venv/bin/python scripts/bulk_download_history.py --market a --limit 5   # 小样
  .venv/bin/python scripts/bulk_download_history.py --status
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data" / "stock_history"
START_DEFAULT = "2000-01-01"
TODAY = datetime.now().strftime("%Y-%m-%d")
COLS = ["date", "open", "high", "low", "close", "volume", "amount", "adj_close"]


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


def _out_path(market: str, symbol: str) -> Path:
    safe = symbol.replace("/", "_").replace("\\", "_")
    return DATA_DIR / market / f"{safe}.parquet"


def _already_done(market: str, symbol: str) -> bool:
    p = _out_path(market, symbol)
    if p.exists():
        return True
    return p.with_suffix(".csv.gz").exists()


def _save(market: str, symbol: str, df: pd.DataFrame) -> int:
    p = _out_path(market, symbol)
    p.parent.mkdir(parents=True, exist_ok=True)
    df = df.copy()
    df["symbol"] = symbol
    df["market"] = market
    for c in COLS:
        if c not in df.columns:
            df[c] = pd.NA
    df = df[COLS + ["symbol", "market"]]
    try:
        df.to_parquet(p, index=False)
    except Exception:
        p = p.with_suffix(".csv.gz")
        df.to_csv(p, index=False, compression="gzip")
    return len(df)


# ── 代码表（带磁盘缓存）──────────────────────────────────────────────────────

def _symbols_cached(market: str, fetch_fn) -> list[str]:
    cache = DATA_DIR / f"_symbols_{market}.txt"
    if cache.exists() and cache.stat().st_size > 0:
        syms = [s.strip() for s in cache.read_text().splitlines() if s.strip()]
        _log(f"[{market}] 用缓存代码表：{len(syms)} 只（{cache.name}）")
        return syms
    syms = fetch_fn()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("\n".join(syms))
    _log(f"[{market}] 抓取代码表：{len(syms)} 只，已缓存 {cache.name}")
    return syms


def fetch_symbols_a(bs) -> list[str]:
    def _do():
        rs = bs.query_stock_basic()
        out = []
        while rs.error_code == "0" and rs.next():
            code, name, ipo, out_date, typ, status = rs.get_row_data()
            if typ == "1":   # 1=股票（含已退市，full 全量）；排除指数(2)/可转债等
                out.append(code)
        if not out:
            raise RuntimeError("query_stock_basic 返回空")
        return out
    return _retry(_do, what="A股代码表")


def fetch_symbols_hk() -> list[str]:
    def _do():
        import akshare as ak
        df = ak.stock_hk_spot()
        col = "代码" if "代码" in df.columns else df.columns[1]
        out = set()
        for code in df[col].astype(str):
            digits = "".join(ch for ch in code if ch.isdigit())
            if digits:
                out.add(f"{int(digits):04d}.HK")
        if not out:
            raise RuntimeError("stock_hk_spot 返回空")
        return sorted(out)
    return _retry(_do, what="港股代码表")


def fetch_symbols_us() -> list[str]:
    def _do():
        import requests
        syms: set[str] = set()
        for fname, sym_col in [("nasdaqlisted.txt", "Symbol"), ("otherlisted.txt", "ACT Symbol")]:
            r = requests.get(f"https://www.nasdaqtrader.com/dynamic/symdir/{fname}", timeout=30)
            r.raise_for_status()
            lines = r.text.splitlines()
            header = lines[0].split("|")
            idx = header.index(sym_col)
            test_idx = header.index("Test Issue") if "Test Issue" in header else None
            for line in lines[1:]:
                if line.startswith("File Creation Time"):
                    continue
                parts = line.split("|")
                if len(parts) <= idx:
                    continue
                sym = parts[idx].strip()
                if not sym or sym in ("Symbol", "ACT Symbol"):
                    continue
                if test_idx is not None and len(parts) > test_idx and parts[test_idx].strip() == "Y":
                    continue
                syms.add(sym.replace(".", "-"))
        if not syms:
            raise RuntimeError("nasdaqtrader 返回空")
        return sorted(syms)
    return _retry(_do, what="美股代码表")


# ── 下载单只（带重试）────────────────────────────────────────────────────────

def fetch_a(bs, symbol: str, start: str) -> pd.DataFrame | None:
    def _do():
        rs = bs.query_history_k_data_plus(
            symbol, "date,open,high,low,close,volume,amount",
            start_date=start, end_date=TODAY, frequency="d", adjustflag="2")
        if rs.error_code != "0":
            raise RuntimeError(f"baostock err {rs.error_code}: {rs.error_msg}")
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        return rows
    rows = _retry(_do, tries=4, base=1.5, what=symbol)
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume", "amount"])
    for c in ["open", "high", "low", "close", "volume", "amount"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def fetch_yf(symbol: str, start: str) -> pd.DataFrame | None:
    import yfinance as yf

    def _do():
        return yf.download(symbol, start=start, end=TODAY, progress=False,
                           auto_adjust=False, threads=False)
    df = _retry(_do, tries=4, base=1.5, what=symbol)
    if df is None or df.empty:
        return None
    df = df.reset_index()
    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    df = df.rename(columns={"Date": "date", "Open": "open", "High": "high", "Low": "low",
                            "Close": "close", "Adj Close": "adj_close", "Volume": "volume"})
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    return df


# ── 批量编排 ──────────────────────────────────────────────────────────────────

def run_market(market: str, start: str, limit: int | None, sleep: float) -> None:
    _log(f"=== 市场 {market} 开始（start={start}）===")
    bs = None
    try:
        if market == "a":
            import baostock as bs_mod
            bs = bs_mod
            bs.login()
            syms = _symbols_cached("a", lambda: fetch_symbols_a(bs))
        elif market == "hk":
            syms = _symbols_cached("hk", fetch_symbols_hk)
        elif market == "us":
            syms = _symbols_cached("us", fetch_symbols_us)
        else:
            raise ValueError(market)
    except Exception as e:
        _log(f"!! 市场 {market} 代码表获取失败，跳过（下次重试）：{str(e)[:120]}")
        if bs is not None:
            bs.logout()
        return

    if limit:
        syms = syms[:limit]
    total = len(syms)
    _log(f"市场 {market} 待处理：{total} 只")

    ok = skip = empty = fail = 0
    try:
        for i, sym in enumerate(syms, 1):
            if _already_done(market, sym):
                skip += 1
                continue
            try:
                df = fetch_a(bs, sym, start) if market == "a" else fetch_yf(sym, start)
                if df is None or len(df) == 0:
                    _save(market, sym, pd.DataFrame(columns=COLS))  # 空占位，避免反复重试
                    empty += 1
                else:
                    n = _save(market, sym, df)
                    ok += 1
                    if ok % 50 == 0:
                        _log(f"  [{market}] {i}/{total} OK={ok} SKIP={skip} 空={empty} FAIL={fail} | {sym}:{n}行")
            except Exception as e:
                fail += 1
                if fail % 25 == 0:
                    _log(f"  [{market}] {sym} FAIL({fail}): {str(e)[:80]}")
            time.sleep(sleep)
    finally:
        if bs is not None:
            bs.logout()
    _log(f"=== 市场 {market} 完成：OK={ok} SKIP={skip} 空={empty} FAIL={fail} / 共{total} ===")


def show_status() -> None:
    print(f"数据目录：{DATA_DIR}")
    for m in ("a", "hk", "us"):
        d = DATA_DIR / m
        if not d.exists():
            print(f"  {m}: 0"); continue
        files = list(d.glob("*.parquet")) + list(d.glob("*.csv.gz"))
        nonempty = sum(1 for f in files if f.stat().st_size > 600)
        print(f"  {m}: {len(files)} 文件（含数据约 {nonempty}）")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=["a", "hk", "us", "all"], default="all")
    ap.add_argument("--start", default=START_DEFAULT)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--sleep", type=float, default=0.3)
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    if args.status:
        show_status()
        return

    markets = ["a", "hk", "us"] if args.market == "all" else [args.market]
    for m in markets:
        run_market(m, args.start, args.limit, args.sleep)
    _log("全部市场处理结束")
    show_status()


if __name__ == "__main__":
    main()
