#!/usr/bin/env python3
"""
全量外汇（FX）历史行情下载：主要货币对 + 美元指数，2000-01-01 至今，日线。

数据源：yfinance（Yahoo Finance）。外汇代码用 `=X` 后缀，美元指数为 DX-Y.NYB。
本机通过 http_proxy=127.0.0.1:1089 访问 Yahoo（勿关代理）。

设计要点（无人值守）：
  - 硬编码「已核实返回数据」的货币对字典（分组：人民币 / 主要直盘 / 交叉盘 / 美元指数）
  - 每个货币对一个 parquet，断点续传（已存在且非空则跳过）
  - 空结果不落占位文件：留空缺让下次运行重试，并记为 dropped（区别于 bulk_download_history 的空占位策略）
  - 所有网络调用带指数退避重试；单个失败不拖垮整体

统一列：date, open, high, low, close, adj_close, volume, ticker, name
存储：data/fx_history/<ticker_sanitized>.parquet
      （sanitize：所有非字母数字 → '_'，如 USDCNY=X→USDCNY_X, DX-Y.NYB→DX_Y_NYB）

用法（务必 .venv/bin/python）：
  .venv/bin/python scripts/download_fx.py
  .venv/bin/python scripts/download_fx.py --limit 3     # 小样
  .venv/bin/python scripts/download_fx.py --status
"""
from __future__ import annotations

import argparse
import re
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data" / "fx_history"
START_DEFAULT = "2000-01-01"
TODAY = datetime.now().strftime("%Y-%m-%d")
COLS = ["date", "open", "high", "low", "close", "adj_close", "volume"]

# 硬编码「已核实返回全量历史数据」的货币对（本机 yfinance 实测）。
# 注意：CNH=X（离岸人民币）当前 Yahoo 仅提供 1d/5d 无日线历史，运行时会记为 dropped；
#       USDCNH=X 返回空，故不列入。
FX_PAIRS: dict[str, str] = {
    # ── 人民币 ──
    "USDCNY=X": "美元人民币",
    "CNH=X": "离岸人民币",     # 目前无历史，运行时自动 drop
    "EURCNY=X": "欧元人民币",
    "CNYJPY=X": "人民币日元",
    # ── 主要直盘 ──
    "EURUSD=X": "欧元美元",
    "GBPUSD=X": "英镑美元",
    "AUDUSD=X": "澳元美元",
    "NZDUSD=X": "纽元美元",
    "USDJPY=X": "美元日元",
    "USDCHF=X": "美元瑞郎",
    "USDCAD=X": "美元加元",
    "USDHKD=X": "美元港币",
    "USDSGD=X": "美元新加坡元",
    "USDKRW=X": "美元韩元",
    "USDINR=X": "美元印度卢比",
    "USDTWD=X": "美元新台币",
    "USDTHB=X": "美元泰铢",
    # ── 交叉盘 ──
    "EURJPY=X": "欧元日元",
    "EURGBP=X": "欧元英镑",
    "GBPJPY=X": "英镑日元",
    "AUDJPY=X": "澳元日元",
    # ── 美元指数 ──
    "DX-Y.NYB": "美元指数",
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
    """所有非字母数字字符 → '_'，如 USDCNY=X→USDCNY_X, DX-Y.NYB→DX_Y_NYB。"""
    return re.sub(r"[^A-Za-z0-9]", "_", ticker)


def _out_path(ticker: str) -> Path:
    return DATA_DIR / f"{_sanitize(ticker)}.parquet"


def _already_done(ticker: str) -> bool:
    """已存在且非空（>600 字节，排除空占位/损坏文件）才算完成。"""
    p = _out_path(ticker)
    return p.exists() and p.stat().st_size > 600


def _save(ticker: str, name: str, df: pd.DataFrame) -> int:
    p = _out_path(ticker)
    p.parent.mkdir(parents=True, exist_ok=True)
    df = df.copy()
    df["ticker"] = ticker
    df["name"] = name
    for c in COLS:
        if c not in df.columns:
            df[c] = pd.NA
    df = df[COLS + ["ticker", "name"]]
    df.to_parquet(p, index=False)
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
    # 单标的 yf.download 返回 MultiIndex 列，展平取第一层（('Close','USDCNY=X')→'Close'）
    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    df = df.rename(columns={"Date": "date", "Open": "open", "High": "high", "Low": "low",
                            "Close": "close", "Adj Close": "adj_close", "Volume": "volume"})
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    return df


def run(start: str, limit: int | None, sleep: float) -> None:
    pairs = list(FX_PAIRS.items())
    if limit:
        pairs = pairs[:limit]
    total = len(pairs)
    _log(f"=== FX 下载开始（start={start}，共 {total} 个货币对）===")

    ok = skip = dropped = fail = 0
    dropped_list: list[str] = []
    for i, (ticker, name) in enumerate(pairs, 1):
        if _already_done(ticker):
            skip += 1
            _log(f"  [{i}/{total}] SKIP 已存在 {ticker}（{name}）")
            continue
        try:
            df = fetch_yf(ticker, start)
            if df is None or len(df) == 0:
                # 空结果：不落占位，记为 dropped，下次运行会重试
                dropped += 1
                dropped_list.append(ticker)
                _log(f"  [{i}/{total}] DROP 空数据 {ticker}（{name}）—— 不落文件，下次重试")
            else:
                n = _save(ticker, name, df)
                ok += 1
                rng = f"{df['date'].min()}..{df['date'].max()}"
                _log(f"  [{i}/{total}] OK {ticker}（{name}）：{n} 行 {rng}")
        except Exception as e:  # noqa: BLE001
            fail += 1
            _log(f"  [{i}/{total}] FAIL {ticker}（{name}）：{str(e)[:100]}")
        time.sleep(sleep)

    _log(f"=== 完成：OK={ok} SKIP={skip} DROP={dropped} FAIL={fail} / 共{total} ===")
    if dropped_list:
        _log(f"    dropped（空数据）：{', '.join(dropped_list)}")


def show_status() -> None:
    print(f"数据目录：{DATA_DIR}")
    print(f"字典货币对总数：{len(FX_PAIRS)}")
    if not DATA_DIR.exists():
        print("  （目录尚不存在）")
        return
    files = sorted(DATA_DIR.glob("*.parquet"))
    nonempty = [f for f in files if f.stat().st_size > 600]
    print(f"  parquet 文件：{len(files)}（非空约 {len(nonempty)}）")
    for f in files:
        try:
            df = pd.read_parquet(f, columns=["date"])
            n = len(df)
            rng = f"{df['date'].min()}..{df['date'].max()}" if n else ""
        except Exception as e:  # noqa: BLE001
            n, rng = -1, str(e)[:40]
        print(f"    {f.name:20s} {n:6d} 行  {rng}")


def main() -> None:
    ap = argparse.ArgumentParser(description="外汇历史行情下载（yfinance）")
    ap.add_argument("--start", default=START_DEFAULT)
    ap.add_argument("--limit", type=int, default=None, help="只处理前 N 个（小样测试）")
    ap.add_argument("--sleep", type=float, default=0.5, help="每个货币对之间的间隔秒数")
    ap.add_argument("--status", action="store_true", help="打印已下载文件状态后退出")
    args = ap.parse_args()

    if args.status:
        show_status()
        return

    run(args.start, args.limit, args.sleep)
    show_status()


if __name__ == "__main__":
    main()
