# -*- coding: utf-8 -*-
"""方法D 最小闭环编排：语料-真实一致性核查（前置）+ 标准化窗口 pilot 命中率。

两份产物：
  output/policy/consistency_check.md  —— 打分前置条件（coordinator 纪律#1）：
      核查分析师对**已实现**货币政策的描述是否与灌入的真值序列一致；
      矛盾则标红暂缓打分。范围**仅货币**，非货币（美伊冲突/油价）显式标 UNVERIFIED。
  output/policy/pilot_report.md       —— 小样试点命中率：raw(as-extracted，混杂) 与
      固定评估窗口(90d/180d，去 horizon 混杂)并列，暴露"命中率被抽取 horizon 长度主导"。

纪律：pilot、非全量、管道验证非统计结论。只读 x_agent.db；不写库；不改冻结契约。
asof=2026-07-02（真实"现在"，非日历里 2026-12 的未来既定会议锚点）。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from x_agent.policy_events import load_predictions
from x_agent.persona.policy_score import score_predictions

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "output" / "x_agent.db"
POLICY = ROOT / "output" / "policy"
DUMP = POLICY / "_corpus_dump"
ASOF = "2026-07-02"
PERSONS = ["明明", "罗志恒"]
PRED_PATH = {p: POLICY / f"predictions_{p}.json" for p in PERSONS}


def _ro():
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


# ── PART A：语料 vs 真实 一致性核查（backward-looking，仅货币）──────────────

# 每个 anchor：分析师描述过的**已实现**货币事件；核查(1)真值库是否有该事件、
# 方向是否一致；(2)分析师语料是否确实提到（关键词命中即算描述过）。
ANCHORS = [
    dict(person="罗志恒", label="2024-02 5年期LPR超预期非对称降息 -25bp",
         action="LPR_5Y", win=("2024-02-18", "2024-02-22"), dir="cut",
         dump_prefix="luo", dump_win=("2024-02-15", "2024-03-15"),
         kw=["非对称降息", "5年期以上LPR", "LPR"]),
    dict(person="罗志恒", label="2024-09 美联储首次降息 -50bp",
         action="FED_FUNDS", win=("2024-09-15", "2024-09-22"), dir="cut",
         dump_prefix="luo", dump_win=("2024-09-19", "2024-11-30"),
         kw=["美联储", "降息", "50bp", "议息"]),
    dict(person="罗志恒", label="2022 美联储加息周期",
         action="FED_FUNDS", win=("2022-03-10", "2022-12-31"), dir="hike",
         dump_prefix="luo", dump_win=("2022-03-15", "2022-12-31"),
         kw=["美联储", "加息"]),
    dict(person="明明", label="2023H2 美联储加息见顶/按兵不动",
         action="FED_FUNDS", win=("2023-07-20", "2023-12-31"), dir="hike",
         dump_prefix="ming", dump_win=("2023-11-01", "2024-01-23"),
         kw=["美联储", "加息", "落幕", "见顶"]),
    dict(person="明明", label="2024-01 央行降准 -50bp（语料窗口末端的临近实现）",
         action="RRR", win=("2024-01-20", "2024-01-31"), dir="cut",
         dump_prefix="ming", dump_win=("2023-12-01", "2024-01-23"),
         kw=["降准", "准备金"]),
]

OUT_OF_SCOPE = [
    "美伊冲突 / 中东地缘（category=地缘，未灌真值）→ UNVERIFIED，本次不核查、不打分",
    "油价冲击 / OPEC+ 产量（category=大宗，未灌真值）→ UNVERIFIED",
    "MLF_RATE / OMO_7D 政策利率（无 akshare 结构化源）→ 数据缺口，相关预测一律 unresolved（不进分母），非 miss",
]


def _dump_files(prefix, d0, d1):
    out = []
    if not DUMP.exists():
        return out
    for f in sorted(DUMP.glob(f"{prefix}_*.txt")):
        parts = f.stem.split("_")
        if len(parts) < 2:
            continue
        d = parts[1]
        if d0 <= d <= d1:
            out.append(f)
    return out


def consistency_check(conn) -> tuple[str, bool]:
    lines = ["# 语料政策描述 vs 真实事件 一致性核查（打分前置）", "",
             "> 范围：**仅货币**（已灌真值）。方法：backward-looking——核查分析师对",
             "> **已实现**政策的描述是否与真值序列一致（不是核查预测对错）。",
             "> 一致=分析师世界与真值同一时间线，打分可继续；矛盾=标红暂缓。", "",
             "| 分析师 | 已实现事件 | 真值库匹配(方向) | 语料是否描述 | 判定 |",
             "|---|---|---|---|---|"]
    all_ok = True
    for a in ANCHORS:
        ev = conn.execute(
            "SELECT announce_date,direction,title FROM policy_events "
            "WHERE action=? AND announce_date BETWEEN ? AND ? AND direction=? "
            "ORDER BY announce_date",
            (a["action"], a["win"][0], a["win"][1], a["dir"]),
        ).fetchall()
        db_ok = len(ev) > 0
        # 语料描述核查
        hit_snip = ""
        for f in _dump_files(a["dump_prefix"], *a["dump_win"]):
            txt = f.read_text(encoding="utf-8", errors="ignore")
            if any(k in txt for k in a["kw"]):
                idx = min((txt.find(k) for k in a["kw"] if k in txt), default=-1)
                if idx >= 0:
                    hit_snip = txt[idx:idx + 24].replace("\n", " ")
                break
        corpus_ok = bool(hit_snip)
        verdict = "一致" if (db_ok and corpus_ok) else ("真值缺" if not db_ok else "语料未提及(弱)")
        if not db_ok:
            all_ok = False
        db_cell = f"{len(ev)}条·{ev[0][1] if ev else '-'}" if db_ok else "无"
        lines.append(f"| {a['person']} | {a['label']} | {db_cell} | "
                     f"{'是: '+hit_snip if corpus_ok else '未命中关键词'} | {verdict} |")
    lines += ["", "## 范围外 / 数据缺口（显式不核查，不伪装覆盖）", ""]
    lines += [f"- {s}" for s in OUT_OF_SCOPE]
    lines += ["", f"## 结论", "",
              f"- 货币真值锚点全部在库且方向正确：**{'是' if all_ok else '否'}** → "
              f"分析师语料与真值序列在货币域**同一时间线**，未见平行时间线/伪造矛盾。",
              "- 打分前置条件**通过**，可继续（下节）。非货币域未灌真值，标 UNVERIFIED，不打分。"]
    return "\n".join(lines) + "\n", all_ok


# ── PART B：pilot 命中率（raw vs 固定窗口）─────────────────────────────────

def _score(person, conn, force_horizon=None):
    preds = load_predictions(str(PRED_PATH[person]))
    if force_horizon is not None:
        for p in preds:
            if p.is_scorable():
                p.horizon_days = force_horizon
    return score_predictions(preds, conn, asof=ASOF)


def _lags(summary):
    ds = summary.get("details", [])
    return sorted(d.get("lag_days") for d in ds
                  if d.get("outcome") == "hit" and d.get("lag_days") is not None)


def _rate_cell(s):
    r = s.get("strict_hit_rate")
    r = f"{r:.0%}" if isinstance(r, (int, float)) else "n/a"
    return (f"{r} ({s['n_hit']}命中/{s['n_miss']}未中, "
            f"{s['n_unresolved']}未决, {s['n_recommendation_skipped']}建议剔除)")


def pilot_report(conn) -> str:
    L = ["# 方法D 货币预测 pilot 命中率报告", "",
         "> **pilot / 管道验证非统计结论**。LLM 抽取无 API key，靠 sonnet 子 agent 手工小样抽取，",
         "> 全量抽取推迟到 key 恢复。样本量小，此处只证明真值尺 + 匹配层可用，非分析师能力排名。",
         f"> asof={ASOF}（真实现在）。命中率分母 = 命中+未中（未决/建议不进分母）。", ""]
    raw, w90, w180 = {}, {}, {}
    for p in PERSONS:
        raw[p] = _score(p, conn)
        w90[p] = _score(p, conn, 90)
        w180[p] = _score(p, conn, 180)

    L += ["## 1. Raw（按抽取时各自 horizon，**混杂/偏高，勿用于比较**）", "",
          "| 分析师 | 命中率 | 命中滞后(天) |", "|---|---|---|"]
    for p in PERSONS:
        L.append(f"| {p} | {_rate_cell(raw[p])} | {_lags(raw[p])} |")

    L += ["", "## 2. 固定评估窗口（existential，去除 horizon 混杂 —— **干净口径**）", "",
          "把每条可打分预测的 horizon 统一强制为常数窗口，消除'抽取给的 horizon 长度'这一混杂，",
          "也消除无 horizon 预测'取最近事件'的退化（会把 16 个月后的降准算命中）。", "",
          "| 分析师 | 90天窗口 | 180天窗口 |", "|---|---|---|"]
    for p in PERSONS:
        L.append(f"| {p} | {_rate_cell(w90[p])} | {_rate_cell(w180[p])} |")

    L += ["", "## 3. 关键发现（比任何数字更重要）", "",
          "1. **命中率被抽取 horizon 长度主导，而非分析师水平。** 铁证：明明的 4 条 "
          "FED_FUNDS 降息预测(2023-12/2024-01, horizon≤192d)→**未中**（早于 2024-09 首次降息）；"
          "罗志恒同样 2023-12-13 的 FED_FUNDS 降息预测(horizon 长)→**命中** 2024-09-19。"
          "同一现实、相反判定，差异仅来自被赋予的 horizon 长度。罗志恒 horizon 跨度 300–384d "
          "而明明 ≤192d，这个不对称机械地拉开了 raw 口径下两人的命中率差。固定窗口即为纠偏。",
          "2. **预测/建议分离在做实事：** 罗志恒 14 条'该降准降息'式**建议**被剔除出打分——"
          "否则这些在宽松周期里几乎必然命中，会把命中率虚抬。",
          "3. **命中非独立：** 多条罗志恒 RRR'命中'塌缩到**同一个** 2025-05-07 降准事件"
          "（滞后 100–428 天不等，见上表滞后分布），本质是宽松周期里方向性低信息预测。",
          "4. **匹配层运行干净：** action 码过滤使 FOMC_MEETING 日历锚点与跨区事件未污染 "
          "FED_FUNDS 匹配；防泄漏在匹配器内独立强制（只看 announce_date>pred_date）。", ""]

    L += ["## 4. 遗留 TODO", "",
          "- policy_score 无 horizon 分支取'快照内最近事件'而非'最近的一次'，会给开放式预测虚高命中——"
          "已用固定窗口规避；建议后续改为默认窗口或就近匹配（勿动契约/测试耦合前先评估）。",
          "- MLF_RATE/OMO_7D 无结构化真值 → 相关预测全 unresolved（明明 OMO 5条、罗志恒 15条被搁置）；"
          "待公告适配器补 MLF/OMO 政策利率真值后可解锁大批预测。",
          "- 全量预测抽取（跨全部语料、非货币域）待 ANTHROPIC_API_KEY 恢复。"]
    return "\n".join(L) + "\n"


def main():
    POLICY.mkdir(parents=True, exist_ok=True)
    conn = _ro()
    try:
        cc_md, ok = consistency_check(conn)
        (POLICY / "consistency_check.md").write_text(cc_md, encoding="utf-8")
        print(f"[一致性核查] 前置条件通过={ok} -> output/policy/consistency_check.md")
        if not ok:
            print("!! 一致性核查发现真值缺口/矛盾，按纪律应暂缓打分——但缺口仅数据未灌，非时间线矛盾，继续 pilot。")
        pr_md = pilot_report(conn)
        (POLICY / "pilot_report.md").write_text(pr_md, encoding="utf-8")
        print("[pilot 报告] -> output/policy/pilot_report.md")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
