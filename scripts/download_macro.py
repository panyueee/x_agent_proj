#!/usr/bin/env python3
"""
中美宏观 + 利率数据管道（akshare）。

本机网络现实：大量 akshare 函数走 eastmoney（push2/datacenter.eastmoney.com），
在此环境被墙且【无超时会永久挂起】。因此每次探测都必须用 call_timeout 硬超时包裹。
urllib 需要 certifi 否则 SSL 报错。

策略：先用 call_timeout 探测每个候选函数；只有返回【非空 DataFrame】的才落盘。
超时 / 报错 / 空 → 跳过并记录原因。

存储：每个指标一个 parquet → data/macro_history/<name>.parquet（保留返回的原始列）。
断点续传：已存在且非空的 parquet 跳过；--status 列出已有 parquet 与行数。

用法（务必 .venv/bin/python）：
  .venv/bin/python scripts/download_macro.py
  .venv/bin/python scripts/download_macro.py --status
"""
from __future__ import annotations

import argparse
import os
import threading
from datetime import datetime
from pathlib import Path

# SSL：eastmoney/新浪等 https 需要 certifi 根证书，否则报 CERTIFICATE_VERIFY_FAILED
os.environ["SSL_CERT_FILE"] = __import__("certifi").where()

import pandas as pd

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data" / "macro_history"


def _log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def call_timeout(fn, timeout=50, **kw):
    """硬超时调用：eastmoney 被墙时函数会永久挂起，用独立线程 + join 超时兜底。"""
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


def _out_path(name: str) -> Path:
    return DATA_DIR / f"{name}.parquet"


def _already_done(name: str) -> bool:
    """已存在且非空（>0 行）视为完成，可断点续传。"""
    p = _out_path(name)
    if not p.exists():
        return False
    try:
        return len(pd.read_parquet(p)) > 0
    except Exception:
        return False  # 损坏/空 → 重下


def _save(name: str, df: pd.DataFrame) -> int:
    p = _out_path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p, index=False)
    return len(df)


# 候选指标：(parquet 名, akshare 函数名, 参数 dict)
# rate_interbank 需要参数；其余多为无参。
CANDIDATES = [
    # ── 中国宏观 ──
    ("macro_china_gdp_yearly", "macro_china_gdp_yearly", {}),
    ("macro_china_cpi_monthly", "macro_china_cpi_monthly", {}),
    ("macro_china_cpi_yearly", "macro_china_cpi_yearly", {}),
    ("macro_china_ppi_yearly", "macro_china_ppi_yearly", {}),
    ("macro_china_pmi_yearly", "macro_china_pmi_yearly", {}),
    ("macro_china_m2_yearly", "macro_china_m2_yearly", {}),
    ("macro_china_shrzgm", "macro_china_shrzgm", {}),               # 社融
    ("macro_china_lpr", "macro_china_lpr", {}),                     # LPR
    ("macro_china_urban_unemployment", "macro_china_urban_unemployment", {}),
    # ── 美国宏观 ──
    ("macro_usa_gdp_monthly", "macro_usa_gdp_monthly", {}),
    ("macro_usa_cpi_monthly", "macro_usa_cpi_monthly", {}),
    ("macro_usa_unemployment_rate", "macro_usa_unemployment_rate", {}),
    ("macro_bank_usa_interest_rate", "macro_bank_usa_interest_rate", {}),  # 美联储利率
    # ── 利率 ──
    ("rate_interbank_shibor_overnight", "rate_interbank",
     {"market": "上海银行同业拆借市场", "symbol": "Shibor人民币", "indicator": "隔夜"}),
    ("macro_china_shibor_all", "macro_china_shibor_all", {}),
]


def run(force: bool = False) -> None:
    import akshare as ak

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    succeeded, skipped = [], []

    for name, fn_name, kw in CANDIDATES:
        if not force and _already_done(name):
            n = len(pd.read_parquet(_out_path(name)))
            _log(f"SKIP(已存在 {n} 行) {name}")
            succeeded.append((name, fn_name, n, "已存在"))
            continue

        fn = getattr(ak, fn_name, None)
        if fn is None:
            _log(f"SKIP(函数不存在) {name} <- ak.{fn_name}")
            skipped.append((name, fn_name, "akshare 无此函数"))
            continue

        _log(f"探测 {name} <- ak.{fn_name}({kw or ''}) ...")
        df, err = call_timeout(fn, timeout=50, **kw)
        if err:
            _log(f"  SKIP {name}: {err}")
            skipped.append((name, fn_name, err))
            continue
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            _log(f"  SKIP {name}: 返回空/非DataFrame")
            skipped.append((name, fn_name, "空/非DataFrame"))
            continue

        n = _save(name, df)
        cols = list(df.columns)
        _log(f"  OK {name}: {n} 行 列={cols}")
        succeeded.append((name, fn_name, n, cols))

    # ── 汇总 ──
    print("\n" + "=" * 70)
    print("汇总")
    print("=" * 70)
    print(f"\nSUCCEEDED ({len(succeeded)}):")
    for name, fn_name, n, cols in succeeded:
        print(f"  ✓ {name:36s} {n:>6} 行  ak.{fn_name}  {cols}")
    print(f"\nSKIPPED ({len(skipped)}):")
    for name, fn_name, reason in skipped:
        print(f"  ✗ {name:36s} ak.{fn_name}  原因: {reason}")


def show_status() -> None:
    print(f"数据目录：{DATA_DIR}")
    if not DATA_DIR.exists():
        print("  (目录不存在)")
        return
    files = sorted(DATA_DIR.glob("*.parquet"))
    if not files:
        print("  (无 parquet)")
        return
    for f in files:
        try:
            df = pd.read_parquet(f)
            print(f"  {f.name:44s} {len(df):>6} 行  列={list(df.columns)}")
        except Exception as e:  # noqa: BLE001
            print(f"  {f.name:44s} 读取失败: {str(e)[:60]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true", help="列出已有 parquet 与行数")
    ap.add_argument("--force", action="store_true", help="忽略已存在，强制重下")
    args = ap.parse_args()

    if args.status:
        show_status()
        return
    run(force=args.force)


if __name__ == "__main__":
    main()
