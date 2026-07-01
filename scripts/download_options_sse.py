#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
构建上交所 ETF 期权（SSE ETF options）逐合约完整期权链。

数据源：akshare 的新浪财经（SINA）接口。
- option_sse_list_sina(symbol, exchange="null")    -> 某标的的到期月份列表
- option_sse_codes_sina(symbol, trade_date, underlying) -> 看涨/看跌合约代码列表
- option_sse_daily_sina(symbol=合约代码)           -> 单合约日线 OHLC + 成交量
- option_sse_greeks_sina(symbol=合约代码)          -> 单合约希腊字母 + 隐含波动率（快照）

每个合约输出一个 parquet：
    data/options_history/chains/sse/<合约代码>.parquet

规则：
- import akshare 之前设置 SSL_CERT_FILE=certifi.where()
- 每个联网调用都放进独立 worker 线程，t.join(30)；超时视为 TIMEOUT 并跳过，异常返回 None
- 可续跑：已存在且非空的 parquet 直接跳过
- greeks 失败不影响日线写盘；仅记录 greeks 不可用
"""

import os
import certifi

# 必须在 import akshare 之前设置证书路径
os.environ["SSL_CERT_FILE"] = certifi.where()

import sys
import threading
import time

import pandas as pd
import akshare as ak

# ----------------------------------------------------------------------------
# 配置
# ----------------------------------------------------------------------------
CALL_TIMEOUT = 30  # 每个联网调用的超时秒数

OUT_DIR = "/Users/pany19/Documents/x_agent_proj/data/options_history/chains/sse"

# 标的：代码 -> (list_sina 用的 cate 名称, 人类可读名)
UNDERLYINGS = {
    "510050": ("50ETF", "华夏上证50ETF"),
    "510300": ("300ETF", "华泰柏瑞沪深300ETF"),
    "510500": ("500ETF", "南方中证500ETF"),
    "588000": ("科创50ETF", "华夏科创50ETF"),
    "588080": ("科创50ETF", "易方达科创50ETF"),
}

CALL_PUT = {"看涨期权": "call", "看跌期权": "put"}


# ----------------------------------------------------------------------------
# 通用超时封装：在独立线程中运行网络调用，超时或异常返回 (None, reason)
# ----------------------------------------------------------------------------
def run_with_timeout(fn, timeout=CALL_TIMEOUT):
    """返回 (value, error)。error 为 None 表示成功；否则为 'TIMEOUT' 或异常字符串。"""
    result = {}

    def worker():
        try:
            result["val"] = fn()
        except Exception as e:  # noqa: BLE001
            result["err"] = repr(e)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None, "TIMEOUT"
    if "err" in result:
        return None, result["err"]
    return result.get("val"), None


# ----------------------------------------------------------------------------
# 1) 枚举所有在市合约：underlying x expiry_month x call/put
# ----------------------------------------------------------------------------
def enumerate_contracts():
    """返回 dict: 合约代码 -> 元数据 dict。"""
    contracts = {}

    for ucode, (cate, uname) in UNDERLYINGS.items():
        months, err = run_with_timeout(
            lambda c=cate: ak.option_sse_list_sina(symbol=c, exchange="null")
        )
        if months is None:
            print(f"[list] {ucode} {cate}: FAILED ({err})", flush=True)
            continue
        print(f"[list] {ucode} {cate}: months={months}", flush=True)

        for month in months:
            for cp_cn, cp_en in CALL_PUT.items():
                df, err = run_with_timeout(
                    lambda s=cp_cn, m=month, u=ucode: ak.option_sse_codes_sina(
                        symbol=s, trade_date=m, underlying=u
                    )
                )
                if df is None:
                    print(
                        f"[codes] {ucode} {month} {cp_cn}: FAILED ({err})",
                        flush=True,
                    )
                    continue
                if df.empty or "期权代码" not in df.columns:
                    continue
                for code in df["期权代码"].tolist():
                    code = str(code).strip()
                    if not code:
                        continue
                    # 同一代码可能重复出现，仅记录一次
                    contracts.setdefault(
                        code,
                        {
                            "contract": code,
                            "underlying": ucode,
                            "underlying_name": uname,
                            "expiry": month,
                            "call_put": cp_en,
                        },
                    )
    return contracts


# ----------------------------------------------------------------------------
# 2) 单合约拉取：日线 + 希腊字母，合并为一张表
# ----------------------------------------------------------------------------
def build_contract_frame(meta):
    """返回 (DataFrame or None, status_dict)。"""
    code = meta["contract"]
    status = {"greeks": "n/a"}

    daily, err = run_with_timeout(lambda: ak.option_sse_daily_sina(symbol=code))
    if daily is None:
        status["daily"] = err
        return None, status
    if daily.empty:
        status["daily"] = "empty"
        return None, status
    status["daily"] = "ok"

    df = daily.rename(
        columns={
            "日期": "date",
            "开盘": "open",
            "最高": "high",
            "最低": "low",
            "收盘": "close",
            "成交量": "volume",
        }
    ).copy()
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")

    # 元数据列
    df["contract"] = meta["contract"]
    df["underlying"] = meta["underlying"]
    df["underlying_name"] = meta["underlying_name"]
    df["expiry"] = meta["expiry"]
    df["call_put"] = meta["call_put"]
    df["strike"] = pd.NA

    # 希腊字母（快照，长表 字段/值 -> 广播到每行）
    greeks, gerr = run_with_timeout(lambda: ak.option_sse_greeks_sina(symbol=code))
    if greeks is None:
        status["greeks"] = f"unavailable ({gerr})"
    elif greeks.empty or "字段" not in greeks.columns:
        status["greeks"] = "unavailable (bad-shape)"
    else:
        status["greeks"] = "ok"
        g = dict(zip(greeks["字段"], greeks["值"]))
        # 行权价
        strike = g.get("行权价")
        if strike is not None:
            df["strike"] = strike
        # 广播希腊字母/隐含波动率快照
        mapping = {
            "Delta": "greek_delta",
            "Gamma": "greek_gamma",
            "Theta": "greek_theta",
            "Vega": "greek_vega",
            "隐含波动率": "greek_iv",
            "理论价值": "greek_theo",
            "交易代码": "trade_code",
            "期权合约简称": "contract_name",
        }
        for src, dst in mapping.items():
            df[dst] = g.get(src, pd.NA)

    return df, status


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------
def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    print("=== 枚举在市合约 ===", flush=True)
    contracts = enumerate_contracts()
    total = len(contracts)
    print(f"=== 共枚举到 {total} 个合约 ===", flush=True)

    attempted = 0
    succeeded = 0
    skipped_existing = 0
    skipped_failed = 0
    total_rows = 0
    greeks_ok = 0
    greeks_bad = 0
    daily_ok = 0
    daily_bad = 0
    fail_reasons = {}

    for i, (code, meta) in enumerate(sorted(contracts.items()), 1):
        out_path = os.path.join(OUT_DIR, f"{code}.parquet")
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            skipped_existing += 1
            continue

        attempted += 1
        df, status = build_contract_frame(meta)

        if status.get("daily") == "ok":
            daily_ok += 1
        else:
            daily_bad += 1
        if status.get("greeks") == "ok":
            greeks_ok += 1
        elif status.get("greeks", "n/a") != "n/a":
            greeks_bad += 1

        if df is None:
            skipped_failed += 1
            reason = status.get("daily", "unknown")
            fail_reasons[reason] = fail_reasons.get(reason, 0) + 1
            print(
                f"[{i}/{total}] {code} SKIP daily={status.get('daily')}",
                flush=True,
            )
            continue

        df.to_parquet(out_path, index=False)
        succeeded += 1
        total_rows += len(df)
        print(
            f"[{i}/{total}] {code} OK rows={len(df)} "
            f"greeks={status.get('greeks')}",
            flush=True,
        )

    # ---- 汇报 ----
    print("\n================ 汇报 ================", flush=True)
    print(f"枚举合约总数 (total):      {total}")
    print(f"已存在跳过 (skip-existing): {skipped_existing}")
    print(f"本次尝试 (attempted):      {attempted}")
    print(f"成功写盘 (succeeded):      {succeeded}")
    print(f"失败跳过 (skip-failed):    {skipped_failed}")
    print(f"总行数 (total_rows):       {total_rows}")
    print(f"daily 成功/失败:           {daily_ok}/{daily_bad}")
    print(f"greeks 成功/不可用:        {greeks_ok}/{greeks_bad}")
    if fail_reasons:
        print("失败原因分布:")
        for r, n in sorted(fail_reasons.items(), key=lambda x: -x[1]):
            print(f"    {r}: {n}")
    print("=====================================", flush=True)


if __name__ == "__main__":
    sys.exit(main())
