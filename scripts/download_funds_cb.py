#!/usr/bin/env python3
"""
基金 + 可转债历史数据下载（akshare）。

本机网络现实：部分 akshare 函数走 eastmoney。实测 push2/datacenter.eastmoney.com 被墙
会永久 HANG（无超时），而 fund.eastmoney.com（开放式基金/货基/ETF 日线）与新浪/集思录
接口在此环境可用。因此每个候选函数都先用 call_timeout 硬超时探测，只对返回非空 DataFrame
的函数建下载；超时/报错的记入 skipped。

存储：
  基金   -> data/fund_history/
  可转债 -> data/cb_history/
  列表型函数整表存一个 parquet；逐标的日线函数每只一个 parquet。

用法（务必 .venv/bin/python）：
  .venv/bin/python scripts/download_funds_cb.py
  .venv/bin/python scripts/download_funds_cb.py --status
  .venv/bin/python scripts/download_funds_cb.py --max-cb 15 --max-etf 12
"""
from __future__ import annotations

import os
os.environ["SSL_CERT_FILE"] = __import__("certifi").where()  # urllib 需要 certifi 否则 SSL 失败

import argparse
import threading
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
FUND_DIR = ROOT / "data" / "fund_history"
CB_DIR = ROOT / "data" / "cb_history"
TODAY = datetime.now().strftime("%Y%m%d")

# 常见 ETF 代码样本（fund_etf_hist_em 需逐只代码；此环境无可用的全量 ETF 代码表接口，
# 故用一小把主流 ETF 作样本，验证函数可用并落数据）
ETF_SAMPLE = [
    "159707", "510300", "510500", "510050", "159915",
    "588000", "512880", "515790", "512480", "159919",
    "510880", "159949", "512760", "515050", "159928",
]

# 已实测经新浪接口可下载的在市可转债代码（bond_zh_cov 走 eastmoney，偶尔 HANG；
# 该表作兜底，保证 bond_zh_hs_cov_daily 逐只日线（新浪，稳定）不被列表函数拖累）
CB_FALLBACK = [
    "sh113537", "sh110043", "sh110059", "sh110048", "sh113050", "sh110089",
    "sh118000", "sh113682", "sh113542", "sh110074", "sz128040", "sz123151",
    "sz127080", "sz128136", "sz123120", "sz128095", "sz127040", "sz123138",
]


def _log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def call_timeout(fn, timeout=25, **kw):
    """硬超时调用；超时（多半走 eastmoney 被墙 HANG）返回 (None, 'TIMEOUT...')。"""
    box = {}

    def run():
        try:
            box["r"] = fn(**kw)
        except Exception as e:  # noqa: BLE001
            box["e"] = e
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None, "TIMEOUT(可能走eastmoney被墙)"
    return box.get("r"), (str(box.get("e"))[:120] if "e" in box else None)


def _nonempty_parquet(p: Path) -> bool:
    return p.exists() and p.stat().st_size > 600


def _save(p: Path, df: pd.DataFrame) -> int:
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p, index=False)
    return len(df)


def cb_symbol(code: str) -> str | None:
    """债券代码 -> 新浪日线所需 symbol（sh/sz 前缀）。11->sh 12->sz 其它跳过。"""
    code = str(code).strip()
    if code.startswith("11") or code.startswith("13"):
        return "sh" + code
    if code.startswith("12"):
        return "sz" + code
    return None


# ── 主流程 ────────────────────────────────────────────────────────────────────

def run(max_cb: int, max_etf: int) -> dict:
    import akshare as ak
    FUND_DIR.mkdir(parents=True, exist_ok=True)
    CB_DIR.mkdir(parents=True, exist_ok=True)
    report = {"succeeded": [], "skipped": []}

    def do_list(name, fn, out_path, tries=1, timeout=25, **kw):
        """列表型函数：探测（可多次，走 eastmoney 的偶发 HANG 用重试兜）+ 整表存一个 parquet。"""
        if _nonempty_parquet(out_path):
            df = pd.read_parquet(out_path)
            _log(f"[SKIP存在] {name}: {out_path.name} rows={len(df)}")
            report["succeeded"].append((name, out_path, len(df), list(df.columns)))
            return df
        r = e = None
        for attempt in range(1, tries + 1):
            _log(f"探测 {name} ...（第 {attempt}/{tries} 次）")
            r, e = call_timeout(fn, timeout=timeout, **kw)
            if r is not None and hasattr(r, "columns") and len(r) > 0:
                break
        if r is None or not hasattr(r, "columns") or len(r) == 0:
            reason = e or "返回空/非DataFrame"
            _log(f"[SKIP] {name}: {reason}")
            report["skipped"].append((name, reason))
            return None
        n = _save(out_path, r)
        _log(f"[OK] {name}: 存 {out_path.name} rows={n}")
        report["succeeded"].append((name, out_path, n, list(r.columns)))
        return r

    # ── 基金：列表型 ──（fund_open_fund_daily_em 走 fund.eastmoney.com，偶发 HANG → 多试几次）
    do_list("fund_open_fund_daily_em", ak.fund_open_fund_daily_em,
            FUND_DIR / "fund_open_fund_daily_em.parquet", tries=3, timeout=40)
    do_list("fund_money_fund_daily_em", ak.fund_money_fund_daily_em,
            FUND_DIR / "fund_money_fund_daily_em.parquet", tries=2, timeout=30)

    # ── 可转债：列表型 ──（bond_zh_cov 走 eastmoney，偶发 HANG → 多试几次；失败仍有兜底表）
    cov_df = do_list("bond_zh_cov", ak.bond_zh_cov,
                     CB_DIR / "bond_zh_cov.parquet", tries=3, timeout=40)
    do_list("bond_cb_index_jsl", ak.bond_cb_index_jsl, CB_DIR / "bond_cb_index_jsl.parquet")
    do_list("bond_cov_comparison", ak.bond_cov_comparison, CB_DIR / "bond_cov_comparison.parquet")

    # ── 可转债：逐只日线 bond_zh_hs_cov_daily（新浪，稳定）──
    # 优先用 bond_zh_cov 代码表，拿不到就用实测可用的兜底表，保证该函数被真正验证/落数据
    # 先放实测可用的兜底代码（保证能拿到数据），再追加 bond_zh_cov 里的其它在市代码
    syms = list(CB_FALLBACK)
    if cov_df is not None and "债券代码" in cov_df.columns:
        for c in cov_df["债券代码"].astype(str).tolist():
            s = cb_symbol(c)
            if s and s not in syms:
                syms.append(s)
        _log(f"探测 bond_zh_hs_cov_daily（兜底 {len(CB_FALLBACK)} + bond_zh_cov 补充，共 {len(syms)} 只，目标 {max_cb} 只）...")
    else:
        _log(f"探测 bond_zh_hs_cov_daily（bond_zh_cov 不可用，用兜底表 {len(syms)} 只，目标 {max_cb} 只）...")
    if True:
        got = 0
        attempts = 0
        func_ok = None
        for sym in syms:
            if got >= max_cb:
                break
            out = CB_DIR / f"cov_daily_{sym}.parquet"
            if _nonempty_parquet(out):
                got += 1
                continue
            attempts += 1
            if attempts > max_cb * 4:  # 尝试上限，避免连续未上市标的空转
                break
            r, e = call_timeout(ak.bond_zh_hs_cov_daily, timeout=25, symbol=sym)
            if r is None or not hasattr(r, "columns") or len(r) == 0:
                if func_ok is None and e and "TIMEOUT" in e:
                    func_ok = False
                    report["skipped"].append(("bond_zh_hs_cov_daily", e))
                    _log(f"[SKIP] bond_zh_hs_cov_daily: {e}")
                    break
                continue  # 单只无数据（多为未上市），继续下一只
            func_ok = True
            n = _save(out, r)
            got += 1
            _log(f"  [OK] {sym}: {n}行 -> {out.name}")
        if func_ok:
            files = sorted(CB_DIR.glob("cov_daily_*.parquet"))
            report["succeeded"].append(
                ("bond_zh_hs_cov_daily", CB_DIR / "cov_daily_*.parquet",
                 f"{len(files)} 只", ["date", "open", "high", "low", "close", "volume"]))
        elif func_ok is False:
            pass  # 已在循环内记入 skipped（TIMEOUT）
        else:
            report["skipped"].append(("bond_zh_hs_cov_daily", "所有候选标的均无数据"))

    # ── 基金：逐只 ETF 日线 fund_etf_hist_em ──
    _log(f"探测 fund_etf_hist_em（样本 {len(ETF_SAMPLE)} 只，目标 {max_etf} 只）...")
    got = 0
    func_ok = None
    for code in ETF_SAMPLE:
        if got >= max_etf:
            break
        out = FUND_DIR / f"etf_daily_{code}.parquet"
        if _nonempty_parquet(out):
            got += 1
            continue
        r, e = call_timeout(ak.fund_etf_hist_em, timeout=25,
                            symbol=code, period="daily",
                            start_date="20150101", end_date=TODAY, adjust="")
        if r is None or not hasattr(r, "columns") or len(r) == 0:
            if func_ok is None and e and "TIMEOUT" in e:
                func_ok = False
                report["skipped"].append(("fund_etf_hist_em", e))
                _log(f"[SKIP] fund_etf_hist_em: {e}")
                break
            continue
        func_ok = True
        n = _save(out, r)
        got += 1
        _log(f"  [OK] ETF {code}: {n}行 -> {out.name}")
    if func_ok:
        files = sorted(FUND_DIR.glob("etf_daily_*.parquet"))
        report["succeeded"].append(
            ("fund_etf_hist_em", FUND_DIR / "etf_daily_*.parquet",
             f"{len(files)} 只",
             ["日期", "开盘", "收盘", "最高", "最低", "成交量", "成交额", "振幅", "涨跌幅", "涨跌额", "换手率"]))

    return report


def show_status() -> None:
    for label, d in [("基金 fund_history", FUND_DIR), ("可转债 cb_history", CB_DIR)]:
        print(f"\n=== {label}  ({d}) ===")
        if not d.exists():
            print("  (目录不存在)")
            continue
        files = sorted(d.glob("*.parquet"))
        if not files:
            print("  (无 parquet)")
            continue
        for f in files:
            try:
                n = len(pd.read_parquet(f, columns=None))
            except Exception as e:  # noqa: BLE001
                n = f"读失败:{str(e)[:40]}"
            print(f"  {f.name:38s} rows={n}")


def print_report(report: dict) -> None:
    print("\n" + "=" * 70)
    print("汇总")
    print("=" * 70)
    print("\nSUCCEEDED:")
    for name, path, rows, cols in report["succeeded"]:
        print(f"  ✓ {name}")
        print(f"      file(s): {path}")
        print(f"      rows={rows}  cols={cols}")
    print("\nSKIPPED:")
    for name, reason in report["skipped"]:
        print(f"  ✗ {name}: {reason}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--max-cb", type=int, default=15, help="下载可转债日线只数")
    ap.add_argument("--max-etf", type=int, default=12, help="下载 ETF 日线只数")
    args = ap.parse_args()

    if args.status:
        show_status()
        return

    _log("开始：基金 + 可转债下载")
    report = run(args.max_cb, args.max_etf)
    print_report(report)
    show_status()
    _log("结束")


if __name__ == "__main__":
    main()
