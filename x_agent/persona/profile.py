# -*- coding: utf-8 -*-
"""画像构建：map-reduce 读语料 → 结构化画像 JSON + Markdown。

成本控制：每篇截断 ~2000 字、每批 10 篇一次 map 调用、语料 >max_articles 只取最近的。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from .corpus import Article, build_corpus, normalize_until
from .llm import extract_json, load_prompt, split_sections

logger = logging.getLogger(__name__)

PERSONAS_DIR = Path(__file__).resolve().parents[2] / "output" / "personas"

MAP_CHARS_PER_ARTICLE = 2000   # 每篇正文截断
MAP_BATCH_SIZE = 10            # 每次 map 调用的文章数
MAX_ARTICLES = 100             # 语料上限：只取最近 N 篇


def _person_dir(person: str) -> Path:
    d = PERSONAS_DIR / person
    d.mkdir(parents=True, exist_ok=True)
    return d


def profile_paths(person: str, until_date: str) -> tuple[Path, Path]:
    d = _person_dir(person)
    stem = f"profile_until_{until_date}"
    return d / f"{stem}.json", d / f"{stem}.md"


def _render_articles(batch: list[Article]) -> str:
    parts = []
    for a in batch:
        parts.append(
            f"### {a.date.date()} | {a.title}\n{a.text[:MAP_CHARS_PER_ARTICLE]}\n"
        )
    return "\n".join(parts)


def _render_markdown(profile: dict) -> str:
    """把画像 JSON 渲染成人读 Markdown。"""
    lines = [f"# {profile.get('person', '')} 画像（截止 {profile.get('until_date', '')}）", ""]
    lines += ["## 总结", profile.get("summary", ""), ""]

    def bullet(title, key):
        lines.append(f"## {title}")
        for item in profile.get(key, []) or []:
            lines.append(f"- {item}")
        lines.append("")

    bullet("分析框架与方法论", "analytical_framework")
    bullet("核心世界观假设", "worldview")

    lines.append("## 观点时间线")
    for t in profile.get("timeline", []) or []:
        shift = f"（转变：{t.get('shift')}）" if t.get("shift") else ""
        lines.append(f"- **{t.get('period', '')}**：{t.get('stance', '')}{shift}")
    lines.append("")

    lines.append("## 显式预测清单")
    for p in profile.get("explicit_predictions", []) or []:
        lines.append(f"- [{p.get('date', '')}] {p.get('prediction', '')}"
                     f"（验证方式：{p.get('verifiable_by', '')}）")
    lines.append("")

    bullet("写作与推理模式", "style")
    return "\n".join(lines)


def build_profile(
    person: str,
    until_date: str,
    llm,
    articles: Optional[list[Article]] = None,
    db_path=None,
) -> dict:
    """构建画像并落盘。articles 可注入（测试用）；否则从 rag.db 拉取。

    返回画像 dict；失败抛异常（由 CLI 捕获，不炸调用方主流程）。
    """
    until_str = str(until_date)[:10]
    if articles is None:
        articles = build_corpus(person, until_date=until_date, db_path=db_path)
    # 双保险：即使调用方传入 articles，也强制 until 过滤
    until = normalize_until(until_date)
    if until is not None:
        articles = [a for a in articles if a.date < until]
    if not articles:
        raise ValueError(f"{person} 在 {until_str} 之前没有语料")
    if len(articles) > MAX_ARTICLES:
        logger.info("语料 %d 篇，只取最近 %d 篇", len(articles), MAX_ARTICLES)
        articles = articles[-MAX_ARTICLES:]

    sections = split_sections(load_prompt("persona_profile.md"))
    map_tpl, reduce_tpl = sections["MAP"], sections["REDUCE"]
    system = "你是研究金融分析师思维方式的专业分析员，输出精炼、有依据。"

    # ---- MAP：分批提笔记 ----
    notes: list[str] = []
    for i in range(0, len(articles), MAP_BATCH_SIZE):
        batch = articles[i:i + MAP_BATCH_SIZE]
        prompt = map_tpl.format(person=person, articles=_render_articles(batch))
        note = llm.complete(system, prompt, max_tokens=2500)
        notes.append(note)
        logger.info("map 批次 %d/%d 完成", i // MAP_BATCH_SIZE + 1,
                    (len(articles) + MAP_BATCH_SIZE - 1) // MAP_BATCH_SIZE)

    # ---- REDUCE：汇总成画像 JSON ----
    prompt = reduce_tpl.format(person=person, until_date=until_str,
                               notes="\n\n---\n\n".join(notes))
    raw = llm.complete(system, prompt, max_tokens=6000)
    profile = extract_json(raw)
    profile.setdefault("person", person)
    profile["until_date"] = until_str
    profile["_meta"] = {
        "n_articles": len(articles),
        "corpus_span": [str(articles[0].date.date()), str(articles[-1].date.date())],
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "model": getattr(llm, "model", "unknown"),
    }

    json_path, md_path = profile_paths(person, until_str)
    json_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(profile), encoding="utf-8")
    logger.info("画像已写入 %s / %s", json_path, md_path)
    return profile


def load_profile(person: str, until_date: str) -> Optional[dict]:
    json_path, _ = profile_paths(person, str(until_date)[:10])
    if not json_path.exists():
        return None
    return json.loads(json_path.read_text(encoding="utf-8"))
