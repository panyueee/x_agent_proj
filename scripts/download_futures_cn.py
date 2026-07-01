#!/usr/bin/env python3
"""
国内期货主力连续（主力连续）全量历史下载：商品期货 + 金融期货，2000-01-01 至今，日线。

数据源（本机实测可用）：
  - akshare.futures_main_sina(symbol="RB0", start_date=..., end_date=...) —— 新浪主力连续历史
    返回列：['日期','开盘价','最高价','最低价','收盘价','成交量','持仓量','动态结算价']
    持仓量 = open interest，动态结算价 = settle。~2-3s/品种。

设计要点：
  - 品种代码表硬编码（futures_display_main_sina 在本机会 HANG，禁止调用）
  - 每个品种一个 parquet，断点续传（已存在且非空跳过）
  - 网络调用带指数退避重试；单品种失败不拖垮整体
  - 无数据/报错的品种从结果中剔除并记录

存储：data/futures_history/cn/<symbol>.parquet
统一列：date, open, high, low, close, volume, open_interest, settle,
        symbol, name, exchange, category

用法（务必 .venv/bin/python）：
  .venv/bin/python scripts/download_futures_cn.py
  .venv/bin/python scripts/download_futures_cn.py --limit 5      # 小样
  .venv/bin/python scripts/download_futures_cn.py --category financial
  .venv/bin/python scripts/download_futures_cn.py --status
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data" / "futures_history" / "cn"
START_DEFAULT = "20000101"
TODAY = datetime.now().strftime("%Y%m%d")

# 统一英文列
COLS = ["date", "open", "high", "low", "close", "volume", "open_interest", "settle"]
META = ["symbol", "name", "exchange", "category"]

# 新浪原始中文列 → 英文列
RENAME = {
    "日期": "date",
    "开盘价": "open",
    "最高价": "high",
    "最低价": "low",
    "收盘价": "close",
    "成交量": "volume",
    "持仓量": "open_interest",
    "动态结算价": "settle",
}

# ── 品种表：symbol → (中文名, 交易所, 类别) ─────────────────────────────────────
# category: commodity / financial
SYMBOLS: dict[str, tuple[str, str, str]] = {
    # 上期所 / 能源中心 SHFE / INE ──────────────────────────────────────────────
    "CU0": ("铜", "SHFE", "commodity"),
    "AL0": ("铝", "SHFE", "commodity"),
    "ZN0": ("锌", "SHFE", "commodity"),
    "PB0": ("铅", "SHFE", "commodity"),
    "NI0": ("镍", "SHFE", "commodity"),
    "SN0": ("锡", "SHFE", "commodity"),
    "AU0": ("黄金", "SHFE", "commodity"),
    "AG0": ("白银", "SHFE", "commodity"),
    "RB0": ("螺纹钢", "SHFE", "commodity"),
    "HC0": ("热卷", "SHFE", "commodity"),
    "SS0": ("不锈钢", "SHFE", "commodity"),
    "BU0": ("沥青", "SHFE", "commodity"),
    "RU0": ("橡胶", "SHFE", "commodity"),
    "FU0": ("燃油", "SHFE", "commodity"),
    "SP0": ("纸浆", "SHFE", "commodity"),
    "WR0": ("线材", "SHFE", "commodity"),
    "AO0": ("氧化铝", "SHFE", "commodity"),
    "BR0": ("丁二烯橡胶", "SHFE", "commodity"),
    "SC0": ("原油", "INE", "commodity"),
    "LU0": ("低硫燃油", "INE", "commodity"),
    "NR0": ("20号胶", "INE", "commodity"),
    "BC0": ("国际铜", "INE", "commodity"),
    "EC0": ("集运欧线", "INE", "commodity"),
    # 大商所 DCE ────────────────────────────────────────────────────────────────
    "C0": ("玉米", "DCE", "commodity"),
    "CS0": ("淀粉", "DCE", "commodity"),
    "A0": ("豆一", "DCE", "commodity"),
    "B0": ("豆二", "DCE", "commodity"),
    "M0": ("豆粕", "DCE", "commodity"),
    "Y0": ("豆油", "DCE", "commodity"),
    "P0": ("棕榈油", "DCE", "commodity"),
    "JD0": ("鸡蛋", "DCE", "commodity"),
    "L0": ("塑料", "DCE", "commodity"),
    "V0": ("PVC", "DCE", "commodity"),
    "PP0": ("聚丙烯", "DCE", "commodity"),
    "J0": ("焦炭", "DCE", "commodity"),
    "JM0": ("焦煤", "DCE", "commodity"),
    "I0": ("铁矿石", "DCE", "commodity"),
    "EG0": ("乙二醇", "DCE", "commodity"),
    "EB0": ("苯乙烯", "DCE", "commodity"),
    "PG0": ("液化气", "DCE", "commodity"),
    "LH0": ("生猪", "DCE", "commodity"),
    "RR0": ("粳米", "DCE", "commodity"),
    "FB0": ("纤维板", "DCE", "commodity"),
    "BB0": ("胶合板", "DCE", "commodity"),
    # 郑商所 CZCE ────────────────────────────────────────────────────────────────
    "SR0": ("白糖", "CZCE", "commodity"),
    "CF0": ("棉花", "CZCE", "commodity"),
    "CY0": ("棉纱", "CZCE", "commodity"),
    "TA0": ("PTA", "CZCE", "commodity"),
    "MA0": ("甲醇", "CZCE", "commodity"),
    "FG0": ("玻璃", "CZCE", "commodity"),
    "RM0": ("菜粕", "CZCE", "commodity"),
    "OI0": ("菜油", "CZCE", "commodity"),
    "ZC0": ("动力煤", "CZCE", "commodity"),
    "SF0": ("硅铁", "CZCE", "commodity"),
    "SM0": ("锰硅", "CZCE", "commodity"),
    "AP0": ("苹果", "CZCE", "commodity"),
    "CJ0": ("红枣", "CZCE", "commodity"),
    "UR0": ("尿素", "CZCE", "commodity"),
    "SA0": ("纯碱", "CZCE", "commodity"),
    "PF0": ("短纤", "CZCE", "commodity"),
    "PK0": ("花生", "CZCE", "commodity"),
    "SH0": ("烧碱", "CZCE", "commodity"),
    "PX0": ("对二甲苯", "CZCE", "commodity"),
    "RS0": ("菜籽", "CZCE", "commodity"),
    "WH0": ("强麦", "CZCE", "commodity"),
    "PR0": ("瓶片", "CZCE", "commodity"),
    # 广期所 GFEX ────────────────────────────────────────────────────────────────
    "SI0": ("工业硅", "GFEX", "commodity"),
    "LC0": ("碳酸锂", "GFEX", "commodity"),
    # 中金所 CFFEX（金融）────────────────────────────────────────────────────────
    "IF0": ("沪深300", "CFFEX", "financial"),
    "IH0": ("上证50", "CFFEX", "financial"),
    "IC0": ("中证500", "CFFEX", "financial"),
    "IM0": ("中证1000", "CFFEX", "financial"),
    "TS0": ("2年国债", "CFFEX", "financial"),
    "TF0": ("5年国债", "CFFEX", "financial"),
    "T0": ("10年国债", "CFFEX", "financial"),
    "TL0": ("30年国债", "CFFEX", "financial"),
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


def _out_path(symbol: str) -> Path:
    safe = symbol.replace("/", "_").replace("\\", "_")
    return DATA_DIR / f"{safe}.parquet"


def _already_done(symbol: str) -> bool:
    p = _out_path(symbol)
    return p.exists() and p.stat().st_size > 0


def _save(symbol: str, df: pd.DataFrame) -> int:
    name, exch, cat = SYMBOLS[symbol]
    p = _out_path(symbol)
    p.parent.mkdir(parents=True, exist_ok=True)
    df = df.copy()
    df["symbol"] = symbol
    df["name"] = name
    df["exchange"] = exch
    df["category"] = cat
    for c in COLS:
        if c not in df.columns:
            df[c] = pd.NA
    df = df[COLS + META]
    df.to_parquet(p, index=False)
    return len(df)


def fetch_one(ak, symbol: str, start: str) -> pd.DataFrame | None:
    def _do():
        return ak.futures_main_sina(symbol=symbol, start_date=start, end_date=TODAY)

    df = _retry(_do, tries=4, base=1.5, what=symbol)
    if df is None or len(df) == 0:
        return None
    df = df.rename(columns=RENAME)
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    for c in ["open", "high", "low", "close", "volume", "open_interest", "settle"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def run(category: str, start: str, limit: int | None, sleep: float) -> None:
    import akshare as ak

    syms = [s for s, (_, _, cat) in SYMBOLS.items()
            if category == "all" or cat == category]
    if limit:
        syms = syms[:limit]
    total = len(syms)
    _log(f"=== 国内期货主力连续下载开始（category={category} start={start} 共{total}品种）===")

    ok = skip = empty = fail = 0
    failed: list[str] = []
    for i, sym in enumerate(syms, 1):
        name = SYMBOLS[sym][0]
        if _already_done(sym):
            skip += 1
            continue
        try:
            df = fetch_one(ak, sym, start)
            if df is None or len(df) == 0:
                empty += 1
                failed.append(f"{sym}({name}) 空")
                _log(f"  [{i}/{total}] {sym} {name}: 无数据，剔除")
            else:
                n = _save(sym, df)
                ok += 1
                rng = f"{df['date'].iloc[0]}~{df['date'].iloc[-1]}"
                _log(f"  [{i}/{total}] OK={ok} SKIP={skip} 空={empty} FAIL={fail} | "
                     f"{sym} {name}: {n}行 {rng}")
        except Exception as e:  # noqa: BLE001
            fail += 1
            failed.append(f"{sym}({name}) 报错:{str(e)[:60]}")
            _log(f"  [{i}/{total}] {sym} {name} FAIL: {str(e)[:80]}")
        time.sleep(sleep)

    _log(f"=== 完成：OK={ok} SKIP={skip} 空={empty} FAIL={fail} / 共{total} ===")
    if failed:
        _log("剔除/失败品种：" + "; ".join(failed))
    show_status()


def show_status() -> None:
    print(f"数据目录：{DATA_DIR}")
    if not DATA_DIR.exists():
        print("  尚无数据")
        return
    files = sorted(DATA_DIR.glob("*.parquet"))
    print(f"  已下载 {len(files)} 个品种 / 共定义 {len(SYMBOLS)} 个")
    total_rows = 0
    for f in files:
        try:
            df = pd.read_parquet(f, columns=["date"])
            total_rows += len(df)
        except Exception:  # noqa: BLE001
            pass
    print(f"  累计约 {total_rows} 行")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", choices=["all", "commodity", "financial"], default="all")
    ap.add_argument("--start", default=START_DEFAULT)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--sleep", type=float, default=0.5)
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    if args.status:
        show_status()
        return

    run(args.category, args.start, args.limit, args.sleep)


if __name__ == "__main__":
    main()
