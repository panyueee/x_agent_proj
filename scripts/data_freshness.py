#!/usr/bin/env python3
"""
数据新鲜度监控：遍历各数据源，报告"最新日期 + 落后今天几天"，超阈值标红。

背景：本项目大量行情/宏观数据靠定时增量维护，一旦某个源静默落后（脚本挂了/接口变了/
没人跑），下游因子、digest、回测都会用到陈旧数据而毫无察觉。本脚本把"最新到哪天"显式
化，配合退出码给 cron 告警。

用法（务必用项目 venv，裸 python 是坏的 anaconda 3.8）：
    .venv/bin/python scripts/data_freshness.py           # 人类可读报告
    .venv/bin/python scripts/data_freshness.py --json     # 机器可解析 JSON

退出码：任一 **告警级(alarm=True)** 数据源滞后 → 1（便于 cron 告警）；全新鲜 → 0。
信息级源（price_bars 遗留快照、rag.db 无入库时间戳）永远不影响退出码，只做展示。

设计要点（见 docs/ops_automation.md）：
- 行情阈值用日历日 ~4 天，吸收周末（周五收盘→周一是 3 天，不该报警）。
- 宏观分档：月度 ~45 天、季度 ~100 天；CPI/PPI 等逐指标单独盯，避免被同目录里
  新鲜的 LPR 把 group-max 抬高、掩盖掉陈旧指标。
- 日期列各源不一：date / 日期 / day / timestamp / TRADE_DATE / 月份(如"2025年6月份")。
  解析不出日期(NaT) → 视为陈旧/异常，绝不当作"新鲜"蒙混过关。
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"
DB = ROOT / "output" / "x_agent.db"
RAG_DB = ROOT / "output" / "rag.db"

# 状态标记（cron/终端可读）
FRESH, STALE, BROKEN, INFO = "🟢 FRESH", "🔴 STALE", "⚠️ BROKEN", "⚪ INFO"

# 候选日期列名（按优先级）与"周末宽限"
DATE_COL_CANDIDATES = ["date", "日期", "day", "timestamp", "TRADE_DATE", "月份", "月度"]


# ── 纯函数：可被单测直接喂合成日期，不碰文件 ──────────────────────────────────

def lag_days(latest: dt.date | None, today: dt.date) -> int | None:
    """最新日期落后今天几天；latest 为 None(解析失败/无数据) → None。"""
    if latest is None:
        return None
    return (today - latest).days


def classify(latest: dt.date | None, today: dt.date, threshold_days: int,
             alarm: bool) -> tuple[str, int | None]:
    """
    纯判定：给定最新日期/今天/阈值，返回 (状态, 落后天数)。

    - latest 为 None → BROKEN（解析不出日期，必须暴露，绝不当 FRESH）。
    - 落后 > 阈值 → STALE。
    - 否则 FRESH。
    - alarm=False 的信息级源：无论新旧都返回 INFO（不参与退出码），但仍算落后天数。
    """
    lag = lag_days(latest, today)
    if not alarm:
        return INFO, lag
    if latest is None:
        return BROKEN, None
    if lag is not None and lag > threshold_days:
        return STALE, lag
    return FRESH, lag


# ── 日期解析：处理各源杂乱的日期列 ────────────────────────────────────────────

_YM_RE = re.compile(r"(\d{4})\D+(\d{1,2})")  # "2025年6月份" / "2025-06" 等
# 未来太远的日期视为脏数据（如 FRED 里混进的 2036 预测行），不让它抬高 group-max
_FUTURE_SLACK = dt.timedelta(days=7)


def _coerce_date(v) -> dt.date | None:
    """把 parquet 统计里的 max 值（str/bytes/date/datetime/np）转成 date。"""
    if v is None:
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    if isinstance(v, bytes):
        try:
            v = v.decode("utf-8", "ignore")
        except Exception:
            return None
    s = str(v)
    # 纯 ISO 日期字符串：字典序 max == 时间序 max，最常见的快路径
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    mm = _YM_RE.search(s)
    if mm and 1 <= int(mm.group(2)) <= 12:
        return dt.date(int(mm.group(1)), int(mm.group(2)), 1)
    return None


def _max_via_pq_stats(path: Path, date_cols: list[str]):
    """
    只读 parquet footer 的列统计拿 max（不读数据页），比整表读快一个数量级。
    返回 (date_or_None, handled)；handled=False 表示要退回 pandas 慢路径。
    """
    try:
        import pyarrow.parquet as pq
        pf = pq.ParquetFile(path)
        names = pf.schema_arrow.names
    except Exception:
        return None, False
    col = next((c for c in date_cols if c in names), None) \
        or next((c for c in DATE_COL_CANDIDATES if c in names), None)
    if col is None:
        return None, True  # 确实没有任何日期列 → BROKEN，无需再读
    ci = names.index(col)
    md = pf.metadata
    best: dt.date | None = None
    for rg in range(md.num_row_groups):
        try:
            st = md.row_group(rg).column(ci).statistics
        except Exception:
            return None, False
        if st is None or not st.has_min_max:
            return None, False  # 无统计 → 退回慢路径
        d = _coerce_date(st.max)
        if d and (best is None or d > best):
            best = d
    return best, True


def _parse_series_max(series) -> dt.date | None:
    """把一列（可能是 date/日期/day/TRADE_DATE/月份）解析成最大日期。失败 → None。"""
    import pandas as pd

    # 先试标准 datetime 解析
    parsed = pd.to_datetime(series, errors="coerce")
    if parsed.notna().any():
        return parsed.max().date()

    # 退化：形如 "2025年6月份" 的中文月份，取每月 1 日近似
    best: dt.date | None = None
    for v in series.dropna().astype(str):
        m = _YM_RE.search(v)
        if not m:
            continue
        y, mo = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            d = dt.date(y, mo, 1)
            if best is None or d > best:
                best = d
    return best


def _file_max_date(path: Path, date_cols: list[str]) -> dt.date | None:
    """读一个 parquet，按候选列找出最大日期。全失败 → None。
    先走 pyarrow footer 统计的快路径，拿不到再退回 pandas 读列。"""
    import pandas as pd

    d, handled = _max_via_pq_stats(path, date_cols)
    if handled:
        return d  # 快路径成功（含"确实没有日期列"→None）

    try:
        df = pd.read_parquet(path)
    except Exception:
        return None
    if df.empty:
        return None
    cols = [c for c in date_cols if c in df.columns]
    if not cols:
        # 没有指定列时，兜底扫所有候选
        cols = [c for c in DATE_COL_CANDIDATES if c in df.columns]
    best: dt.date | None = None
    for c in cols:
        d = _parse_series_max(df[c])
        if d and (best is None or d > best):
            best = d
    return best


# ── 各类数据源检查器 ──────────────────────────────────────────────────────────

def check_parquet_dir(name: str, subpath: str, date_cols: list[str],
                      threshold: int, alarm: bool, recursive: bool,
                      today: dt.date, grace: int = 3) -> dict:
    """
    一个 parquet 目录组：group-max 代表"该源最后一次成功更新到哪天"。
    另报 stale_files（落后 group-max 超过 grace 天的文件数，抓"部分静默落后"）
    与 broken_files（解析不出日期的文件数）。
    """
    d = DATA / subpath
    pattern = str(d / ("**/*.parquet" if recursive else "*.parquet"))
    files = glob.glob(pattern, recursive=recursive)
    if not files:
        return {"source": name, "status": BROKEN if alarm else INFO,
                "latest": None, "lag": None, "threshold": threshold,
                "alarm": alarm, "n_files": 0, "stale_files": 0,
                "broken_files": 0, "note": "无 parquet 文件"}

    per_file: list[dt.date | None] = [_file_max_date(Path(f), date_cols) for f in files]
    # 丢弃明显未来的脏日期（如 FRED 混进的预测行），否则会抬高 group-max 掩盖陈旧
    future_cut = today + _FUTURE_SLACK
    n_future = sum(1 for x in per_file if x is not None and x > future_cut)
    dated = [x for x in per_file if x is not None and x <= future_cut]
    broken_files = sum(1 for x in per_file if x is None)
    group_max = max(dated) if dated else None
    stale_files = 0
    if group_max is not None:
        cutoff = group_max - dt.timedelta(days=grace)
        stale_files = sum(1 for x in dated if x < cutoff)

    status, lag = classify(group_max, today, threshold, alarm)
    laggard = f"，{stale_files} 个文件落后组内最新>{grace}天" if stale_files else ""
    broken = f"，{broken_files} 个文件无有效日期" if broken_files else ""
    future = f"，{n_future} 个文件含未来脏日期(已忽略)" if n_future else ""
    return {"source": name, "status": status, "latest": group_max.isoformat() if group_max else None,
            "lag": lag, "threshold": threshold, "alarm": alarm, "n_files": len(files),
            "stale_files": stale_files, "broken_files": broken_files,
            "note": f"{len(files)} 文件{laggard}{broken}{future}"}


def check_macro_indicator(label: str, filename: str, date_cols: list[str],
                          threshold: int, today: dt.date) -> dict:
    """单个宏观指标文件（逐指标盯，避免被同目录新鲜指标掩盖陈旧指标）。"""
    path = DATA / "macro_history" / filename
    if not path.exists():
        return {"source": f"macro:{label}", "status": BROKEN, "latest": None,
                "lag": None, "threshold": threshold, "alarm": True,
                "note": f"缺文件 {filename}"}
    latest = _file_max_date(path, date_cols)
    status, lag = classify(latest, today, threshold, alarm=True)
    return {"source": f"macro:{label}", "status": status,
            "latest": latest.isoformat() if latest else None, "lag": lag,
            "threshold": threshold, "alarm": True, "note": filename}


def check_price_bars(today: dt.date) -> list[dict]:
    """
    price_bars 表：**信息级**。只有 backfill_prices.py / main.py 运行时才写，
    每日增量脚本写的是 data/*_history parquet 而非此表 → 它天然会落后。
    不参与退出码，只提示"如需刷新跑 scripts/backfill_prices.py"。
    """
    out: list[dict] = []
    if not DB.exists() or DB.stat().st_size == 0:
        return [{"source": "price_bars(表·遗留快照)", "status": INFO, "latest": None,
                 "lag": None, "threshold": None, "alarm": False, "note": "库不存在/空"}]
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        rows = c.execute(
            "SELECT market, MAX(timestamp), COUNT(*) FROM price_bars GROUP BY market"
        ).fetchall()
        c.close()
    except Exception as e:  # noqa: BLE001
        return [{"source": "price_bars(表·遗留快照)", "status": INFO, "latest": None,
                 "lag": None, "threshold": None, "alarm": False, "note": f"读失败 {e}"}]
    for market, mx, n in rows:
        latest = None
        if mx:
            try:
                latest = dt.date.fromisoformat(str(mx)[:10])
            except Exception:
                latest = None
        lag = lag_days(latest, today)
        out.append({"source": f"price_bars:{market}", "status": INFO,
                    "latest": latest.isoformat() if latest else None, "lag": lag,
                    "threshold": None, "alarm": False,
                    "note": f"{n} 行（遗留快照，刷新跑 backfill_prices.py）"})
    return out


def check_rag_sources(today: dt.date) -> list[dict]:
    """
    rag.db 各 source_type：**信息级**。chunks 表无入库时间戳，多数 source_type
    也没有日期字段（extra_meta 偶有 published_at）。因此只报行数 + 能取到的
    published_at 最大值，其余标注"无日期追踪"。不参与退出码。
    """
    if not RAG_DB.exists() or RAG_DB.stat().st_size == 0:
        return [{"source": "rag.db", "status": INFO, "latest": None, "lag": None,
                 "threshold": None, "alarm": False, "note": "库不存在/空"}]
    out: list[dict] = []
    try:
        c = sqlite3.connect(f"file:{RAG_DB}?mode=ro", uri=True)
        types = c.execute(
            "SELECT source_type, COUNT(*) FROM chunks GROUP BY source_type ORDER BY 2 DESC"
        ).fetchall()
        for st, n in types:
            # 尝试从 extra_meta 抠 published_at（大多为空）
            latest = None
            try:
                for (em,) in c.execute(
                    "SELECT extra_meta FROM chunks WHERE source_type=? "
                    "AND extra_meta LIKE '%published_at%' LIMIT 500", (st,)):
                    try:
                        pa = json.loads(em).get("published_at", "")
                    except Exception:
                        pa = ""
                    if pa and len(pa) >= 10:
                        try:
                            d = dt.date.fromisoformat(pa[:10])
                            if latest is None or d > latest:
                                latest = d
                        except Exception:
                            pass
            except Exception:
                pass
            note = "无日期追踪" if latest is None else "published_at(部分)"
            out.append({"source": f"rag:{st}", "status": INFO,
                        "latest": latest.isoformat() if latest else None,
                        "lag": lag_days(latest, today), "threshold": None,
                        "alarm": False, "note": f"{n} chunks · {note}"})
        c.close()
    except Exception as e:  # noqa: BLE001
        return [{"source": "rag.db", "status": INFO, "latest": None, "lag": None,
                 "threshold": None, "alarm": False, "note": f"读失败 {e}"}]
    return out


# ── 数据源登记表 ──────────────────────────────────────────────────────────────
# (name, subpath, date_cols, threshold_days, alarm, recursive)
PARQUET_SOURCES = [
    ("行情:A/港/美股",  "stock_history",     ["date"],        4,  True,  True),
    ("行情:指数",       "index_history",     ["date"],        4,  True,  False),
    ("行情:汇率",       "fx_history",        ["date"],        4,  True,  False),
    ("行情:加密",       "crypto_history",    ["date"],        4,  True,  False),
    ("行情:债券收益率", "bond_history",      ["date"],        5,  True,  False),
    ("行情:ETF",        "etf_history",       ["date"],        4,  True,  False),
    ("行情:场内基金",   "fund_history",      ["date", "日期"], 5,  True,  False),
    ("行情:期货",       "futures_history",   ["date"],        5,  True,  True),
    ("行情:期权",       "options_history",   ["date"],        5,  True,  True),
    ("行情:REITs",      "reits_history",     ["day", "date"], 7,  True,  True),
    ("行情:可转债",     "cb_history",        ["date"],        7,  True,  False),
    ("宏观:FRED",       "macro_history/fred", ["date"],       45, True,  False),
    ("宏观:房价70城",   "realestate_history", ["date", "日期"], 50, True,  False),
]

# 逐指标盯的关键宏观（同目录内混着新鲜/陈旧，必须单独看）
# (label, filename, date_cols, threshold_days)
MACRO_INDICATORS = [
    ("CPI月度",  "macro_china_cpi_monthly.parquet",        ["日期", "date"], 45),
    ("CPI年度",  "macro_china_cpi_yearly.parquet",         ["日期", "date"], 45),
    ("PPI年度",  "macro_china_ppi_yearly.parquet",         ["日期", "date"], 45),
    ("PMI",      "macro_china_pmi.parquet",                ["月份"],         45),
    ("M2货币",   "macro_china_money_supply.parquet",       ["月份"],         45),
    ("LPR",      "macro_china_lpr.parquet",                ["TRADE_DATE"],   45),
    ("GDP季度",  "macro_china_gdp_yearly.parquet",         ["日期", "date"], 120),
    ("社融投资", "macro_china_gdzctz.parquet",             ["月份", "日期"], 45),
    ("工业增加", "macro_china_industrial_production_yoy.parquet", ["日期", "月份"], 45),
]


def run(today: dt.date | None = None) -> list[dict]:
    today = today or dt.date.today()
    results: list[dict] = []
    for name, sub, cols, thr, alarm, rec in PARQUET_SOURCES:
        results.append(check_parquet_dir(name, sub, cols, thr, alarm, rec, today))
    for label, fn, cols, thr in MACRO_INDICATORS:
        results.append(check_macro_indicator(label, fn, cols, thr, today))
    results.extend(check_price_bars(today))
    results.extend(check_rag_sources(today))
    return results


def _print_table(results: list[dict]) -> None:
    w = max(len(r["source"]) for r in results)
    print(f"\n{'数据源':<{w}}  状态       最新日期     落后  阈值  备注")
    print("─" * (w + 56))
    for r in results:
        latest = r["latest"] or "—"
        lag = f"{r['lag']}d" if r["lag"] is not None else "—"
        thr = f"{r['threshold']}d" if r.get("threshold") is not None else "—"
        print(f"{r['source']:<{w}}  {r['status']:<9} {latest:<11}  {lag:>4}  {thr:>4}  {r['note']}")


def main() -> int:
    today = dt.date.today()
    results = run(today)

    if "--json" in sys.argv:
        print(json.dumps({"today": today.isoformat(), "results": results},
                         ensure_ascii=False, indent=2))
    else:
        print(f"\n数据新鲜度报告  ·  今天 {today.isoformat()}")
        _print_table(results)
        n_stale = sum(1 for r in results if r["status"] == STALE and r["alarm"])
        n_broken = sum(1 for r in results if r["status"] == BROKEN and r["alarm"])
        n_fresh = sum(1 for r in results if r["status"] == FRESH)
        print(f"\n汇总：{n_fresh} FRESH / {n_stale} STALE / {n_broken} BROKEN "
              f"（信息级源不计入告警）")
        if n_stale or n_broken:
            print("⚠️  有告警级数据源滞后/异常，退出码=1")

    # 退出码：只看 alarm=True 的 STALE/BROKEN
    bad = sum(1 for r in results if r["alarm"] and r["status"] in (STALE, BROKEN))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
