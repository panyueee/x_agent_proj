# -*- coding: utf-8 -*-
"""会议日历 scheduled 政策事件骨架加载脚本（半自动）。

生成"时点锚点"骨架事件：中国两会 / 中央政治局会议(经济) / 中央经济工作会议(CEWC) /
美联储 FOMC 例会，覆盖 2022-01 至 2026-12。**只标时点，不编造会议决定内容**——
决定内容是下游 persona 打分要预测的对象，不能在这里当真值填。

冻结契约：只 import x_agent/policy_events.py，不修改。写 output/x_agent.db，
用 connect_write()（带 busy_timeout，容忍 tgb 并发写库），upsert_event 保证幂等。

FOMC 特例：任务要求 category="货币" + action="FOMC_MEETING"，但冻结契约的
MONETARY_ACTIONS 受控码表里没有 FOMC_MEETING（那张表是给"利率决议"用的，如
FED_FUNDS；FOMC_MEETING 是"开会时点"锚点，语义不同，不该也不能塞进去改契约）。
因此 FOMC 行用 upsert_event(strict=False) —— 这是契约自带的逃生阀，不是绕过
契约，validate() 仍会跑，只是不因这一条"货币类 action 未在受控码表"而中断写入。
中国会议类（category="会议"）严格校验能干净通过，继续用 strict=True 以便捕获手误。

国务院常务会议：高频、无规律日历，无法可靠推导，这里只打印骨架说明，不编日期
（见 announce_state_council_council_meetings_note()）。

运行：
  .venv/bin/python scripts/load_policy_calendar.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from x_agent.policy_events import PolicyEvent, connect_write, ensure_schema, upsert_event  # noqa: E402

DB_PATH = str(ROOT / "output" / "x_agent.db")

YEARS = range(2022, 2027)  # 2022..2026


# ── 1. 中国两会（全国人大 + 政协）：announce_date 用人大开幕日锚点 ──────────
# 2022-2025 为实际公开开幕日；2026 尚未官宣，用惯例日期（3/5）近似。
TWO_SESSIONS = {
    2022: ("2022-03-05", "official"),
    2023: ("2023-03-05", "official"),
    2024: ("2024-03-05", "official"),
    2025: ("2025-03-05", "official"),
    2026: ("2026-03-05", "calendar_convention"),
}

# ── 2. 中央政治局会议（经济主题，惯例 4/7/12 月）─────────────────────────
# 2022-2024 为实际公开日期；2025-2026 官方日期未逐一核实，用惯例近似日。
POLITBURO = {
    2022: [("2022-04-29", "official"), ("2022-07-28", "official"), ("2022-12-06", "official")],
    2023: [("2023-04-28", "official"), ("2023-07-24", "official"), ("2023-12-08", "official")],
    2024: [("2024-04-30", "official"), ("2024-07-30", "official"), ("2024-12-09", "official")],
    2025: [("2025-04-25", "calendar_convention"), ("2025-07-30", "calendar_convention"),
           ("2025-12-08", "calendar_convention")],
    2026: [("2026-04-25", "calendar_convention"), ("2026-07-25", "calendar_convention"),
           ("2026-12-08", "calendar_convention")],
}

# ── 3. 中央经济工作会议 CEWC（惯例 12 月中下旬）──────────────────────────
CEWC = {
    2022: ("2022-12-16", "official"),
    2023: ("2023-12-12", "official"),
    2024: ("2024-12-12", "official"),
    2025: ("2025-12-11", "calendar_convention"),
    2026: ("2026-12-15", "calendar_convention"),
}

# ── 4. 美联储 FOMC 例会：announce_date 用两日会议第二天(决策公布日) ────────
# 2022-2026 均为 federalreserve.gov 公开既定日历，标 "official"。
FOMC = {
    2022: ["2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15",
           "2022-07-27", "2022-09-21", "2022-11-02", "2022-12-14"],
    2023: ["2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14",
           "2023-07-26", "2023-09-20", "2023-11-01", "2023-12-13"],
    2024: ["2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12",
           "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18"],
    2025: ["2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
           "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10"],
    2026: ["2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
           "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09"],
}


def build_two_sessions_events() -> list[PolicyEvent]:
    events = []
    for year, (date, src) in TWO_SESSIONS.items():
        tag = "" if src == "official" else "(日历推导)"
        events.append(PolicyEvent(
            region="CN", category="会议", event_type="scheduled",
            issuer="全国人大", action="TWO_SESSIONS", direction="na",
            announce_date=date, effective_date="",
            title=f"{year} 全国两会{tag}",
            params={"date_source": src, "note": "全国人大+全国政协；announce_date 取人大开幕日锚点"},
            source_tier="manual", verification_status="unverified",
        ))
    return events


def build_politburo_events() -> list[PolicyEvent]:
    events = []
    for year, items in POLITBURO.items():
        for date, src in items:
            tag = "" if src == "official" else "(日历推导)"
            month = int(date.split("-")[1])
            events.append(PolicyEvent(
                region="CN", category="会议", event_type="scheduled",
                issuer="中央政治局", action="POLITBURO", direction="na",
                announce_date=date, effective_date="",
                title=f"{year} 中央政治局会议(经济,{month}月){tag}",
                params={"date_source": src},
                source_tier="manual", verification_status="unverified",
            ))
    return events


def build_cewc_events() -> list[PolicyEvent]:
    events = []
    for year, (date, src) in CEWC.items():
        tag = "" if src == "official" else "(日历推导)"
        events.append(PolicyEvent(
            region="CN", category="会议", event_type="scheduled",
            issuer="中共中央", action="CEWC", direction="na",
            announce_date=date, effective_date="",
            title=f"{year} 中央经济工作会议{tag}",
            params={"date_source": src},
            source_tier="manual", verification_status="unverified",
        ))
    return events


def build_fomc_events() -> list[PolicyEvent]:
    events = []
    for year, dates in FOMC.items():
        for date in dates:
            events.append(PolicyEvent(
                region="US", category="货币", event_type="scheduled",
                issuer="FOMC", action="FOMC_MEETING", direction="na",
                announce_date=date, effective_date="",
                title=f"FOMC 例会 {date}",
                params={"date_source": "official",
                        "note": "会议锚点，非利率决议；决议内容(FED_FUNDS)留待货币 loader 补"},
                source_tier="manual", verification_status="unverified",
            ))
    return events


def print_state_council_note() -> None:
    print(
        "[国务院常务会议] 高频(约每周一次)、日期不规则，无法用日历惯例可靠推导 —— "
        "只打印骨架说明，不逐条编造事件；需要接公告适配器 "
        "(scripts/load_policy_announcements.py) 从 gov.cn 会议纪要抓取真实日期。"
    )


def main() -> None:
    conn = connect_write(DB_PATH)
    ensure_schema(conn)

    groups = [
        ("两会", build_two_sessions_events(), True),
        ("政治局会议", build_politburo_events(), True),
        ("CEWC", build_cewc_events(), True),
        ("FOMC 例会", build_fomc_events(), False),  # strict=False：见文件头注释
    ]

    print(f"[load_policy_calendar] 写入目标: {DB_PATH}")
    print_state_council_note()

    total_new, total_upd = 0, 0
    for name, events, strict in groups:
        new_cnt, upd_cnt = 0, 0
        dates = []
        for ev in events:
            is_new = upsert_event(conn, ev, strict=strict)
            dates.append(ev.announce_date)
            if is_new:
                new_cnt += 1
            else:
                upd_cnt += 1
        total_new += new_cnt
        total_upd += upd_cnt
        date_range = f"{min(dates)} ~ {max(dates)}" if dates else "(无)"
        print(f"[{name}] 共 {len(events)} 条，新增 {new_cnt}，覆盖(已存在) {upd_cnt}，"
              f"日期范围 {date_range}，strict={strict}")

    conn.close()
    print(f"[load_policy_calendar] 合计 {total_new + total_upd} 条，"
          f"本次新增 {total_new}，覆盖(幂等) {total_upd}")


if __name__ == "__main__":
    main()
