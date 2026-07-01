#!/usr/bin/env python3
"""
全量历史债券市场数据下载：国债收益率曲线与利率（中国 + 美国），2000-01-01 至今。

数据源（本机实测可用，需 certifi 修 SSL）：
  - ⭐ 核心：akshare.bond_zh_us_rate —— 中美国债收益率 + 利差 一张宽表，2000 至今约 7000+ 行
       列：日期 / 中国国债收益率2年,5年,10年,30年 / 中国国债收益率10年-2年 / 中国GDP年增率
           / 美国国债收益率2年,5年,10年,30年 / 美国国债收益率10年-2年 / 美国GDP年增率
       这是利率宽表（非 OHLCV），债券本身就是"收益率"而非价格，符合预期。
  - 美债收益率指数：yfinance OHLCV —— ^TNX(美债10年) ^TYX(美债30年) ^FVX(美债5年) ^IRX(美债13周)
  - 中债收益率曲线：akshare.bond_china_yield —— 需按年循环拉取再拼接（长区间返回空），约 2006 起有数据
       列：曲线名称,日期,3月,6月,1年,3年,5年,7年,10年,30年（含国债/短融/商业银行债多条曲线）

设计要点（无人值守）：
  - 所有网络调用带指数退避重试
  - 每个数据集一个 parquet，断点续传（已存在且非空跳过）
  - 单数据集失败不拖垮其他数据集

存储：data/bond_history/
  - cn_us_treasury_yields.parquet    中美国债收益率宽表（date + 各期限收益率/利差）
  - us_yield_<name>.parquet          单个美债收益率指数 OHLCV
  - cn_yield_curve.parquet           中债收益率曲线（best-effort，逐年拼接）

用法（务必 .venv/bin/python）：
  .venv/bin/python scripts/download_bonds.py
  .venv/bin/python scripts/download_bonds.py --limit 2     # 美债指数只跑前 2 个 + 主表
  .venv/bin/python scripts/download_bonds.py --status
"""
from __future__ import annotations

# ── SSL：akshare/urllib 在本机需 certifi，否则 CERTIFICATE_VERIFY_FAILED ──
import os
import certifi
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

import argparse
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data" / "bond_history"
START_DEFAULT = "2000-01-01"
TODAY = datetime.now().strftime("%Y-%m-%d")

# 美债收益率指数（yfinance 报价其值≈收益率×10，如 ^TNX=42 表示 4.2%）
US_YIELD_TICKERS = [
    ("^TNX", "us10y"),   # 美债 10 年
    ("^TYX", "us30y"),   # 美债 30 年
    ("^FVX", "us5y"),    # 美债 5 年
    ("^IRX", "us13w"),   # 美债 13 周（短端）
]
OHLCV_COLS = ["date", "open", "high", "low", "close", "adj_close", "volume", "ticker", "name"]


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


def _nonempty(p: Path) -> bool:
    return p.exists() and p.stat().st_size > 600


def _save(p: Path, df: pd.DataFrame) -> int:
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_parquet(p, index=False)
    except Exception:
        p = p.with_suffix(".csv.gz")
        df.to_csv(p, index=False, compression="gzip")
    return len(df)


# ── a. 中美国债收益率宽表（核心）────────────────────────────────────────────────

def download_cn_us_treasury() -> None:
    p = DATA_DIR / "cn_us_treasury_yields.parquet"
    if _nonempty(p):
        _log("[cn_us] 已存在，跳过")
        return
    try:
        import akshare as ak
        df = _retry(lambda: ak.bond_zh_us_rate(start_date="20000101"),
                    tries=5, base=2.0, what="bond_zh_us_rate")
        if df is None or df.empty:
            _log("!! [cn_us] 返回空，跳过")
            return
        df = df.rename(columns={"日期": "date"})
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        n = _save(p, df)
        _log(f"[cn_us] 保存 {n} 行，{df['date'].min()}~{df['date'].max()}，列数 {len(df.columns)}")
    except Exception as e:  # noqa: BLE001
        _log(f"!! [cn_us] 失败：{str(e)[:160]}")


# ── b. 美债收益率指数 OHLCV（yfinance）──────────────────────────────────────────

def _fetch_yf(ticker: str, start: str) -> pd.DataFrame | None:
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


def download_us_yield_indices(start: str, limit: int | None) -> None:
    tickers = US_YIELD_TICKERS[:limit] if limit else US_YIELD_TICKERS
    for ticker, name in tickers:
        p = DATA_DIR / f"us_yield_{name}.parquet"
        if _nonempty(p):
            _log(f"[us:{name}] 已存在，跳过")
            continue
        try:
            df = _fetch_yf(ticker, start)
            if df is None or df.empty:
                _log(f"!! [us:{name}] {ticker} 返回空")
                continue
            df["ticker"] = ticker
            df["name"] = name
            for c in OHLCV_COLS:
                if c not in df.columns:
                    df[c] = pd.NA
            df = df[OHLCV_COLS]
            n = _save(p, df)
            _log(f"[us:{name}] {ticker} 保存 {n} 行，{df['date'].min()}~{df['date'].max()}")
        except Exception as e:  # noqa: BLE001
            _log(f"!! [us:{name}] {ticker} 失败：{str(e)[:160]}")


# ── c. 中债收益率曲线（best-effort，逐年拼接）──────────────────────────────────

def download_cn_yield_curve() -> None:
    p = DATA_DIR / "cn_yield_curve.parquet"
    if _nonempty(p):
        _log("[cn_curve] 已存在，跳过")
        return
    try:
        import akshare as ak
    except Exception as e:  # noqa: BLE001
        _log(f"!! [cn_curve] 导入 akshare 失败：{str(e)[:120]}")
        return

    frames: list[pd.DataFrame] = []
    this_year = datetime.now().year
    for year in range(2002, this_year + 1):
        sd, ed = f"{year}0101", f"{year}1231"
        try:
            df = _retry(lambda s=sd, e=ed: ak.bond_china_yield(start_date=s, end_date=e),
                        tries=3, base=1.5, what=f"cn_yield {year}")
            if df is not None and not df.empty:
                frames.append(df)
                _log(f"  [cn_curve] {year}: {len(df)} 行")
        except Exception as e:  # noqa: BLE001
            _log(f"  [cn_curve] {year} 失败：{str(e)[:80]}")
        time.sleep(0.4)

    if not frames:
        _log("!! [cn_curve] 逐年拼接后仍为空，跳过（bond_zh_us_rate 已覆盖中国国债收益率）")
        return
    out = pd.concat(frames, ignore_index=True)
    out = out.rename(columns={"日期": "date"})
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    out = out.drop_duplicates(subset=[c for c in ["曲线名称", "date"] if c in out.columns])
    n = _save(p, out)
    curves = out["曲线名称"].nunique() if "曲线名称" in out.columns else 0
    _log(f"[cn_curve] 保存 {n} 行，{out['date'].min()}~{out['date'].max()}，曲线 {curves} 条")


# ── 状态 ──────────────────────────────────────────────────────────────────────

def show_status() -> None:
    print(f"数据目录：{DATA_DIR}")
    if not DATA_DIR.exists():
        print("  (空)")
        return
    files = sorted(list(DATA_DIR.glob("*.parquet")) + list(DATA_DIR.glob("*.csv.gz")))
    if not files:
        print("  (无数据文件)")
        return
    for f in files:
        try:
            df = pd.read_parquet(f) if f.suffix == ".parquet" else pd.read_csv(f)
            rng = ""
            if "date" in df.columns and len(df):
                rng = f"  {df['date'].min()}~{df['date'].max()}"
            print(f"  {f.name}: {len(df)} 行, {len(df.columns)} 列{rng}")
        except Exception as e:  # noqa: BLE001
            print(f"  {f.name}: 读取失败 {str(e)[:60]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=START_DEFAULT, help="美债指数起始日")
    ap.add_argument("--limit", type=int, default=None, help="美债指数只跑前 N 个")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    if args.status:
        show_status()
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _log("=== 债券数据下载开始 ===")
    download_cn_us_treasury()
    download_us_yield_indices(args.start, args.limit)
    download_cn_yield_curve()
    _log("=== 全部完成 ===")
    show_status()


if __name__ == "__main__":
    main()
