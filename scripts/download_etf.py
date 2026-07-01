#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载 A 股主流 ETF 日线历史 → data/etf_history/*.parquet

数据源：新浪（akshare.fund_etf_hist_sina）。
  eastmoney(fund_etf_hist_em / push2his) 在本机被墙，实测 TIMEOUT，故不用。
  新浪源返回干净的原始日线 OHLCV + 成交额，无需复权，字段稳定。

特性：
  - 逐只下载，每只写一个 parquet(date,open,high,low,close,volume,amount,symbol,name)
  - 每次网络调用用 threading 超时保护（join 25s），挂起则跳过
  - 断点续传：已存在且非空的 parquet 默认跳过（--force 覆盖）
  - --status 扫描输出目录汇报进度
  - logging 记录每只结果

用法：
  .venv/bin/python scripts/download_etf.py            # 下载缺失的
  .venv/bin/python scripts/download_etf.py --force    # 全部重下
  .venv/bin/python scripts/download_etf.py --status   # 只看进度
"""
import os
# 必须在任何联网库导入前设置，绕开本机 SSL 证书问题
os.environ["SSL_CERT_FILE"] = __import__("certifi").where()

import sys
import time
import logging
import argparse
import threading
from pathlib import Path

import akshare as ak

# ---------------------------------------------------------------------------
# 主流 ETF 清单：(代码, 名称)
# ---------------------------------------------------------------------------
ETF_LIST = [
    # ---- 宽基 ----
    ("510300", "沪深300ETF"),
    ("510050", "上证50ETF"),
    ("510500", "中证500ETF"),
    ("588000", "科创50ETF"),
    ("159915", "创业板ETF"),
    ("159919", "300ETF(深)"),
    ("510330", "沪深300ETF(华夏)"),
    ("512100", "中证1000ETF"),
    ("510180", "上证180ETF"),
    ("159901", "深证100ETF"),
    ("159949", "创业板50ETF"),
    ("512160", "中证500ETF(深)"),
    ("515790", "光伏ETF"),
    # ---- 行业 / 主题 ----
    ("512880", "证券ETF"),
    ("512000", "券商ETF"),
    ("515030", "新能源车ETF"),
    ("512480", "半导体ETF"),
    ("516160", "新能源ETF"),
    ("512690", "酒ETF"),
    ("159928", "消费ETF"),
    ("512010", "医药ETF"),
    ("512170", "医疗ETF"),
    ("515000", "科技ETF"),
    ("515050", "5GETF"),
    ("512760", "芯片ETF"),
    ("512660", "军工ETF"),
    ("512800", "银行ETF"),
    ("512200", "房地产ETF"),
    ("515220", "煤炭ETF"),
    ("159611", "电力ETF"),
    ("516950", "基建ETF"),
    ("512400", "有色金属ETF"),
    ("159825", "农业ETF"),
    ("512980", "传媒ETF"),
    ("159766", "旅游ETF"),
    ("516010", "游戏ETF"),
    ("515880", "通信ETF"),
    ("159995", "芯片ETF(深)"),
    # ---- 海外 / 跨境 ----
    ("513050", "中概互联ETF"),
    ("513100", "纳指ETF"),
    ("159941", "纳指ETF(深)"),
    ("513500", "标普500ETF"),
    ("513180", "恒生科技ETF"),
    ("159920", "恒生ETF"),
    ("513330", "恒生互联网ETF"),
    # ---- 商品 ----
    ("518880", "黄金ETF"),
    ("159934", "黄金ETF(深)"),
    ("159980", "有色ETF"),
    # ---- 债券 ----
    ("511010", "国债ETF"),
    ("511260", "十年国债ETF"),
    ("511220", "城投债ETF"),
    ("511380", "可转债ETF"),
    ("511990", "华宝添益(货币)"),
]

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "etf_history"
CALL_TIMEOUT = 25          # 单次网络调用超时（秒）
SLEEP_BETWEEN = 0.3        # 每只之间限速，避免被新浪节流

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("download_etf")


def sina_symbol(code: str) -> str:
    """代码 → 新浪带交易所前缀符号。5/6 开头 → sh，1/0/3 开头 → sz。"""
    return ("sh" if code[0] in "56" else "sz") + code


def call_with_timeout(fn, timeout=CALL_TIMEOUT):
    """在子线程执行 fn，最多等 timeout 秒。

    返回 (status, result)：
      status ∈ {"ok", "timeout", "error"}
      ok    → result 为返回值
      error → result 为异常字符串
    """
    box = {}

    def worker():
        try:
            box["val"] = fn()
            box["ok"] = True
        except Exception as e:  # noqa: BLE001
            box["err"] = repr(e)[:300]
            box["ok"] = False

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return "timeout", None
    if box.get("ok"):
        return "ok", box["val"]
    return "error", box.get("err", "unknown")


def fetch_one(code: str):
    """抓取单只 ETF 日线，返回规整后的 DataFrame 或抛出/超时信息。"""
    sym = sina_symbol(code)
    return call_with_timeout(lambda: ak.fund_etf_hist_sina(symbol=sym))


def parquet_path(code: str, name: str) -> Path:
    return OUT_DIR / f"{code}_{name}.parquet"


def existing_path(code: str) -> Path | None:
    """按代码前缀匹配已有 parquet（名称可能变化时也能命中）。"""
    hits = list(OUT_DIR.glob(f"{code}_*.parquet"))
    return hits[0] if hits else None


def is_done(code: str) -> bool:
    p = existing_path(code)
    return p is not None and p.stat().st_size > 0


def cmd_status():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    done, missing = [], []
    for code, name in ETF_LIST:
        (done if is_done(code) else missing).append((code, name))
    print(f"输出目录: {OUT_DIR}")
    print(f"总计 {len(ETF_LIST)} 只 | 已完成 {len(done)} | 待下载 {len(missing)}")
    if missing:
        print("待下载: " + ", ".join(f"{c}" for c, _ in missing))
    return done, missing


def cmd_download(force: bool):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ok, skipped, failed = [], [], []
    total = len(ETF_LIST)
    for i, (code, name) in enumerate(ETF_LIST, 1):
        if not force and is_done(code):
            skipped.append(code)
            log.info("[%d/%d] %s %s 已存在，跳过", i, total, code, name)
            continue

        status, res = fetch_one(code)
        if status == "timeout":
            failed.append((code, name, "TIMEOUT"))
            log.warning("[%d/%d] %s %s 超时，跳过", i, total, code, name)
            time.sleep(SLEEP_BETWEEN)
            continue
        if status == "error":
            failed.append((code, name, res))
            log.warning("[%d/%d] %s %s 失败: %s", i, total, code, name, res)
            time.sleep(SLEEP_BETWEEN)
            continue

        df = res
        if df is None or len(df) == 0:
            failed.append((code, name, "EMPTY"))
            log.warning("[%d/%d] %s %s 返回空", i, total, code, name)
            time.sleep(SLEEP_BETWEEN)
            continue

        df = df.copy()
        df["symbol"] = code
        df["name"] = name
        try:
            df.to_parquet(parquet_path(code, name), index=False)
        except Exception as e:  # noqa: BLE001
            failed.append((code, name, f"WRITE:{e!r}"[:120]))
            log.warning("[%d/%d] %s %s 写盘失败: %s", i, total, code, name, e)
            time.sleep(SLEEP_BETWEEN)
            continue

        ok.append((code, name, len(df), df["date"].min(), df["date"].max()))
        log.info("[%d/%d] %s %s OK rows=%d %s~%s",
                 i, total, code, name, len(df), df["date"].min(), df["date"].max())
        time.sleep(SLEEP_BETWEEN)

    # 汇总
    print("\n" + "=" * 60)
    print(f"完成: 成功 {len(ok)} | 跳过(已存在) {len(skipped)} | 失败 {len(failed)}")
    if ok:
        print("\n成功样例:")
        for code, name, n, d0, d1 in ok:
            if code in ("510300", "588000"):
                print(f"  {code} {name}: {n} 行, {d0} ~ {d1}")
    if failed:
        print("\n失败清单:")
        for code, name, why in failed:
            print(f"  {code} {name}: {why}")
    return ok, skipped, failed


def main():
    ap = argparse.ArgumentParser(description="下载 A股主流 ETF 日线历史（新浪源）")
    ap.add_argument("--status", action="store_true", help="只显示进度，不下载")
    ap.add_argument("--force", action="store_true", help="覆盖已存在文件重新下载")
    args = ap.parse_args()

    if args.status:
        cmd_status()
        return
    cmd_download(force=args.force)


if __name__ == "__main__":
    main()
