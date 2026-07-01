#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
下载中金所股指期权（CFFEX stock-index options）逐合约日频行情，构建完整期权链。

三大品种族：
  - HS300 (沪深300 / IO):   option_cffex_hs300_list/spot/daily_sina
  - SZ50  (上证50 / HO):    option_cffex_sz50_list/spot/daily_sina
  - ZZ1000(中证1000 / MO):  option_cffex_zz1000_list/spot/daily_sina

流程（SINA 数据源）：
  list_sina()  -> dict{标的名: [月份代码...]}，如 io2607
  spot_sina(月份代码) -> DataFrame，含 行权价 / 看涨合约-标识 / 看跌合约-标识（完整合约代码）
  daily_sina(完整合约代码) -> date/open/high/low/close/volume

输出：每个合约一个 parquet ->
  data/options_history/chains/cffex/<contract>.parquet
可续跑：已存在的非空 parquet 跳过。
"""

# ── 必须在 import akshare 之前设置 SSL 证书 ────────────────────────────────
import os
import certifi
os.environ["SSL_CERT_FILE"] = certifi.where()
# 清除代理环境变量：本机代理(127.0.0.1:1089)未运行会导致 ProxyError；SINA 直连即可
for _pv in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(_pv, None)
os.environ["NO_PROXY"] = "*"

import re
import time
import threading
import traceback
import pandas as pd
import akshare as ak

ROOT = "/Users/pany19/Documents/x_agent_proj"
OUT_DIR = os.path.join(ROOT, "data", "options_history", "chains", "cffex")
os.makedirs(OUT_DIR, exist_ok=True)

TIMEOUT = 30  # 秒，每个 akshare 网络调用的线程超时上限


# ── 通用超时包装：新线程执行，join(TIMEOUT)，仍存活即 TIMEOUT -> None ──────────
def timed_call(fn, *args, **kwargs):
    """在独立线程中执行 fn，超时或异常返回 (None, reason)；成功返回 (result, None)。"""
    box = {}

    def _worker():
        try:
            box["r"] = fn(*args, **kwargs)
        except Exception as e:  # 任意异常 -> None
            box["e"] = f"{type(e).__name__}: {e}"

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(TIMEOUT)
    if t.is_alive():
        return None, "TIMEOUT"
    if "e" in box:
        return None, box["e"]
    return box.get("r"), None


# ── 各品种族的函数映射 ──────────────────────────────────────────────────────
FAMILIES = {
    "hs300": {
        "product": "io",
        "list_fn": "option_cffex_hs300_list_sina",
        "spot_fn": "option_cffex_hs300_spot_sina",
        "daily_fn": "option_cffex_hs300_daily_sina",
    },
    "sz50": {
        "product": "ho",
        "list_fn": "option_cffex_sz50_list_sina",
        "spot_fn": "option_cffex_sz50_spot_sina",
        "daily_fn": "option_cffex_sz50_daily_sina",
    },
    "zz1000": {
        "product": "mo",
        "list_fn": "option_cffex_zz1000_list_sina",
        "spot_fn": "option_cffex_zz1000_spot_sina",
        "daily_fn": "option_cffex_zz1000_daily_sina",
    },
}

# 合约代码解析：io2607C4200 -> product=io, ym=2607, cp=C, strike=4200
CODE_RE = re.compile(r"^([a-zA-Z]+)(\d{4})([CP])(\d+)$")


def parse_code(code):
    m = CODE_RE.match(str(code).strip())
    if not m:
        return {"strike": None, "expiry": None, "call_put": None}
    _, ym, cp, strike = m.groups()
    yy, mm = ym[:2], ym[2:]
    expiry = f"20{yy}-{mm}"
    return {
        "strike": float(strike),
        "expiry": expiry,
        "call_put": "call" if cp.upper() == "C" else "put",
    }


def flatten_list(list_ret):
    """list_sina 返回 dict{名称: [月份代码...]} -> 扁平月份代码列表。"""
    months = []
    if isinstance(list_ret, dict):
        for v in list_ret.values():
            if isinstance(v, (list, tuple)):
                months.extend(v)
            else:
                months.append(v)
    elif isinstance(list_ret, (list, tuple)):
        months.extend(list_ret)
    # 去重保序
    seen, out = set(), []
    for m in months:
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


def enumerate_contracts(fam_key, cfg):
    """返回该品种族全部完整合约代码列表 [(code, strike, call_put), ...]。"""
    list_fn = getattr(ak, cfg["list_fn"])
    spot_fn = getattr(ak, cfg["spot_fn"])

    list_ret, err = timed_call(list_fn)
    if list_ret is None:
        print(f"  [{fam_key}] list_sina 失败: {err} -> 该族跳过")
        return None, err
    months = flatten_list(list_ret)
    print(f"  [{fam_key}] 月份代码 {len(months)} 个: {months}")

    contracts = []
    for mon in months:
        spot, serr = timed_call(spot_fn, symbol=mon)
        if spot is None or getattr(spot, "empty", True):
            print(f"    [{fam_key}] spot({mon}) 无数据/失败: {serr}")
            continue
        for col in ("看涨合约-标识", "看跌合约-标识"):
            if col in spot.columns:
                for _, row in spot.iterrows():
                    code = row.get(col)
                    if isinstance(code, str) and code.strip():
                        strike = row.get("行权价")
                        contracts.append(code.strip())
    # 去重
    seen, out = set(), []
    for c in contracts:
        if c not in seen:
            seen.add(c)
            out.append(c)
    print(f"  [{fam_key}] 枚举到完整合约 {len(out)} 个")
    return out, None


def main():
    print(f"akshare {ak.__version__}")
    print(f"输出目录: {OUT_DIR}\n")

    report = {}
    grand_rows = 0

    for fam_key, cfg in FAMILIES.items():
        print("=" * 70)
        print(f"品种族: {fam_key} (product={cfg['product']})")
        stat = {
            "attempted": 0,
            "succeeded": 0,
            "skipped_existing": 0,
            "skipped_timeout": 0,
            "skipped_nodata": 0,
            "skipped_error": 0,
            "rows": 0,
            "list_ok": True,
        }
        report[fam_key] = stat

        contracts, err = enumerate_contracts(fam_key, cfg)
        if contracts is None:
            stat["list_ok"] = False
            stat["list_error"] = err
            continue

        daily_fn = getattr(ak, cfg["daily_fn"])

        for code in contracts:
            stat["attempted"] += 1
            out_path = os.path.join(OUT_DIR, f"{code}.parquet")

            # 续跑：已存在非空则跳过
            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                stat["skipped_existing"] += 1
                continue

            df, derr = timed_call(daily_fn, symbol=code)
            if df is None:
                if derr == "TIMEOUT":
                    stat["skipped_timeout"] += 1
                else:
                    stat["skipped_error"] += 1
                    if stat["skipped_error"] <= 3:
                        print(f"    [{fam_key}] daily({code}) 错误: {derr}")
                continue
            if getattr(df, "empty", True) or len(df) == 0:
                stat["skipped_nodata"] += 1
                continue

            # 元数据列
            meta = parse_code(code)
            df = df.copy()
            df["family"] = fam_key
            df["contract"] = code
            df["strike"] = meta["strike"]
            df["expiry"] = meta["expiry"]
            df["call_put"] = meta["call_put"]
            # 规范 date 列
            df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date

            try:
                df.to_parquet(out_path, index=False)
            except Exception as e:
                stat["skipped_error"] += 1
                print(f"    [{fam_key}] 写 parquet 失败 {code}: {e}")
                continue

            stat["succeeded"] += 1
            stat["rows"] += len(df)

        grand_rows += stat["rows"]

    # ── 汇总报告 ───────────────────────────────────────────────────────────
    print("\n" + "#" * 70)
    print("汇总报告")
    print("#" * 70)
    for fam_key, s in report.items():
        print(f"\n[{fam_key}] list_ok={s['list_ok']}"
              + (f" list_error={s.get('list_error')}" if not s["list_ok"] else ""))
        print(f"  attempted={s['attempted']} succeeded={s['succeeded']} rows={s['rows']}")
        print(f"  skipped: existing={s['skipped_existing']} "
              f"timeout={s['skipped_timeout']} nodata={s['skipped_nodata']} "
              f"error={s['skipped_error']}")
    print(f"\n总行数 (本次写入): {grand_rows}")
    # 磁盘上所有 parquet 统计
    all_pq = [f for f in os.listdir(OUT_DIR) if f.endswith(".parquet")]
    print(f"磁盘上 parquet 文件总数: {len(all_pq)}")


if __name__ == "__main__":
    main()
