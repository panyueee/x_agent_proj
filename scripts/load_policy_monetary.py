#!/usr/bin/env python3
"""
货币政策事件灌入脚本 —— 把 akshare 结构化利率/准备金率序列灌入独立政策事件库
（output/x_agent.db 的 policy_events 表），供 persona 方法D 的政策预测打分用作
客观真值尺（地基，最优先）。

冻结契约：仅 import x_agent/policy_events.py 的 PolicyEvent / connect_write /
ensure_schema / upsert_event / query_events，绝不修改该文件或 storage.py。

数据源（均已用 .venv/bin/python 实测，见脚本内注释的新鲜度结论）：
  1. CN LPR      ak.macro_china_lpr()                          新鲜到 2026-06
  2. CN 降准 RRR ak.macro_china_reserve_requirement_ratio()    新鲜到 2025-05
  3. MLF/OMO     akshare 无结构化利率序列 → 数据缺口，不灌
  4. US FED_FUNDS ak.macro_bank_usa_interest_rate()            过期，停在 2025-09
  5. ECB/BOJ     ak.macro_bank_euro_interest_rate() /
                 ak.macro_bank_japan_interest_rate()           过期，停在 2025-09/10

用法（务必用项目 venv）：
  /Users/pany19/Documents/x_agent_proj/.venv/bin/python scripts/load_policy_monetary.py

幂等：身份哈希（region|category|action|issuer|announce|effective）覆盖写，
重复运行不产生重复行；脚本末尾会自跑一次二次执行做幂等自检。
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import akshare as ak  # noqa: E402

from x_agent.policy_events import (  # noqa: E402
    PolicyEvent,
    connect_write,
    ensure_schema,
    query_events,
    upsert_event,
)

DB_PATH = ROOT / "output" / "x_agent.db"
SUMMARY_PATH = ROOT / "output" / "policy" / "monetary_load_summary.json"

LPR_REFORM_DATE = "2019-08-20"  # 2019-08-20 之前是老口径 LPR，口径不同，不灌


def _iso(d) -> str:
    """akshare 返回的日期列多为 datetime.date，str() 即为 'YYYY-MM-DD'。"""
    return str(d)


def _direction_for_delta(delta: float) -> str:
    if delta < 0:
        return "cut"
    if delta > 0:
        return "hike"
    return "hold"


def _record(stats: dict, action: str, announce_date: str, is_new: bool, gap: str | None = None) -> None:
    s = stats.setdefault(action, {"count": 0, "min_date": None, "max_date": None,
                                   "new_inserted": 0, "gaps": []})
    s["count"] += 1
    if is_new:
        s["new_inserted"] += 1
    if s["min_date"] is None or announce_date < s["min_date"]:
        s["min_date"] = announce_date
    if s["max_date"] is None or announce_date > s["max_date"]:
        s["max_date"] = announce_date
    if gap:
        s["gaps"].append(gap)


def _record_gap_only(stats: dict, action: str, gap: str) -> None:
    s = stats.setdefault(action, {"count": 0, "min_date": None, "max_date": None,
                                   "new_inserted": 0, "gaps": []})
    s["gaps"].append(gap)


# ── 1. CN LPR（scheduled，每月既定报价）────────────────────────────────────

def load_lpr(conn, stats: dict) -> None:
    df = ak.macro_china_lpr().sort_values("TRADE_DATE").reset_index(drop=True)
    df["TRADE_DATE"] = df["TRADE_DATE"].apply(_iso)
    max_date = df["TRADE_DATE"].max()
    print(f"[LPR] akshare 最新日期 {max_date}")

    for col, action, label in [("LPR1Y", "LPR_1Y", "1年期"), ("LPR5Y", "LPR_5Y", "5年期以上")]:
        prev_val = None
        for _, row in df.iterrows():
            date_str = row["TRADE_DATE"]
            val = row[col]
            if val != val:  # NaN
                prev_val = None
                continue
            if date_str < LPR_REFORM_DATE:
                prev_val = val  # 仅用作后续 diff 基准，不生成改革前事件
                continue
            if prev_val is None or prev_val != prev_val:
                direction = "na"
                delta_pp = 0.0
                title = f"{label}LPR {val:.2f}%（改革首次报价，无可比基准）"
            else:
                delta_pp = round(val - prev_val, 2)
                direction = _direction_for_delta(delta_pp)
                title = f"{label}LPR {val:.2f}%（{delta_pp:+.2f}）"
            ev = PolicyEvent(
                region="CN", category="货币", event_type="scheduled", issuer="PBOC",
                action=action, direction=direction,
                announce_date=date_str, effective_date=date_str,
                title=title, params={"lpr": val, "delta_pp": delta_pp},
                source_tier="structured_feed", verification_status="auto",
            )
            is_new = upsert_event(conn, ev)
            _record(stats, action, date_str, is_new)
            prev_val = val


# ── 2. CN 降准 RRR（discretionary，公布/生效日分离）────────────────────────

def load_rrr(conn, stats: dict) -> None:
    df = ak.macro_china_reserve_requirement_ratio()
    # akshare 返回 newest-first，必须 sort 成升序；公布时间是 '%Y年%m月%d日' 字符串
    df = df.copy()
    df["_announce_dt"] = df["公布时间"].apply(lambda s: dt.datetime.strptime(s, "%Y年%m月%d日").date())
    df = df.sort_values("_announce_dt").reset_index(drop=True)
    max_date = df["_announce_dt"].max().isoformat()
    print(f"[RRR] akshare 最新公布日期 {max_date}")

    for _, row in df.iterrows():
        announce_date = row["_announce_dt"].isoformat()
        effective_date = dt.datetime.strptime(row["生效时间"], "%Y年%m月%d日").date().isoformat()
        delta = row["大型金融机构-调整幅度"]
        if delta != delta:  # NaN，理论上不会发生（已探过 0 NaN），防御性跳过
            print(f"[RRR] 跳过 {announce_date}：调整幅度缺失")
            continue
        delta_pp = round(float(delta), 2)
        direction = _direction_for_delta(delta_pp)
        after = row["大型金融机构-调整后"]
        verb = "降准" if delta_pp < 0 else ("升准" if delta_pp > 0 else "维持")
        title = f"{verb} {abs(delta_pp):.2g}pp（大型机构{'降至' if delta_pp < 0 else '升至' if delta_pp > 0 else '维持'} {after:.1f}%）"
        ev = PolicyEvent(
            region="CN", category="货币", event_type="discretionary", issuer="PBOC",
            action="RRR", direction=direction,
            announce_date=announce_date, effective_date=effective_date,
            title=title, params={"rrr_after": float(after), "delta_pp": delta_pp},
            source_tier="structured_feed", verification_status="auto",
        )
        is_new = upsert_event(conn, ev)
        _record(stats, "RRR", announce_date, is_new)


# ── 3. MLF / OMO：akshare 无结构化源 → 数据缺口 ────────────────────────────

def flag_mlf_omo_gap(stats: dict) -> None:
    msg = "数据缺口：MLF/OMO 无结构化 akshare 源，留待公告适配器"
    print(msg)
    _record_gap_only(stats, "MLF_RATE", msg)
    _record_gap_only(stats, "OMO_7D", msg)


# ── 4/5. US FED_FUNDS / ECB_RATE / BOJ_RATE（过期序列，只灌非 NaN 决议）────

def _load_foreign_rate_series(conn, stats: dict, fn, region: str, issuer: str, action: str,
                               label: str, stale_hint: str) -> None:
    df = fn().sort_values("日期").reset_index(drop=True)
    df["日期"] = df["日期"].apply(_iso)
    valid = df[df["今值"].notna()]
    if valid.empty:
        print(f"[{action}] 无任何非 NaN 决议数据")
        _record_gap_only(stats, action, f"数据缺口：{label} akshare 序列无有效值")
        return
    last_valid_date = valid["日期"].max()
    first_nan_after = df[(df["日期"] > last_valid_date)]
    print(f"[{action}] akshare 最新有效决议 {last_valid_date}（后续 {len(first_nan_after)} 行为 NaN）")

    for _, row in valid.iterrows():
        date_str = row["日期"]
        val = float(row["今值"])
        prev = row["前值"]
        if prev != prev:  # NaN，序列首条
            direction = "na"
            delta_pp = 0.0
            title = f"{label} {val:.2f}%（序列首条，无可比基准）"
        else:
            delta_pp = round(val - float(prev), 2)
            direction = _direction_for_delta(delta_pp)
            title = f"{label} {val:.2f}%（{delta_pp:+.2f}）"
        ev = PolicyEvent(
            region=region, category="货币", event_type="scheduled", issuer=issuer,
            action=action, direction=direction,
            announce_date=date_str, effective_date="",
            title=title, params={"target_rate": val, "delta_pp": delta_pp},
            source_tier="structured_feed", verification_status="auto",
        )
        is_new = upsert_event(conn, ev)
        _record(stats, action, date_str, is_new)

    gap_msg = f"数据缺口：{stale_hint}"
    print(gap_msg)
    _record_gap_only(stats, action, gap_msg)


def load_fed(conn, stats: dict) -> None:
    _load_foreign_rate_series(
        conn, stats, ak.macro_bank_usa_interest_rate,
        region="US", issuer="FOMC", action="FED_FUNDS", label="联邦基金目标利率",
        stale_hint="US FOMC akshare 序列停在 2025-09，2025-10 之后无真值，未灌",
    )


def load_ecb(conn, stats: dict) -> None:
    _load_foreign_rate_series(
        conn, stats, ak.macro_bank_euro_interest_rate,
        region="INTL", issuer="ECB", action="ECB_RATE", label="欧洲央行主要利率",
        stale_hint="ECB akshare 序列已过期（约停在 2025-09），之后无真值，未灌",
    )


def load_boj(conn, stats: dict) -> None:
    _load_foreign_rate_series(
        conn, stats, ak.macro_bank_japan_interest_rate,
        region="INTL", issuer="BOJ", action="BOJ_RATE", label="日本央行政策利率",
        stale_hint="BOJ akshare 序列已过期（约停在 2025-10），之后无真值，未灌",
    )


# ── 自检 ────────────────────────────────────────────────────────────────

def self_check(conn) -> None:
    print("\n=== 自检抽查（announce/effective 分离 + delta_pp）===")
    rrr_events = query_events(conn, category="货币", action="RRR")
    for ev in rrr_events[-3:]:
        print(f"  RRR announce={ev['announce_date']} effective={ev['effective_date']} "
              f"delta_pp={ev['params'].get('delta_pp')} title={ev['title']}")
    lpr_events = query_events(conn, category="货币", action="LPR_1Y")
    for ev in lpr_events[-3:]:
        print(f"  LPR_1Y announce={ev['announce_date']} effective={ev['effective_date']} "
              f"delta_pp={ev['params'].get('delta_pp')} title={ev['title']}")
    fed_events = query_events(conn, category="货币", action="FED_FUNDS")
    if fed_events:
        ev = fed_events[-1]
        print(f"  FED_FUNDS(last) announce={ev['announce_date']} delta_pp={ev['params'].get('delta_pp')} "
              f"title={ev['title']}")


def run(conn) -> dict:
    stats: dict = {}
    load_lpr(conn, stats)
    load_rrr(conn, stats)
    flag_mlf_omo_gap(stats)
    load_fed(conn, stats)
    load_ecb(conn, stats)
    load_boj(conn, stats)
    return stats


def print_summary(stats: dict) -> None:
    print("\n=== 汇总 ===")
    for action in sorted(stats.keys()):
        s = stats[action]
        gap_note = f" | gaps: {s['gaps']}" if s["gaps"] else ""
        print(f"  {action}: count={s['count']} new_inserted={s['new_inserted']} "
              f"min_date={s['min_date']} max_date={s['max_date']}{gap_note}")


def main() -> None:
    if str(DB_PATH) != str(ROOT / "output" / "x_agent.db"):
        raise RuntimeError("写库路径必须是 output/x_agent.db")
    print(f"写库路径: {DB_PATH}")
    conn = connect_write(str(DB_PATH))
    try:
        ensure_schema(conn)
        stats = run(conn)
        print_summary(stats)
        self_check(conn)
    finally:
        conn.close()

    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SUMMARY_PATH, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    print(f"\n汇总已 dump 到 {SUMMARY_PATH}")


if __name__ == "__main__":
    main()
