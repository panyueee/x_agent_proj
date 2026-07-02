# -*- coding: utf-8 -*-
"""训练/测试评估：时间切分——用 until=T 的画像对 [T, T+ΔT) 的真实文章盲预测→LLM-judge 对答案。

未来函数防线：
- 训练语料由 build_corpus(until_date=T) 强制过滤（date < T）；
- 测试集 = T <= date < T + test_window_days；
- 评估前 assert 训练集最大日期 < T（双保险）。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from .corpus import Article, build_corpus, normalize_until
from .llm import extract_json, load_prompt, split_sections
from .predict import predict, question_from_title
from .profile import _person_dir, build_profile, load_profile

logger = logging.getLogger(__name__)

JUDGE_ARTICLE_CHARS = 3000  # judge 看到的真实文章截断长度
VERDICT_SCORE = {"hit": 1.0, "partial": 0.5, "miss": 0.0}


def split_test_set(
    articles: list[Article], train_until, test_window_days: int
) -> list[Article]:
    """从全量文章中切出测试窗口 [T, T+ΔT)。"""
    t0 = normalize_until(train_until)
    t1 = t0 + timedelta(days=test_window_days)
    return [a for a in articles if t0 <= a.date < t1]


def judge_one(person: str, prediction: dict, article: Article, llm) -> dict:
    """LLM-judge：比对盲预测与真实文章，输出 hit/partial/miss + 理由。"""
    tpl = split_sections(load_prompt("persona_judge.md"))["JUDGE"]
    prompt = tpl.format(
        prediction=json.dumps(
            {k: prediction.get(k) for k in ("proposition", "direction", "confidence", "rationale")},
            ensure_ascii=False,
        ),
        title=article.title,
        date=str(article.date.date()),
        article_text=article.text[:JUDGE_ARTICLE_CHARS],
    )
    system = "你是严格的评审员，输出严格 JSON。"
    raw = llm.complete(system, prompt, max_tokens=800)
    verdict = extract_json(raw)
    v = str(verdict.get("verdict", "miss")).lower().strip()
    if v not in VERDICT_SCORE:
        logger.warning("judge 返回未知 verdict=%r，按 miss 处理", v)
        v = "miss"
    return {"verdict": v, "reason": verdict.get("reason", "")}


def evaluate(
    person: str,
    train_until: str,
    test_window_days: int,
    llm,
    db_path=None,
    rebuild_profile: bool = False,
    max_test_articles: int | None = None,
) -> dict:
    """端到端评估。返回汇总 dict，逐条结果写 eval_results.jsonl，报告写 eval_report.md。"""
    train_until = str(train_until)[:10]
    t0 = normalize_until(train_until)

    # 1) 画像（训练集）：build_corpus 内部已按 until 过滤
    profile = None if rebuild_profile else load_profile(person, train_until)
    if profile is None:
        train_articles = build_corpus(person, until_date=train_until, db_path=db_path)
        assert all(a.date < t0 for a in train_articles), "训练集泄漏：存在 >= train_until 的文章"
        profile = build_profile(person, train_until, llm, articles=train_articles, db_path=db_path)
    else:
        logger.info("复用已有画像 profile_until_%s.json", train_until)

    # 2) 测试集：[T, T+ΔT) 的真实文章
    all_articles = build_corpus(person, until_date=None, db_path=db_path)
    test_articles = split_test_set(all_articles, train_until, test_window_days)
    if max_test_articles:
        test_articles = test_articles[:max_test_articles]
    if not test_articles:
        raise ValueError(f"{person} 在 [{train_until}, +{test_window_days}d) 内无测试文章")

    # 3) 逐篇：盲预测 → judge 对答案（单篇失败跳过，不炸整轮）
    results = []
    for i, art in enumerate(test_articles, 1):
        try:
            q = question_from_title(art.title)
            pred = predict(person, profile, q, llm, log_to_file=True)
            j = judge_one(person, pred, art, llm)
            results.append({
                "title": art.title, "date": str(art.date.date()), "question": q,
                "prediction": pred["proposition"], "direction": pred["direction"],
                "confidence": pred.get("confidence"),
                "verdict": j["verdict"], "reason": j["reason"],
            })
            logger.info("[%d/%d] %s -> %s", i, len(test_articles), art.title[:30], j["verdict"])
        except Exception as e:  # 失败不炸主流程
            logger.warning("评估单篇失败，跳过: %s (%s)", art.title[:40], e)

    # 4) 汇总与落盘
    n = len(results)
    counts = {v: sum(1 for r in results if r["verdict"] == v) for v in VERDICT_SCORE}
    weighted = sum(VERDICT_SCORE[r["verdict"]] for r in results) / n if n else 0.0
    strict = counts.get("hit", 0) / n if n else 0.0
    summary = {
        "person": person, "train_until": train_until,
        "test_window_days": test_window_days,
        "n_test": n, "counts": counts,
        "strict_hit_rate": round(strict, 3),
        "weighted_hit_rate": round(weighted, 3),
        "evaluated_at": datetime.now().isoformat(timespec="seconds"),
    }

    d = _person_dir(person)
    with (d / "eval_results.jsonl").open("a", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps({**r, "train_until": train_until}, ensure_ascii=False) + "\n")
    (d / "eval_report.md").write_text(_render_report(summary, results), encoding="utf-8")
    logger.info("评估报告已写入 %s", d / "eval_report.md")
    return summary


def _render_report(summary: dict, results: list[dict]) -> str:
    lines = [
        f"# {summary['person']} 预测评估报告",
        "",
        f"- 画像截止（train_until）：{summary['train_until']}",
        f"- 测试窗口：其后 {summary['test_window_days']} 天，共 {summary['n_test']} 篇真实文章",
        f"- 严格命中率（仅 hit）：**{summary['strict_hit_rate']:.1%}**",
        f"- 加权命中率（hit=1, partial=0.5）：**{summary['weighted_hit_rate']:.1%}**",
        f"- 分布：hit {summary['counts'].get('hit', 0)} / partial {summary['counts'].get('partial', 0)}"
        f" / miss {summary['counts'].get('miss', 0)}",
        f"- 评估时间：{summary['evaluated_at']}",
        "",
        "> 方法：对测试窗口内每篇真实文章，从标题提取去结论化主题作为预测问题，"
        "预测器只见画像；LLM-judge 独立比对预测与原文核心观点。"
        "局限：主题词本身可能暗示方向，命中率存在一定乐观偏差。",
        "",
        "## 逐条明细",
        "",
        "| 日期 | 文章 | 预测方向 | 预测命题 | 判定 | 理由 |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        def esc(s):
            return str(s or "").replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {r['date']} | {esc(r['title'])[:40]} | {esc(r['direction'])} "
            f"| {esc(r['prediction'])[:60]} | **{r['verdict']}** | {esc(r['reason'])[:80]} |"
        )
    return "\n".join(lines) + "\n"
