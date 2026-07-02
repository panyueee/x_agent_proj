# -*- coding: utf-8 -*-
"""方法D 匹配层 + 打分 —— 纯代码，不需要 LLM/API。

给分析师的政策预测 (Prediction) 找到 policy_events 库里对应的真实事件，判定
hit / miss / unresolved 三态，汇总命中率、滞后天数、数值误差。

冻结契约（只读，绝不修改）：
  - x_agent.policy_events：PolicyEvent / Prediction / query_events / load_predictions
  - x_agent.storage：不涉及，本模块只读 policy_events 表

防泄漏纪律：
  - 只匹配 announce_date **严格大于** pred.pred_date 的事件；
  - recommendation（kind != "prediction"）一律跳过，不进任何分母；
  - 三态：hit / miss 进 strict_hit_rate 分母，unresolved 不进（时间还没到，不是错）。

三态判定规则（两条分支不对称，均出自冻结契约 policy_events.py 的预测字段注释）：
  - 有 horizon_days：窗口 [pred_date, pred_date+horizon_days] 有限，**存在性**判定
    ——窗口内只要有一条同类事件方向吻合就是 hit（例：LPR 月度例会先 hold 后 cut，
    cut 落在窗口内仍算命中）。
  - 无 horizon_days：窗口开放到 asof，契约明文"取快照内**最近事件**"——**单事件**
    判定，只看窗口内最新（announce_date 最大）一条同类事件的方向。若在此改用存在性
    判定，多年跨度的开放窗口里只要历史上出现过一次吻合方向的事件就会判 hit，即使
    最新政策立场已反转，系统性虚高命中率。
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import statistics
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from ..policy_events import (
    Prediction,
    PolicyEvent,
    ensure_schema,
    load_predictions,
    query_events,
    upsert_event,
)

logger = logging.getLogger(__name__)

OUTCOME_HIT = "hit"
OUTCOME_MISS = "miss"
OUTCOME_UNRESOLVED = "unresolved"
OUTCOME_SKIPPED = "skipped"


def _parse_date(s: str) -> date:
    return date.fromisoformat(str(s))


def match_prediction(pred: Prediction, events: list[dict], asof: str) -> dict:
    """给单条预测找匹配事件，判定三态结局。

    events：候选事件池（dict 列表，形如 query_events 的返回；不要求调用方
    预先按 region/category/action/日期过滤 —— 本函数自己做全部过滤，
    这样可以直接喂"脏"/更宽的事件列表做防泄漏自测）。
    asof：数据快照边界（YYYY-MM-DD），无 horizon 的预测看到这天为止。

    返回 dict，字段：
      pred_id, person, kind, outcome (hit/miss/unresolved/skipped),
      lag_days, value_error, matched_event（摘要 dict 或 None）, reason
    """
    base = {
        "pred_id": pred.pred_id,
        "person": pred.person,
        "kind": pred.kind,
        "region": pred.region,
        "category": pred.category,
        "action": pred.action,
        "direction": pred.direction,
        "pred_date": pred.pred_date,
        "horizon_days": pred.horizon_days,
        "value": pred.value,
    }

    if not pred.is_scorable():
        return {**base, "outcome": OUTCOME_SKIPPED, "lag_days": None,
                "value_error": None, "matched_event": None,
                "reason": "recommendation，不打分"}

    pred_date = _parse_date(pred.pred_date)
    asof_date = _parse_date(asof)

    if pred.horizon_days is not None:
        window_end = min(pred_date + timedelta(days=int(pred.horizon_days)), asof_date)
    else:
        window_end = asof_date

    # 候选：同 region+category（+action，若预测指定了具体 action）、
    # announce_date 严格大于 pred_date（防泄漏）且 <= window_end。
    candidates = []
    for ev in events:
        if ev.get("region") != pred.region:
            continue
        if ev.get("category") != pred.category:
            continue
        if pred.action and ev.get("action") != pred.action:
            continue
        ann = ev.get("announce_date")
        if not ann:
            continue
        try:
            ann_date = _parse_date(ann)
        except ValueError:
            continue
        if not (pred_date < ann_date <= window_end):
            continue
        candidates.append((ann_date, ev))

    if not candidates:
        return {**base, "outcome": OUTCOME_UNRESOLVED, "lag_days": None,
                "value_error": None, "matched_event": None,
                "reason": "窗口/快照内尚无同类事件"}

    candidates.sort(key=lambda t: t[0])

    if pred.horizon_days is not None:
        # 有 horizon：窗口有限，三态是存在性判定（契约："窗口内存在同类事件且
        # direction 一致"）——窗口内只要有一条方向吻合就算 hit（如 LPR 月度例会
        # 先 hold 后 cut，cut 落在窗口内仍算命中），不是只看窗口内第一条事件。
        # hit 取"最早一条方向吻合"的事件（离预测最近的确认证据，决定 lag_days）；
        # miss（窗口内有事件但没有一条吻合）取最早一条事件作为参照。
        matching = [c for c in candidates if c[1].get("direction") == pred.direction]
        if matching:
            outcome = OUTCOME_HIT
            ann_date, matched = matching[0]
        else:
            outcome = OUTCOME_MISS
            ann_date, matched = candidates[0]
    else:
        # 无 horizon：窗口开放到 asof，契约明文"匹配层取快照内**最近事件**"
        # （policy_events.py 注释 + 任务书"取最近一条同类事件"）——单事件判定，
        # 不是存在性判定。否则在多年跨度的开放窗口里，只要历史上出现过一次
        # 吻合方向的事件就判 hit，会系统性虚高命中率（哪怕最新政策立场已反转）。
        # 取窗口内**最新**（announce_date 最大）一条同类事件，比对其方向。
        ann_date, matched = candidates[-1]
        outcome = OUTCOME_HIT if matched.get("direction") == pred.direction else OUTCOME_MISS

    lag_days = (ann_date - pred_date).days
    matched_direction = matched.get("direction")

    value_error = None
    if pred.value is not None:
        delta_pp = (matched.get("params") or {}).get("delta_pp")
        if delta_pp is not None:
            value_error = abs(float(pred.value) - float(delta_pp))

    matched_summary = {
        "region": matched.get("region"),
        "category": matched.get("category"),
        "action": matched.get("action"),
        "direction": matched_direction,
        "issuer": matched.get("issuer"),
        "announce_date": matched.get("announce_date"),
        "title": matched.get("title"),
        "delta_pp": (matched.get("params") or {}).get("delta_pp"),
    }

    return {
        **base,
        "outcome": outcome,
        "lag_days": lag_days,
        "value_error": value_error,
        "matched_event": matched_summary,
        "reason": "direction 一致" if outcome == OUTCOME_HIT else "direction 不符",
    }


def score_predictions(
    preds: list[Prediction],
    conn: sqlite3.Connection,
    asof: Optional[str] = None,
) -> dict:
    """批量打分，返回汇总 + 逐条明细。

    asof 缺省取库里 policy_events.announce_date 的最大值。
    """
    if asof is None:
        row = conn.execute("SELECT MAX(announce_date) FROM policy_events").fetchone()
        asof = row[0] if row and row[0] else date.today().isoformat()

    details = []
    for pred in preds:
        if not pred.is_scorable():
            details.append(match_prediction(pred, [], asof))
            continue
        # 预取候选事件池：同 region+category，announce_date 严格大于 pred_date
        # 且不晚于快照边界（match_prediction 内部会再次独立校验，防止绕过）。
        events = query_events(
            conn, region=pred.region, category=pred.category,
            after=pred.pred_date, before=asof,
        )
        details.append(match_prediction(pred, events, asof))

    n_hit = sum(1 for d in details if d["outcome"] == OUTCOME_HIT)
    n_miss = sum(1 for d in details if d["outcome"] == OUTCOME_MISS)
    n_unresolved = sum(1 for d in details if d["outcome"] == OUTCOME_UNRESOLVED)
    n_skipped = sum(1 for d in details if d["outcome"] == OUTCOME_SKIPPED)
    n_scorable = n_hit + n_miss + n_unresolved

    denom = n_hit + n_miss
    strict_hit_rate = (n_hit / denom) if denom else None

    # 契约原文"命中时附 lag_days/value_error"——聚合口径只取 hit，避免 miss 的
    # 方向错误掺进数值误差/滞后统计（miss 时 matched_event 只是"最接近的参照
    # 事件"，不是分析师"预测对了、只是数值差多少"的语境，掺进来会污染这两个
    # 聚合指标；per-record 明细里仍保留 miss 的 lag_days/value_error 供人工核查）。
    hit_lags = [d["lag_days"] for d in details
                if d["outcome"] == OUTCOME_HIT and d["lag_days"] is not None]
    hit_value_errors = [d["value_error"] for d in details
                         if d["outcome"] == OUTCOME_HIT and d["value_error"] is not None]

    persons = sorted({d["person"] for d in details}) or ([preds[0].person] if preds else [])

    return {
        "person": persons[0] if len(persons) == 1 else "+".join(persons),
        "asof": asof,
        "n_total": len(preds),
        "n_scorable": n_scorable,
        "n_hit": n_hit,
        "n_miss": n_miss,
        "n_unresolved": n_unresolved,
        "n_recommendation_skipped": n_skipped,
        "strict_hit_rate": strict_hit_rate,
        "median_lag_days": statistics.median(hit_lags) if hit_lags else None,
        "mean_value_error": statistics.mean(hit_value_errors) if hit_value_errors else None,
        "details": details,
    }


# ── 自测（无 API，纯内存 sqlite，验证三态判定 + 防泄漏 + prediction/recommendation 分离）──

def _selftest() -> bool:
    conn = sqlite3.connect(":memory:")
    ensure_schema(conn)

    # 假事件：
    # 1) 2024-01-15 之前的降息（用于验证防泄漏——不该被 2024-02-01 的预测匹配到）
    upsert_event(conn, PolicyEvent(
        region="CN", category="货币", event_type="scheduled", issuer="PBOC",
        action="LPR_1Y", direction="cut", announce_date="2024-01-22",
        params={"delta_pp": -0.10}, source_tier="manual",
    ))
    # 2) 预测日之后、窗口内的降息事件 —— 应命中 hit
    upsert_event(conn, PolicyEvent(
        region="CN", category="货币", event_type="scheduled", issuer="PBOC",
        action="LPR_1Y", direction="cut", announce_date="2024-02-20",
        params={"delta_pp": -0.15}, source_tier="manual",
    ))
    # 3) 另一 region+category+action，用于 miss 场景：预测 hike，实际 hold
    upsert_event(conn, PolicyEvent(
        region="US", category="货币", event_type="scheduled", issuer="FOMC",
        action="FED_FUNDS", direction="hold", announce_date="2024-03-20",
        params={"delta_pp": 0.0}, source_tier="manual",
    ))
    # 4)+5) 多事件窗口关键场景：RRR 先 hold 后 cut（月度例会常见节奏）——
    #    验证三态判定是"窗口内存在方向吻合的事件"（存在性），而非只看窗口内第一条事件。
    upsert_event(conn, PolicyEvent(
        region="CN", category="货币", event_type="scheduled", issuer="PBOC",
        action="RRR", direction="hold", announce_date="2024-02-10",
        params={"delta_pp": 0.0}, source_tier="manual",
    ))
    upsert_event(conn, PolicyEvent(
        region="CN", category="货币", event_type="scheduled", issuer="PBOC",
        action="RRR", direction="cut", announce_date="2024-03-05",
        params={"delta_pp": -0.25}, source_tier="manual",
    ))
    # 6)+7) 无 horizon 关键场景：MLF_RATE 先 cut(02-10) 后 hold(06-10) ——
    #    验证无 horizon 分支是"单事件/最新事件"判定，不是存在性判定。
    upsert_event(conn, PolicyEvent(
        region="CN", category="货币", event_type="discretionary", issuer="PBOC",
        action="MLF_RATE", direction="cut", announce_date="2024-02-10",
        params={"delta_pp": -0.10}, source_tier="manual",
    ))
    upsert_event(conn, PolicyEvent(
        region="CN", category="货币", event_type="discretionary", issuer="PBOC",
        action="MLF_RATE", direction="hold", announce_date="2024-06-10",
        params={"delta_pp": 0.0}, source_tier="manual",
    ))
    # 8)+9) 有 horizon 的真判别场景：OMO_7D 窗口内先 cut(02-10) 后 hold(03-05)，
    #    matched 事件（cut）**不是**窗口内最新一条（hold 才是最新）——这才真正
    #    区分"存在性"(应 hit，匹配 cut) vs "单事件/最新"(会误判 miss，匹配 hold)。
    #    H 用的是 hold-then-cut，matched 恰好也是最新那条，两种判法结果相同，
    #    不能证明代码走的是存在性分支；这组数据专门堵住这个假阳性。
    upsert_event(conn, PolicyEvent(
        region="CN", category="货币", event_type="scheduled", issuer="PBOC",
        action="OMO_7D", direction="cut", announce_date="2024-02-10",
        params={"delta_pp": -0.10}, source_tier="manual",
    ))
    upsert_event(conn, PolicyEvent(
        region="CN", category="货币", event_type="scheduled", issuer="PBOC",
        action="OMO_7D", direction="hold", announce_date="2024-03-05",
        params={"delta_pp": 0.0}, source_tier="manual",
    ))
    conn.commit()

    preds = [
        # A: 预测日 2024-02-01，之后窗口内(60天)有 2024-02-20 cut 事件 → hit
        Prediction(person="testA", pred_date="2024-02-01", region="CN",
                   category="货币", action="LPR_1Y", direction="cut",
                   kind="prediction", value=-0.10, horizon_days=60,
                   quote="预计LPR下调"),
        # B: 预测日 2024-02-01，预测 hike，实际匹配到的是 cut → miss
        Prediction(person="testA", pred_date="2024-02-01", region="CN",
                   category="货币", action="LPR_1Y", direction="hike",
                   kind="prediction", horizon_days=60, quote="预计LPR上调"),
        # C: 预测日 2024-02-01，horizon 很短(5天)，窗口内无事件 → unresolved
        Prediction(person="testA", pred_date="2024-02-01", region="CN",
                   category="货币", action="LPR_1Y", direction="cut",
                   kind="prediction", horizon_days=5, quote="近期LPR下调"),
        # D: recommendation，不打分 → skipped
        Prediction(person="testA", pred_date="2024-02-01", region="CN",
                   category="货币", action="LPR_1Y", direction="cut",
                   kind="recommendation", quote="建议降息"),
        # E: 防泄漏专项——pred_date 晚于事件1的公布日，但早于本身声称的"预测"，
        #    验证 2024-01-22 的事件（在 pred_date 之前）不会被当作命中的未来事件。
        #    用一个 pred_date 设在事件1之后、事件2之前的预测，窗口给到很短，
        #    确认不会"回头"匹配到 pred_date 之前已发生的事件1。
        Prediction(person="testA", pred_date="2024-01-25", region="CN",
                   category="货币", action="LPR_1Y", direction="cut",
                   kind="prediction", horizon_days=3, quote="防泄漏专项"),
        # F: US FED，预测 hike，实际 hold → miss，同时验证 value_error（无 value）
        Prediction(person="testA", pred_date="2024-03-01", region="US",
                   category="货币", action="FED_FUNDS", direction="hike",
                   kind="prediction", horizon_days=30, quote="预计加息"),
        # H: 多事件窗口关键场景——RRR 窗口内先 hold(02-10) 后 cut(03-05)，
        #    预测 cut。窗口内"存在"一条方向吻合的事件(cut) → 应判 hit，
        #    且 matched_event 应指向 03-05 的 cut，而不是 02-10 的 hold。
        Prediction(person="testA", pred_date="2024-01-01", region="CN",
                   category="货币", action="RRR", direction="cut",
                   kind="prediction", value=-0.25, horizon_days=90,
                   quote="预计降准（多事件窗口场景）"),
    ]

    result = score_predictions(preds, conn, asof="2024-04-01")
    details = {d["pred_id"]: d for d in result["details"]}

    checks = []

    a_id = preds[0].pred_id
    checks.append(("A: hit", details[a_id]["outcome"] == OUTCOME_HIT))
    checks.append(("A: lag_days=19", details[a_id]["lag_days"] == 19))
    checks.append(("A: value_error=0.05",
                    details[a_id]["value_error"] is not None
                    and abs(details[a_id]["value_error"] - 0.05) < 1e-9))

    b_id = preds[1].pred_id
    checks.append(("B: miss", details[b_id]["outcome"] == OUTCOME_MISS))

    c_id = preds[2].pred_id
    checks.append(("C: unresolved (窗口内无事件)", details[c_id]["outcome"] == OUTCOME_UNRESOLVED))

    d_id = preds[3].pred_id
    checks.append(("D: recommendation -> skipped", details[d_id]["outcome"] == OUTCOME_SKIPPED))

    e_id = preds[4].pred_id
    # 事件1(2024-01-22) 早于本预测 pred_date(2024-01-25)，绝不能被匹配；
    # 窗口(2024-01-25~2024-01-28)内也没有其它同类事件 -> unresolved
    checks.append(("E: 防泄漏 -> unresolved (不回头匹配历史事件)",
                    details[e_id]["outcome"] == OUTCOME_UNRESOLVED))

    f_id = preds[5].pred_id
    checks.append(("F: US FED miss", details[f_id]["outcome"] == OUTCOME_MISS))

    # G: 直接调用 match_prediction，喂一个"脏"的全量事件池（未经 query_events 的
    # after 过滤），验证防泄漏是 match_prediction 自身的职责，不是靠调用方过滤：
    # 事件1(2024-01-22 cut) 早于 pred_date(2024-02-10)，绝不能被当作命中；
    # 事件2(2024-02-20 cut) 晚于 pred_date 且在窗口内 -> 应该 hit。
    all_events = query_events(conn)  # 全量，不带 after/before，刻意"泄漏"历史事件
    leak_probe = Prediction(person="testG", pred_date="2024-02-10", region="CN",
                             category="货币", action="LPR_1Y", direction="cut",
                             kind="prediction", horizon_days=15, quote="防泄漏直连探针")
    g_result = match_prediction(leak_probe, all_events, asof="2024-04-01")
    checks.append(("G: match_prediction 自身过滤 -> hit 且匹配到 02-20 而非 01-22",
                    g_result["outcome"] == OUTCOME_HIT
                    and g_result["matched_event"]["announce_date"] == "2024-02-20"))

    leak_probe2 = Prediction(person="testG", pred_date="2024-01-20", region="CN",
                              category="货币", action="LPR_1Y", direction="cut",
                              kind="prediction", horizon_days=1, quote="窗口太短探针")
    g_result2 = match_prediction(leak_probe2, all_events, asof="2024-04-01")
    checks.append(("G2: 窗口(1天)内(01-20~01-21)无事件 -> unresolved，不误配 01-22",
                    g_result2["outcome"] == OUTCOME_UNRESOLVED))

    # I: 无 horizon 关键判别场景（区分"存在性"vs"单事件/最新"两种判法）——
    # MLF_RATE 窗口内先 cut(02-10) 后 hold(06-10)（最新）。预测 cut，无 horizon。
    # 若误用存在性判定 -> 会因窗口内曾出现过 cut 而判 hit（虚高命中率）；
    # 契约要求"取快照内最近事件"（单事件、看最新一条）-> 最新是 hold，应判 miss。
    no_horizon_probe = Prediction(person="testI", pred_date="2024-01-01", region="CN",
                                   category="货币", action="MLF_RATE", direction="cut",
                                   kind="prediction", horizon_days=None,
                                   quote="无horizon判别探针")
    i_result = match_prediction(no_horizon_probe, all_events, asof="2024-12-01")
    checks.append(("I: 无horizon看最新事件(06-10 hold)而非曾出现过的cut -> miss",
                    i_result["outcome"] == OUTCOME_MISS
                    and i_result["matched_event"]["announce_date"] == "2024-06-10"))

    # J: 有 horizon 的真判别场景——OMO_7D 窗口内先 cut(02-10) 后 hold(03-05，即
    # 窗口内最新一条)。预测 cut，horizon=90。存在性判定 -> hit，匹配 cut(02-10)；
    # 若误用"单事件/最新"判法 -> 会看最新的 hold 而判 miss。H 场景里 matched 恰好
    # 也是最新事件，两种判法结果一样，不能证伪；这组数据才真正锁定分支走向。
    horizon_exist_probe = Prediction(person="testJ", pred_date="2024-01-01", region="CN",
                                      category="货币", action="OMO_7D", direction="cut",
                                      kind="prediction", horizon_days=90,
                                      quote="有horizon存在性判别探针")
    j_result = match_prediction(horizon_exist_probe, all_events, asof="2024-12-01")
    checks.append(("J: 有horizon存在性判定 -> hit，匹配 02-10 的cut而非窗口内最新的03-05 hold",
                    j_result["outcome"] == OUTCOME_HIT
                    and j_result["matched_event"]["announce_date"] == "2024-02-10"))

    h_id = preds[6].pred_id
    # 关键场景：窗口内先 hold(02-10) 后 cut(03-05)，预测 cut。存在性判定 -> hit，
    # 且必须匹配到 cut 那条（03-05），而不是窗口内第一条 hold（02-10）。
    checks.append(("H: 窗口内先hold后cut，预测cut -> hit（存在性判定，非只看第一条）",
                    details[h_id]["outcome"] == OUTCOME_HIT))
    checks.append(("H: matched_event 是 03-05 的 cut，不是 02-10 的 hold",
                    details[h_id]["matched_event"] is not None
                    and details[h_id]["matched_event"]["announce_date"] == "2024-03-05"
                    and details[h_id]["matched_event"]["direction"] == "cut"))
    checks.append(("H: lag_days=64（到 cut 事件，不是到 hold 事件）",
                    details[h_id]["lag_days"] == 64))

    checks.append(("n_recommendation_skipped == 1", result["n_recommendation_skipped"] == 1))
    checks.append(("n_scorable == 6 (A,B,C,E,F,H)", result["n_scorable"] == 6))
    checks.append(("n_hit == 2 (A, H)", result["n_hit"] == 2))
    checks.append(("n_miss == 2 (B, F)", result["n_miss"] == 2))
    checks.append(("n_unresolved == 2 (C, E)", result["n_unresolved"] == 2))
    checks.append(("strict_hit_rate == 2/4", result["strict_hit_rate"] is not None
                    and abs(result["strict_hit_rate"] - 0.5) < 1e-9))

    ok = True
    print("=== policy_score._selftest ===")
    for name, passed in checks:
        mark = "OK" if passed else "FAIL"
        print(f"[{mark}] {name}")
        ok = ok and passed

    print(json.dumps(
        {k: v for k, v in result.items() if k != "details"},
        ensure_ascii=False, indent=2,
    ))
    print("=== 全部通过 ===" if ok else "=== 存在失败项 ===")
    return ok


# ── CLI ──────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="方法D：政策预测匹配打分")
    ap.add_argument("--preds", default=None, help="output/policy/predictions_<person>.json；缺省跑自测")
    ap.add_argument("--asof", default=None, help="快照边界 YYYY-MM-DD，缺省取库内 announce_date 最大值")
    ap.add_argument("--db", default="output/x_agent.db", help="policy_events 所在库路径")
    args = ap.parse_args()

    if not args.preds:
        ok = _selftest()
        raise SystemExit(0 if ok else 1)

    preds = load_predictions(args.preds)
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    try:
        result = score_predictions(preds, conn, asof=args.asof)
    finally:
        conn.close()

    print(f"person={result['person']} asof={result['asof']}")
    print(f"n_total={result['n_total']} n_scorable={result['n_scorable']} "
          f"n_hit={result['n_hit']} n_miss={result['n_miss']} "
          f"n_unresolved={result['n_unresolved']} "
          f"n_recommendation_skipped={result['n_recommendation_skipped']}")
    hr = result["strict_hit_rate"]
    print(f"strict_hit_rate={hr:.3f}" if hr is not None else "strict_hit_rate=N/A（分母为0）")
    print(f"median_lag_days={result['median_lag_days']} "
          f"mean_value_error={result['mean_value_error']}")
    for d in result["details"]:
        print(f"  [{d['outcome']:>10}] {d['pred_id']} {d['region']}/{d['category']}/"
              f"{d['action']} pred={d['direction']}@{d['pred_date']} "
              f"-> {d.get('matched_event')}")

    out_dir = Path("output/policy")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"score_{result['person']}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"已写 {out_path}")


if __name__ == "__main__":
    main()
