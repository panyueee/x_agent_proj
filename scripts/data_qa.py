#!/usr/bin/env python3
"""
数据体检：扫描 data/ 下所有行情 parquet，检出常见抓取瑕疵——
空/占位文件、历史截断(末日聚集)、内部日期断档、全零列。输出质量报告。
用法：.venv/bin/python scripts/data_qa.py [--dir stock_history] [--sample N]
"""
from __future__ import annotations
import argparse, os, sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"
TODAY = datetime.now().strftime("%Y-%m-%d")
MARKET_DIRS = ["stock_history/a", "stock_history/hk", "stock_history/us",
               "futures_history/cn", "futures_history/global",
               "fx_history", "bond_history", "crypto_history", "index_history",
               "etf_history", "reits_history/history", "cb_history",
               "options_history/chains", "macro_history/fred"]


def _date_col(df):
    for c in ("date", "日期", "day"):
        if c in df.columns:
            return c
    return df.columns[0]


def check_file(path):
    """返回 (rows, last_date, max_gap_days, zero_cols, ok)。"""
    try:
        df = pd.read_parquet(path)
    except Exception:
        return {"rows": -1, "err": "read_fail"}
    if len(df) == 0:
        return {"rows": 0}
    dc = _date_col(df)
    try:
        dts = pd.to_datetime(df[dc], errors="coerce").dropna().sort_values()
    except Exception:
        dts = None
    last = dts.iloc[-1].strftime("%Y-%m-%d") if dts is not None and len(dts) else "?"
    # 最大内部断档(日历日；>15 天可能异常，但含长假/停牌，仅作参考)
    gap = int(dts.diff().dt.days.max()) if dts is not None and len(dts) > 1 else 0
    # 全零数值列(如 IF0 settle=0)
    zero_cols = []
    for c in df.columns:
        if df[c].dtype.kind in "fi":
            s = pd.to_numeric(df[c], errors="coerce")
            if s.notna().any() and (s.fillna(0) == 0).all():
                zero_cols.append(c)
    return {"rows": len(df), "last": last, "gap": gap, "zero_cols": zero_cols}


def scan_dir(subdir, sample):
    d = DATA / subdir
    if not d.exists():
        return None
    files = [f for f in os.listdir(d) if f.endswith(".parquet")]
    if not files:
        return None
    if sample and len(files) > sample:
        import random; random.seed(0); files = random.sample(files, sample)
    n_empty = n_readfail = 0
    rows_list, last_dates = [], Counter()
    gap_flag, zero_flag = [], defaultdict(int)
    for f in files:
        r = check_file(d / f)
        if r["rows"] == -1: n_readfail += 1; continue
        if r["rows"] == 0: n_empty += 1; continue
        rows_list.append(r["rows"])
        last_dates[r["last"]] += 1
        if r["gap"] > 40: gap_flag.append((f, r["gap"]))
        for c in r["zero_cols"]: zero_flag[c] += 1
    return {"n": len(files), "empty": n_empty, "readfail": n_readfail,
            "rows_med": int(pd.Series(rows_list).median()) if rows_list else 0,
            "rows_min": min(rows_list) if rows_list else 0,
            "last_dates": last_dates, "gap_flag": gap_flag, "zero_flag": dict(zero_flag)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=None, help="只查某个子目录")
    ap.add_argument("--sample", type=int, default=None, help="每目录抽样N个(大目录提速)")
    a = ap.parse_args()
    dirs = [a.dir] if a.dir else MARKET_DIRS
    print(f"数据体检 @ {TODAY}（今日）\n" + "=" * 70)
    for sub in dirs:
        r = scan_dir(sub, a.sample)
        if r is None:
            continue
        # 末日聚集：最近的末日占比(判断整体是否新鲜)
        top_last = r["last_dates"].most_common(3)
        recent = sum(v for k, v in r["last_dates"].items() if k >= "2026-06-25")
        print(f"\n📁 {sub}")
        print(f"   文件 {r['n']} | 空/占位 {r['empty']} | 读失败 {r['readfail']} | 行数 中位{r['rows_med']}/最少{r['rows_min']}")
        print(f"   末日Top3: {top_last} | 末日≥6/25(新鲜)占 {recent}/{r['n']-r['empty']}")
        if r["zero_flag"]:
            print(f"   ⚠ 全零列: {r['zero_flag']}")
        if r["gap_flag"]:
            print(f"   ⚠ 大断档(>40天) {len(r['gap_flag'])}个, 例: {r['gap_flag'][:3]}")
    print("\n" + "=" * 70 + "\n体检完成")


if __name__ == "__main__":
    main()
