#!/usr/bin/env python3
"""
全球期货全量历史下载：③商品期货 + ④股指期货，2000-01-01 至今，日线。

数据源：yfinance（Yahoo Finance 连续合约代码，形如 CL=F / GC=F / ES=F）。
本机需走本地代理（http_proxy/https_proxy 已在 shell 环境设好）；yahoo 走代理正常。

设计要点（无人值守，可续传）：
  - 硬编码代码表 {ticker: (中文名, 类别)}，类别为 commodity / index
  - 每个品种一个 parquet，断点续传（已存在且非空跳过）
  - 单只网络调用带指数退避重试；无数据落空占位（避免反复重试）
  - 单只失败不拖垮整体

存储：data/futures_history/global/<ticker_sanitized>.parquet
      文件名把 '=' 等特殊字符替换为 '_'（如 CL=F -> CL_F.parquet）
统一列：date, open, high, low, close, adj_close, volume, ticker, name, category

用法（务必 .venv/bin/python）：
  .venv/bin/python scripts/download_futures_global.py
  .venv/bin/python scripts/download_futures_global.py --limit 3   # 小样
  .venv/bin/python scripts/download_futures_global.py --status
"""
from __future__ import annotations

import argparse
import re
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data" / "futures_history" / "global"
START_DEFAULT = "2000-01-01"
TODAY = datetime.now().strftime("%Y-%m-%d")
COLS = ["date", "open", "high", "low", "close", "adj_close", "volume"]

# 全球期货连续合约代码表：ticker -> (中文名, 类别)
# 类别：commodity=商品期货 / index=股指期货
# 已逐一用 yfinance 实测能返回数据（2026-07 校验）；欧洲股指(FESX/FDAX)Yahoo 无 =F 对应，已剔除。
FUTURES: dict[str, tuple[str, str]] = {
    # ③ 商品期货 —— 能源
    "CL=F": ("WTI原油", "commodity"),
    "BZ=F": ("布伦特原油", "commodity"),
    "NG=F": ("天然气", "commodity"),
    "RB=F": ("RBOB汽油", "commodity"),
    "HO=F": ("取暖油", "commodity"),
    # ③ 商品期货 —— 贵金属
    "GC=F": ("黄金", "commodity"),
    "SI=F": ("白银", "commodity"),
    "PL=F": ("铂", "commodity"),
    "PA=F": ("钯", "commodity"),
    # ③ 商品期货 —— 基本金属
    "HG=F": ("铜", "commodity"),
    # ③ 商品期货 —— 农产品
    "ZS=F": ("大豆", "commodity"),
    "ZC=F": ("玉米", "commodity"),
    "ZW=F": ("小麦", "commodity"),
    "ZL=F": ("豆油", "commodity"),
    "ZM=F": ("豆粕", "commodity"),
    "KC=F": ("咖啡", "commodity"),
    "SB=F": ("糖", "commodity"),
    "CT=F": ("棉花", "commodity"),
    "CC=F": ("可可", "commodity"),
    "LBS=F": ("木材(旧,2023停)", "commodity"),
    "LBR=F": ("木材(新)", "commodity"),
    "OJ=F": ("橙汁", "commodity"),
    # ③ 商品期货 —— 畜牧
    "LE=F": ("活牛", "commodity"),
    "HE=F": ("瘦猪", "commodity"),
    "GF=F": ("饲牛", "commodity"),
    # ④ 股指期货
    "ES=F": ("标普500 E-mini", "index"),
    "NQ=F": ("纳斯达克100 E-mini", "index"),
    "YM=F": ("道琼斯 E-mini", "index"),
    "RTY=F": ("罗素2000 E-mini", "index"),
    "NKD=F": ("日经225(美元)", "index"),
}


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


def _sanitize(ticker: str) -> str:
    """CL=F -> CL_F；把非字母数字字符统一替换为下划线。"""
    return re.sub(r"[^0-9A-Za-z]+", "_", ticker).strip("_")


def _out_path(ticker: str) -> Path:
    return DATA_DIR / f"{_sanitize(ticker)}.parquet"


def _already_done(ticker: str) -> bool:
    p = _out_path(ticker)
    if p.exists() and p.stat().st_size > 600:
        return True
    return (p.with_suffix(".csv.gz")).exists()


def _save(ticker: str, name: str, category: str, df: pd.DataFrame) -> int:
    p = _out_path(ticker)
    p.parent.mkdir(parents=True, exist_ok=True)
    df = df.copy()
    for c in COLS:
        if c not in df.columns:
            df[c] = pd.NA
    df = df[COLS]
    df["ticker"] = ticker
    df["name"] = name
    df["category"] = category
    try:
        df.to_parquet(p, index=False)
    except Exception:
        p = p.with_suffix(".csv.gz")
        df.to_csv(p, index=False, compression="gzip")
    return len(df)


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
    df = df.rename(columns={"Date": "date", "Open": "open", "High": "high", "Low": "low",
                            "Close": "close", "Adj Close": "adj_close", "Volume": "volume"})
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    return df


def run(start: str, limit: int | None, sleep: float) -> None:
    items = list(FUTURES.items())
    if limit:
        items = items[:limit]
    total = len(items)
    _log(f"=== 全球期货下载开始（start={start}，共 {total} 个品种）===")

    ok = skip = empty = fail = 0
    for i, (ticker, (name, category)) in enumerate(items, 1):
        if _already_done(ticker):
            skip += 1
            continue
        try:
            df = fetch_yf(ticker, start)
            if df is None or len(df) == 0:
                _save(ticker, name, category, pd.DataFrame(columns=COLS))  # 空占位
                empty += 1
                _log(f"  [{i}/{total}] 空 {ticker} {name}")
            else:
                n = _save(ticker, name, category, df)
                ok += 1
                lo, hi = df["date"].min(), df["date"].max()
                _log(f"  [{i}/{total}] OK {ticker} {name}（{category}）：{n}行 {lo}~{hi}")
        except Exception as e:  # noqa: BLE001
            fail += 1
            _log(f"  [{i}/{total}] FAIL {ticker} {name}: {str(e)[:100]}")
        time.sleep(sleep)

    _log(f"=== 全部完成：OK={ok} SKIP={skip} 空={empty} FAIL={fail} / 共{total} ===")
    show_status()


def show_status() -> None:
    print(f"数据目录：{DATA_DIR}")
    if not DATA_DIR.exists():
        print("  （尚无文件）")
        return
    files = sorted(DATA_DIR.glob("*.parquet")) + sorted(DATA_DIR.glob("*.csv.gz"))
    nonempty = 0
    for f in files:
        try:
            n = len(pd.read_parquet(f)) if f.suffix == ".parquet" else len(pd.read_csv(f))
        except Exception:
            n = -1
        if n > 0:
            nonempty += 1
        print(f"  {f.name}: {n} 行")
    print(f"共 {len(files)} 文件，含数据 {nonempty} 个")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=START_DEFAULT)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--sleep", type=float, default=0.5)
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    if args.status:
        show_status()
        return

    run(args.start, args.limit, args.sleep)


if __name__ == "__main__":
    main()
