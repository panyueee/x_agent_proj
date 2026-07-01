#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""中国宏观数据补采脚本（probe-and-keep race）。

针对此前被屏蔽 / 源迁移（mofcom 挂了、eastmoney 源变）的中国宏观指标，
用 akshare 的多个候选函数「赛跑」——逐个尝试，谁能返回数据就留谁（keep winners）。
每个成功的函数结果单独存为一份 parquet 到 data/macro_history/。

约束（按任务要求）：
- 只新增本文件，不改动其它文件；不做 git commit。
- 顶部注入 certifi CA，规避 SSL 证书问题。
- 每个 akshare 调用放进独立线程，join 25s；超时 / 报错 → 跳过。
- 可恢复：目标 parquet 已存在则跳过（--force 覆盖）。
- --status 只打印当前进度不抓取。

已在别处采过、这里不重复：GDP/CPI/PPI/PMI(yearly)/M2/LPR、Shibor。

用法：
    .venv/bin/python scripts/download_cn_macro_supplement.py            # 抓取
    .venv/bin/python scripts/download_cn_macro_supplement.py --status   # 查看进度
    .venv/bin/python scripts/download_cn_macro_supplement.py --force    # 强制重抓
"""

import os

# ---- SSL：必须在 import akshare 之前注入 CA ----
os.environ["SSL_CERT_FILE"] = __import__("certifi").where()

import argparse
import logging
import threading
import traceback
from datetime import datetime

# ---- 路径 ----
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT_DIR = os.path.join(ROOT, "data", "macro_history")
os.makedirs(OUT_DIR, exist_ok=True)

# ---- 日志 ----
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("cn_macro")

CALL_TIMEOUT = 25  # 秒，线程 join 上限

# 已在别处采过、跳过的逻辑指标（仅作说明，不参与抓取）
ALREADY_DONE = ["GDP", "CPI", "PPI", "PMI(yearly)", "M2", "LPR", "Shibor"]

# 赛跑分组：逻辑指标 -> 候选 akshare 函数列表（按优先级）。
# 逐个尝试，成功一个也继续尝试其余（都存下来，probe-and-keep）。
# 特意避开纯 eastmoney 依赖时选取存在多来源的函数。
GROUPS = {
    "社融_社会融资规模": ["macro_china_shrzgm"],
    "城镇调查失业率": ["macro_china_urban_unemployment"],
    "进口_同比": ["macro_china_imports_yoy"],
    "出口_同比": ["macro_china_exports_yoy"],
    "贸易差额": ["macro_china_trade_balance"],
    "工业增加值": ["macro_china_gyzjz", "macro_china_industrial_production_yoy"],
    "固定资产投资": ["macro_china_gdzctz"],
    "社会消费品零售": ["macro_china_consumer_goods_retail"],
    "外汇储备_黄金": ["macro_china_foreign_exchange_gold", "macro_china_fx_gold",
                       "macro_china_fx_reserves_yearly"],
    "货币供应": ["macro_china_money_supply", "macro_china_supply_of_money"],
    "存款准备金率": ["macro_china_reserve_requirement_ratio"],
    "央行债券发行": ["macro_china_bond_public"],
    "制造业PMI": ["macro_china_pmi"],
    "非制造业PMI": ["macro_china_non_man_pmi"],
}


def _target_path(fn_name: str) -> str:
    return os.path.join(OUT_DIR, f"{fn_name}.parquet")


def _call_with_timeout(fn, timeout=CALL_TIMEOUT):
    """在独立线程里调用 fn()，join(timeout)。

    返回 (status, result): status in {"OK","TIMEOUT","ERROR"}。
    """
    box = {}

    def _run():
        try:
            box["df"] = fn()
        except Exception as e:  # noqa: BLE001
            box["err"] = e
            box["tb"] = traceback.format_exc()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return "TIMEOUT", None
    if "err" in box:
        return "ERROR", box
    return "OK", box.get("df")


def probe_one(fn_name: str, force: bool):
    """尝试单个函数。返回 dict 结果记录。"""
    import akshare as ak

    path = _target_path(fn_name)
    if os.path.exists(path) and not force:
        try:
            import pandas as pd
            rows = len(pd.read_parquet(path))
        except Exception:  # noqa: BLE001
            rows = "?"
        log.info("SKIP  %-40s 已存在 (%s rows)", fn_name, rows)
        return {"fn": fn_name, "status": "SKIP", "rows": rows}

    if not hasattr(ak, fn_name):
        log.warning("MISS  %-40s akshare 无此函数", fn_name)
        return {"fn": fn_name, "status": "MISSING", "rows": 0}

    fn = getattr(ak, fn_name)
    status, res = _call_with_timeout(fn)

    if status == "TIMEOUT":
        log.warning("TIMEOUT %-38s 超过 %ss 跳过", fn_name, CALL_TIMEOUT)
        return {"fn": fn_name, "status": "TIMEOUT", "rows": 0}
    if status == "ERROR":
        msg = str(res.get("err"))[:120]
        log.warning("ERROR %-40s %s", fn_name, msg)
        return {"fn": fn_name, "status": "ERROR", "rows": 0, "err": msg}

    df = res
    try:
        import pandas as pd
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            log.warning("EMPTY %-40s 返回空", fn_name)
            return {"fn": fn_name, "status": "EMPTY", "rows": 0}
        df.to_parquet(path, index=False)
        cols = list(df.columns)
        log.info("OK    %-40s %d rows, cols=%s", fn_name, len(df), cols)
        return {"fn": fn_name, "status": "OK", "rows": len(df), "cols": cols}
    except Exception as e:  # noqa: BLE001
        log.warning("WRITE-FAIL %-35s %s", fn_name, str(e)[:100])
        return {"fn": fn_name, "status": "WRITE_ERROR", "rows": 0, "err": str(e)[:100]}


def show_status():
    log.info("== 状态 (%s) ==", OUT_DIR)
    log.info("已在别处采过(跳过): %s", ", ".join(ALREADY_DONE))
    for group, fns in GROUPS.items():
        parts = []
        for fn in fns:
            p = _target_path(fn)
            if os.path.exists(p):
                try:
                    import pandas as pd
                    parts.append(f"{fn}=OK({len(pd.read_parquet(p))})")
                except Exception:  # noqa: BLE001
                    parts.append(f"{fn}=OK(?)")
            else:
                parts.append(f"{fn}=-")
        log.info("  %-20s %s", group, " | ".join(parts))


def main():
    ap = argparse.ArgumentParser(description="中国宏观数据补采 probe-and-keep race")
    ap.add_argument("--status", action="store_true", help="只显示进度，不抓取")
    ap.add_argument("--force", action="store_true", help="强制重抓（覆盖已存在）")
    args = ap.parse_args()

    if args.status:
        show_status()
        return

    t0 = datetime.now()
    log.info("开始补采中国宏观数据 -> %s", OUT_DIR)
    results = []
    winners = {}  # group -> [fn...]
    for group, fns in GROUPS.items():
        log.info("---- %s ----", group)
        winners[group] = []
        for fn in fns:
            r = probe_one(fn, args.force)
            r["group"] = group
            results.append(r)
            if r["status"] in ("OK", "SKIP"):
                winners[group].append(f"{r['fn']}({r['rows']})")

    # ---- 汇总报告 ----
    log.info("=" * 60)
    log.info("汇总（用时 %.0fs）", (datetime.now() - t0).total_seconds())
    ok = [r for r in results if r["status"] == "OK"]
    skip = [r for r in results if r["status"] == "SKIP"]
    fail = [r for r in results if r["status"] not in ("OK", "SKIP")]

    log.info("成功(本次) %d 个:", len(ok))
    for r in ok:
        log.info("  [OK]   %-22s %-38s %d rows", r["group"], r["fn"], r["rows"])
    if skip:
        log.info("已存在(跳过) %d 个:", len(skip))
        for r in skip:
            log.info("  [SKIP] %-22s %-38s %s rows", r["group"], r["fn"], r["rows"])
    if fail:
        log.info("失败 %d 个:", len(fail))
        for r in fail:
            log.info("  [%s] %-20s %-38s %s", r["status"], r["group"], r["fn"], r.get("err", ""))

    log.info("-" * 60)
    log.info("各指标赢家(winners):")
    for group, w in winners.items():
        log.info("  %-20s %s", group, ", ".join(w) if w else "(无 — 全部失败)")


if __name__ == "__main__":
    main()
