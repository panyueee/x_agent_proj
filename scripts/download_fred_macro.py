#!/usr/bin/env python3
"""
美国 / 全球宏观数据管道（FRED 无密钥 CSV）。

数据源：FRED 图表 CSV 端点，无需 API key：
  https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES>
返回 CSV：observation_date,<SERIES>；缺失值为 "."。

本机网络现实：https 需要 certifi 根证书，否则 CERTIFICATE_VERIFY_FAILED。
本文件在 import 阶段即设置 SSL_CERT_FILE。

存储：每个指标一个 parquet → data/macro_history/fred/<series_id>.parquet
列：date(datetime64), value(float), series_id(str), name(str中文名)。
断点续传：已存在且非空的 parquet 跳过；--status 列出已有 parquet + 行数 + 最新日期。
错误重试：每个 series 最多重试 3 次（指数退避）。

用法（务必 .venv/bin/python）：
  .venv/bin/python scripts/download_fred_macro.py
  .venv/bin/python scripts/download_fred_macro.py --status
  .venv/bin/python scripts/download_fred_macro.py --force   # 忽略已存在，全量重下
"""
from __future__ import annotations

import argparse
import io
import os
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

# SSL：FRED https 需要 certifi 根证书，否则报 CERTIFICATE_VERIFY_FAILED
os.environ["SSL_CERT_FILE"] = __import__("certifi").where()

import pandas as pd

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data" / "macro_history" / "fred"
CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"

# ~40 个核心 FRED 指标：series_id -> 中文名
SERIES: dict[str, str] = {
    # ---- 物价 / 通胀 ----
    "CPIAUCSL": "CPI 消费者物价指数(所有项,季调)",
    "CPILFESL": "核心CPI(剔除食品能源)",
    "PCEPI": "PCE 个人消费支出物价指数",
    "PCEPILFE": "核心PCE(美联储目标)",
    "PPIACO": "PPI 生产者物价指数(所有商品)",
    "T5YIE": "5年盈亏平衡通胀预期",
    "T10YIE": "10年盈亏平衡通胀预期",
    # ---- 就业 ----
    "UNRATE": "失业率",
    "PAYEMS": "非农就业总人数",
    "ICSA": "首次申领失业金人数(初请)",
    "CCSA": "续请失业金人数",
    "CIVPART": "劳动参与率",
    "AHETPI": "非管理层平均时薪",
    "JTSJOL": "JOLTS 职位空缺数",
    # ---- 增长 / 产出 ----
    "GDPC1": "实际GDP(链式2017美元)",
    "GDP": "名义GDP",
    "INDPRO": "工业生产指数",
    "TCU": "产能利用率",
    "GDPPOT": "潜在实际GDP",
    # ---- 消费 / 情绪 ----
    "RSAFS": "零售与食品服务销售额",
    "UMCSENT": "密歇根大学消费者信心指数",
    "PCE": "个人消费支出",
    "PSAVERT": "个人储蓄率",
    "DSPIC96": "实际可支配个人收入",
    # ---- 利率 / 货币 ----
    "FEDFUNDS": "联邦基金有效利率(月)",
    "DFF": "联邦基金有效利率(日)",
    "SOFR": "有担保隔夜融资利率",
    "M2SL": "M2 货币供应量",
    "M1SL": "M1 货币供应量",
    "DGS10": "10年期美债收益率",
    "DGS2": "2年期美债收益率",
    "DGS30": "30年期美债收益率",
    "DGS3MO": "3月期美债收益率",
    "T10Y2Y": "10年-2年期限利差",
    "T10Y3M": "10年-3月期限利差",
    "WALCL": "美联储总资产(资产负债表)",
    # ---- 房地产 ----
    "HOUST": "新屋开工数",
    "PERMIT": "新屋营建许可",
    "CSUSHPINSA": "标普/凯斯席勒全国房价指数",
    "MORTGAGE30US": "30年固定按揭利率",
    "EXHOSLUSM495S": "成屋销售",
    # ---- 市场 / 汇率 / 商品 ----
    "VIXCLS": "VIX 波动率指数",
    "DTWEXBGS": "美元指数(广义,名义)",
    "DCOILWTICO": "WTI 原油价格",
    "DEXCHUS": "美元兑人民币汇率",
    "DEXUSEU": "美元兑欧元汇率",
    "DEXJPUS": "日元兑美元汇率",
    "DEXUSUK": "英镑兑美元汇率",
    "DEXKOUS": "韩元兑美元汇率",
    "DHHNGSP": "亨利港天然气现货价",
    "GVZCLS": "黄金ETF波动率指数(GVZ)",
    # ---- 信用 / 其它领先指标 ----
    "BAMLH0A0HYM2": "美银美国高收益债期权调整利差",
    "BAMLC0A0CM": "美银美国投资级公司债利差",
    "DGORDER": "耐用品新订单",
    "STLFSI4": "圣路易斯联储金融压力指数",
}


def _log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _out_path(sid: str) -> Path:
    return DATA_DIR / f"{sid}.parquet"


def _fetch_csv(sid: str, retries: int = 3) -> str:
    """下载单个 series 的 CSV 文本，失败重试（指数退避）。"""
    url = CSV_URL.format(sid=sid)
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            # 注意：给 FRED 加自定义 User-Agent 会被其挂起（读超时），必须用默认 UA
            with urllib.request.urlopen(url, timeout=45) as resp:
                return resp.read().decode("utf-8")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
            last_err = e
            wait = 2 ** attempt
            _log(f"  [{sid}] 第{attempt}次失败: {str(e)[:80]}，{wait}s 后重试")
            time.sleep(wait)
    raise RuntimeError(f"{sid} 下载失败(重试{retries}次): {last_err}")


def _parse(sid: str, name: str, csv_text: str) -> pd.DataFrame:
    """解析 FRED CSV → date,value,series_id,name；剔除缺失('.')行。"""
    df = pd.read_csv(io.StringIO(csv_text))
    if df.shape[1] < 2:
        raise ValueError(f"{sid} CSV 列数异常: {list(df.columns)}")
    date_col, val_col = df.columns[0], df.columns[1]
    df = df.rename(columns={date_col: "date", val_col: "value"})
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")  # '.' -> NaN
    df = df.dropna(subset=["date", "value"]).reset_index(drop=True)
    df["series_id"] = sid
    df["name"] = name
    return df[["date", "value", "series_id", "name"]]


def _read_meta(path: Path) -> tuple[int, str]:
    """读取已存在 parquet 的行数与最新日期（用于 --status）。"""
    try:
        df = pd.read_parquet(path)
        if df.empty:
            return 0, "-"
        return len(df), str(pd.to_datetime(df["date"]).max().date())
    except Exception as e:  # noqa: BLE001
        return -1, f"读取失败:{str(e)[:40]}"


def do_status() -> None:
    _log(f"=== FRED 宏观数据状态（{DATA_DIR}）===")
    have = 0
    for sid, name in SERIES.items():
        path = _out_path(sid)
        if path.exists():
            rows, latest = _read_meta(path)
            have += 1
            _log(f"  ✓ {sid:16s} {rows:>6} 行  最新 {latest:12s} {name}")
        else:
            _log(f"  ✗ {sid:16s} {'':6}       {'':12}  {name}  <缺失>")
    _log(f"总计 {len(SERIES)} 个指标，已落盘 {have}，缺失 {len(SERIES) - have}")


def do_download(force: bool = False) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    total = len(SERIES)
    ok, skip, fail = 0, 0, 0
    failures: list[tuple[str, str]] = []
    _log(f"=== 开始下载 {total} 个 FRED 指标 → {DATA_DIR} ===")
    for i, (sid, name) in enumerate(SERIES.items(), 1):
        path = _out_path(sid)
        if path.exists() and not force:
            try:
                if not pd.read_parquet(path).empty:
                    _log(f"[{i}/{total}] 跳过 {sid}（已存在）")
                    skip += 1
                    continue
            except Exception:  # noqa: BLE001
                pass  # 损坏则重下
        try:
            csv_text = _fetch_csv(sid)
            df = _parse(sid, name, csv_text)
            if df.empty:
                raise ValueError("解析后为空（全部缺失值？）")
            df.to_parquet(path, index=False)
            latest = str(df["date"].max().date())
            _log(f"[{i}/{total}] ✓ {sid:16s} {len(df):>6} 行  最新 {latest}  {name}")
            ok += 1
        except Exception as e:  # noqa: BLE001
            _log(f"[{i}/{total}] ✗ {sid} 失败: {str(e)[:120]}")
            failures.append((sid, str(e)[:120]))
            fail += 1
        time.sleep(0.3)  # 轻微限速，友好对待 FRED

    _log("=== 完成 ===")
    _log(f"总计 {total}，新增成功 {ok}，跳过(已存在) {skip}，失败 {fail}")
    if failures:
        _log("失败列表：")
        for sid, err in failures:
            _log(f"  - {sid}: {err}")


def main() -> None:
    ap = argparse.ArgumentParser(description="FRED 美国/全球宏观数据下载管道（无密钥）")
    ap.add_argument("--status", action="store_true", help="仅列出已落盘指标状态")
    ap.add_argument("--force", action="store_true", help="忽略已存在文件，全量重下")
    args = ap.parse_args()
    if args.status:
        do_status()
    else:
        do_download(force=args.force)


if __name__ == "__main__":
    main()
