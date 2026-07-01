#!/usr/bin/env python3
"""
期权 + 房价(70城) + REITs 数据下载（akshare 1.18.64，务必 .venv/bin/python）。

本机网络实况：
  - 大量 akshare 接口走 eastmoney（push2/datacenter.eastmoney.com）在此环境被墙，
    且底层无超时会永久挂起。因此每次接口调用都用 call_timeout 硬超时兜底。
  - urllib/requests 需要 certifi 证书，否则 SSL 失败 —— 启动即设 SSL_CERT_FILE。

只下载“实测返回非空 DataFrame”的接口；走 eastmoney 被墙 / 需特殊参数 / 报错的记为 skipped。

实测结论（2026-07）：
  可用（sina 数据源，未被墙）：
    - 9 条 QVIX 日线波动率指数（index_option_*_qvix，约 2755 行 ≈ 11 年）  ← 核心历史
    - 9 条 QVIX 当日分钟快照（index_option_*_min_qvix，当日 ~239 行）
    - 3 条中金所股指期权主力日线（option_cffex_{sz50,hs300,zz1000}_daily_sina）
    - option_finance_board 期权链当日快照（按标的×到期月）
    - option_finance_sse_underlying 标的现价快照
    - option_sse_daily_sina 单合约日线（合约短命，历史很薄）
    - macro_china_new_house_price 70 城房价指数（约 370 行）
  被墙（eastmoney，SKIP）：
    - option_current_em / reits_realtime_em / reits_hist_em

存储：
  data/options_history/*.parquet
  data/realestate_history/*.parquet
每个结果一个 parquet；断点续传（已存在且非空则跳过）。

用法：
  .venv/bin/python scripts/download_options_realestate.py
  .venv/bin/python scripts/download_options_realestate.py --status
"""
from __future__ import annotations

import argparse
import os
import threading
from datetime import datetime
from pathlib import Path

# —— 证书 & 静音 akshare 的 SettingWithCopy/FutureWarning 噪声 ——
os.environ.setdefault("SSL_CERT_FILE", __import__("certifi").where())
import warnings  # noqa: E402

warnings.filterwarnings("ignore")

import pandas as pd  # noqa: E402
import akshare as ak  # noqa: E402

ROOT = Path(__file__).parent.parent
OPT_DIR = ROOT / "data" / "options_history"
RE_DIR = ROOT / "data" / "realestate_history"


def _log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# —— 硬超时：eastmoney 接口无超时会永久挂起，必须用独立线程 join 兜底 ——
def call_timeout(fn, timeout: int = 25, **kw):
    box: dict = {}

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


def _nonempty_df(x) -> bool:
    return isinstance(x, pd.DataFrame) and not x.empty


def _done(path: Path) -> bool:
    """断点续传：文件存在且非空（>500B，规避空占位）才算完成。"""
    return path.exists() and path.stat().st_size > 500


def _save(path: Path, df: pd.DataFrame) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return len(df)


# ── builder：每个返回 (df|None, reason)；df 非空即落盘 ────────────────────────

FINANCE_BOARD_SYMBOLS = [
    "华夏上证50ETF期权",
    "华泰柏瑞沪深300ETF期权",
    "南方中证500ETF期权",
    "华夏科创50ETF期权",
    "易方达科创50ETF期权",
    "嘉实沪深300ETF期权",
    "沪深300股指期权",
    "中证1000股指期权",
    "上证50股指期权",
]


def build_simple(fn, timeout: int = 25):
    """无参接口直接调用。"""
    def _b():
        r, e = call_timeout(fn, timeout=timeout)
        if e:
            return None, e
        if not _nonempty_df(r):
            return None, "空DataFrame"
        return r, None
    return _b


def build_finance_board(symbol: str):
    """期权链当日快照：先探一个月，响应了再遍历 01-12 拼接（避免挂起标的空转 12*超时）。"""
    def _b():
        # 先探一个月（当月）判活；被墙/挂起立即退出
        probe_month = datetime.now().strftime("%m")
        r, e = call_timeout(ak.option_finance_board, timeout=25,
                            symbol=symbol, end_month=probe_month)
        if e and "TIMEOUT" in e:
            return None, e
        frames = []
        for mm in [f"{m:02d}" for m in range(1, 13)]:
            r, e = call_timeout(ak.option_finance_board, timeout=20,
                                symbol=symbol, end_month=mm)
            if _nonempty_df(r):
                r = r.copy()
                r["到期月"] = mm
                r["标的"] = symbol
                frames.append(r)
        if not frames:
            return None, "全部到期月均空(可能非当前挂牌标的)"
        return pd.concat(frames, ignore_index=True), None
    return _b


def build_underlying_spot():
    """各 ETF/指数期权标的现价快照，拼成一张表。"""
    def _b():
        frames = []
        for sym in FINANCE_BOARD_SYMBOLS:
            r, e = call_timeout(ak.option_finance_sse_underlying, timeout=20, symbol=sym)
            if _nonempty_df(r):
                r = r.copy()
                r["期权品种"] = sym
                frames.append(r)
        if not frames:
            return None, "无标的现价返回"
        return pd.concat(frames, ignore_index=True), None
    return _b


def build_sse_contract_daily():
    """510050(50ETF) 当前挂牌看涨/看跌合约日线拼接；合约短命，历史很薄。"""
    def _b():
        month = datetime.now().strftime("%Y%m")
        frames = []
        for side in ["看涨期权", "看跌期权"]:
            codes, e = call_timeout(ak.option_sse_codes_sina, timeout=20,
                                    symbol=side, trade_date=month, underlying="510050")
            if not _nonempty_df(codes):
                continue
            for code in codes["期权代码"].astype(str).str.strip().tolist():
                d, e = call_timeout(ak.option_sse_daily_sina, timeout=20, symbol=code)
                if _nonempty_df(d):
                    d = d.copy()
                    d["合约代码"] = code
                    d["方向"] = side
                    frames.append(d)
        if not frames:
            return None, "无合约日线返回"
        return pd.concat(frames, ignore_index=True), None
    return _b


# ── 任务表：(名称, 存放目录, 文件名, builder) ─────────────────────────────────

QVIX_KEYS = ["1000index", "100etf", "300etf", "300index", "500etf",
             "50etf", "50index", "cyb", "kcb"]
CFFEX_KEYS = ["sz50", "hs300", "zz1000"]


def build_tasks():
    tasks = []
    # 1) QVIX 日线波动率指数（核心历史，~2755 行）
    for k in QVIX_KEYS:
        fn = getattr(ak, f"index_option_{k}_qvix")
        tasks.append(("options", f"qvix_{k}.parquet", build_simple(fn)))
    # 2) QVIX 当日分钟快照
    for k in QVIX_KEYS:
        fn = getattr(ak, f"index_option_{k}_min_qvix")
        tasks.append(("options", f"qvix_min_{k}.parquet", build_simple(fn)))
    # 3) 中金所股指期权主力日线
    for k in CFFEX_KEYS:
        fn = getattr(ak, f"option_cffex_{k}_daily_sina")
        tasks.append(("options", f"cffex_{k}_daily.parquet", build_simple(fn)))
    # 4) 期权链当日快照（按标的）
    for sym in FINANCE_BOARD_SYMBOLS:
        safe = sym.replace("/", "_")
        tasks.append(("options", f"board_{safe}.parquet", build_finance_board(sym)))
    # 5) 标的现价快照
    tasks.append(("options", "underlying_spot.parquet", build_underlying_spot()))
    # 6) 上交所单合约日线（薄历史）
    tasks.append(("options", "sse_510050_contract_daily.parquet", build_sse_contract_daily()))
    # 7) 70 城房价指数
    tasks.append(("realestate", "new_house_price_70cities.parquet",
                  build_simple(ak.macro_china_new_house_price)))
    # 8) REITs（eastmoney 被墙，仍作为任务纳入以便如实记录 SKIP）
    tasks.append(("realestate", "reits_realtime_em.parquet",
                  build_simple(ak.reits_realtime_em)))
    tasks.append(("realestate", "reits_hist_em_508097.parquet",
                  lambda: (lambda r, e: (r if _nonempty_df(r) else None, e or "空"))(
                      *call_timeout(ak.reits_hist_em, timeout=25, symbol="508097"))))
    return tasks


def _dir_for(kind: str) -> Path:
    return OPT_DIR if kind == "options" else RE_DIR


def run() -> None:
    OPT_DIR.mkdir(parents=True, exist_ok=True)
    RE_DIR.mkdir(parents=True, exist_ok=True)
    tasks = build_tasks()
    ok = skip = fail = 0
    results = []
    for kind, fname, builder in tasks:
        path = _dir_for(kind) / fname
        if _done(path):
            skip += 1
            _log(f"SKIP(已存在) {fname}")
            results.append((fname, "EXISTS", None))
            continue
        try:
            df, reason = builder()
        except Exception as e:  # noqa: BLE001
            df, reason = None, f"异常:{str(e)[:100]}"
        if _nonempty_df(df):
            n = _save(path, df)
            ok += 1
            _log(f"OK {fname}  {n} 行  {list(df.columns)[:6]}")
            results.append((fname, f"OK {n}行", None))
        else:
            fail += 1
            _log(f"SKIP(无数据) {fname}  原因: {reason}")
            results.append((fname, "SKIP", reason))
    _log(f"=== 完成 OK={ok} 已存在={skip} 无数据/跳过={fail} / 共{len(tasks)} ===")
    show_status()


def show_status() -> None:
    print("\n== 数据文件状态 ==")
    for kind, d in [("options_history", OPT_DIR), ("realestate_history", RE_DIR)]:
        print(f"[{kind}] {d}")
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
            except Exception:  # noqa: BLE001
                n = "?"
            print(f"  {f.name:42s} rows={n}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    if args.status:
        show_status()
        return
    run()


if __name__ == "__main__":
    main()
