#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
下载商品期权 FULL 逐合约日线历史（akshare SINA 端点）。

流程：
  品种(product) --option_commodity_contract_sina--> 月份合约(如 au2608)
             --option_commodity_contract_table_sina--> 逐行权价看涨/看跌合约码(如 au2608C1080)
             --option_commodity_hist_sina--> 该合约日线历史 -> 单独 parquet

约束（强制）：
  - 解释器 .venv/bin/python (3.14)
  - import akshare 前设置 SSL_CERT_FILE = certifi.where()
  - 每个 akshare 网络调用放进新线程，t.join(30)，超时判 TIMEOUT -> SKIP
  - 任何异常返回 None
  - 一合约一 parquet，已存在非空则跳过（可断点续跑）
  - 不做 git commit
"""

import os
import certifi

os.environ["SSL_CERT_FILE"] = certifi.where()

import sys
import time
import json
import threading
import traceback

import pandas as pd
import akshare as ak

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
ROOT = "/Users/pany19/Documents/x_agent_proj"
OUT_DIR = os.path.join(ROOT, "data", "options_history", "chains", "commodity")
LOG_PATH = os.path.join(ROOT, "data", "options_history", "commodity_download.log")
REPORT_PATH = os.path.join(ROOT, "data", "options_history", "commodity_report.json")
os.makedirs(OUT_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# 请求的品种 -> akshare 实际接受的品种字符串（经 live 页面确认）
# akshare 的合法品种取自 SINA optionsDP 页面的 active li 文本，动态变化。
# 下列为本次任务请求映射；不可用者在下方 UNAVAILABLE 说明。
# ---------------------------------------------------------------------------
PRODUCTS = [
    "沪铜期权",        # 铜期权
    "沪铝期权",        # 铝期权
    "黄金期权",
    "白银期权",
    "橡胶期权",        # 天然橡胶期权
    "豆粕期权",
    "豆油期权",
    "玉米期权",
    "铁矿石期权",
    "液化石油气期权",  # LPG期权
    "白糖期权",
    "棉花期权",
    "PTA期权",
    "甲醇期权",
    "菜籽粕期权",      # 菜粕期权
    "动力煤期权",
    "工业硅期权",
]
# 请求但 SINA 页面无对应品种（页面无 active li）-> 无法抓取：
UNAVAILABLE = ["锌期权", "原油期权", "棕榈油期权"]

TIMEOUT_SEC = 30
RETRY = 3           # 网络类错误重试次数
RETRY_SLEEP = 2.0   # 重试间隔
CALL_DELAY = 0.25   # 每次 hist 调用之间的礼貌延时


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def timed_call(fn):
    """在新线程里跑 fn；30s 内没结束判 TIMEOUT 返回 ('TIMEOUT', None)。
    异常返回 ('ERROR', exc)。成功返回 ('OK', result)。"""
    box = {}

    def worker():
        try:
            box["result"] = fn()
            box["status"] = "OK"
        except Exception as e:  # noqa
            box["status"] = "ERROR"
            box["error"] = e

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(TIMEOUT_SEC)
    if t.is_alive():
        return "TIMEOUT", None
    if box.get("status") == "OK":
        return "OK", box.get("result")
    return "ERROR", box.get("error")


def call_with_retry(fn, what):
    """带重试的 timed_call。TIMEOUT 不重试（直接放弃）。
    返回 (status, result)；status ∈ {OK, TIMEOUT, ERROR, NODATA}。"""
    last_err = None
    for attempt in range(RETRY):
        status, res = timed_call(fn)
        if status == "OK":
            return "OK", res
        if status == "TIMEOUT":
            log(f"    TIMEOUT: {what}")
            return "TIMEOUT", None
        # ERROR
        err = res
        msg = str(err)
        # 空数据：该合约从未成交，SINA 返回空 -> 合法 no-data，不重试
        if "No value to decode" in msg or "No JSON" in msg:
            return "NODATA", None
        last_err = err
        if attempt < RETRY - 1:
            time.sleep(RETRY_SLEEP)
    log(f"    ERROR: {what} :: {type(last_err).__name__}: {str(last_err)[:80]}")
    return "ERROR", None


def get_months(product):
    def fn():
        return ak.option_commodity_contract_sina(symbol=product)
    status, df = call_with_retry(fn, f"contract_sina({product})")
    if status != "OK" or df is None or df.empty:
        return status, []
    return "OK", df["合约"].dropna().astype(str).tolist()


def get_contract_codes(product, month):
    def fn():
        return ak.option_commodity_contract_table_sina(symbol=product, contract=month)
    status, df = call_with_retry(fn, f"contract_table_sina({product},{month})")
    if status != "OK" or df is None or df.empty:
        return status, []
    calls = df["看涨合约-看涨期权合约"].dropna().astype(str).tolist()
    puts = df["看跌合约-看跌期权合约"].dropna().astype(str).tolist()
    codes = [(c, "C") for c in calls] + [(p, "P") for p in puts]
    return "OK", codes


def get_hist(code):
    def fn():
        return ak.option_commodity_hist_sina(symbol=code)
    return call_with_retry(fn, f"hist_sina({code})")


def parquet_path(code):
    # 合约码为 ascii（如 au2608C1080），各交易所前缀唯一，直接用作文件名
    safe = "".join(ch for ch in code if ch.isalnum() or ch in "._-")
    return os.path.join(OUT_DIR, f"{safe}.parquet")


def main():
    log("=" * 70)
    log(f"START commodity option chain download. products={len(PRODUCTS)} "
        f"unavailable={UNAVAILABLE}")

    report = {}
    grand_rows = 0

    for product in PRODUCTS:
        pstat = {
            "product": product,
            "months": [],
            "months_status": None,
            "attempted": 0,   # 尝试抓 hist 的合约数
            "succeeded": 0,   # 成功写 parquet
            "skipped_existing": 0,
            "nodata": 0,
            "timeout": 0,
            "error": 0,
            "rows": 0,
        }
        log("-" * 60)
        log(f"PRODUCT {product}")

        mstatus, months = get_months(product)
        pstat["months_status"] = mstatus
        pstat["months"] = months
        if mstatus != "OK":
            log(f"  months FAILED status={mstatus}; skip product")
            report[product] = pstat
            continue
        log(f"  months({len(months)}): {months}")

        for month in months:
            cstatus, codes = get_contract_codes(product, month)
            if cstatus != "OK":
                log(f"  month {month}: contract_table {cstatus}; skip month")
                continue
            log(f"  month {month}: {len(codes)} contracts")

            for code, cp in codes:
                path = parquet_path(code)
                # 断点续跑：已有非空 parquet 跳过
                if os.path.exists(path) and os.path.getsize(path) > 0:
                    pstat["skipped_existing"] += 1
                    continue

                pstat["attempted"] += 1
                hstatus, df = get_hist(code)
                time.sleep(CALL_DELAY)

                if hstatus == "NODATA":
                    pstat["nodata"] += 1
                    continue
                if hstatus == "TIMEOUT":
                    pstat["timeout"] += 1
                    continue
                if hstatus != "OK" or df is None or df.empty:
                    pstat["error"] += 1
                    continue

                df = df.copy()
                df["product"] = product
                df["contract"] = code
                df["call_put"] = cp
                df["month"] = month
                # date 已由 akshare 归一化为 date 对象；保持列名 date
                try:
                    df.to_parquet(path, index=False)
                    pstat["succeeded"] += 1
                    pstat["rows"] += len(df)
                except Exception as e:
                    log(f"    WRITE ERROR {code}: {e}")
                    pstat["error"] += 1

        grand_rows += pstat["rows"]
        log(f"  DONE {product}: attempted={pstat['attempted']} "
            f"succeeded={pstat['succeeded']} skipped_existing={pstat['skipped_existing']} "
            f"nodata={pstat['nodata']} timeout={pstat['timeout']} error={pstat['error']} "
            f"rows={pstat['rows']}")
        report[product] = pstat
        # 每个品种结束落盘 report，避免崩溃丢失
        with open(REPORT_PATH, "w", encoding="utf-8") as f:
            json.dump({"products": report, "unavailable": UNAVAILABLE,
                       "grand_rows": grand_rows}, f, ensure_ascii=False, indent=2)

    log("=" * 70)
    log(f"ALL DONE. grand_total_rows={grand_rows}")
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump({"products": report, "unavailable": UNAVAILABLE,
                   "grand_rows": grand_rows}, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log("FATAL:\n" + traceback.format_exc())
        sys.exit(1)
