# -*- coding: utf-8 -*-
"""persona_methods 专用语料加载器（只读 rag.db）。

复用 x_agent.persona.corpus 的原语（日期解析 / 只读连接 / chunk 拼接），
在其基础上补齐主 corpus.py 未映射的来源：
  - source_type='column'（李迅雷 9271、张瑜 8662），日期在 extra_meta.date
  - source_type='research_web'（张瑜、郭磊），日期在 extra_meta.date
  - wechat feed：明晰笔谈=明明、戴康的策略世界=戴康
不修改 corpus.py，避免与张瑜 grounded 实验撞车。

CLI:
  .venv/bin/python scripts/pm_corpus.py --person 李迅雷 --until 2026-07-01 --list
  .venv/bin/python scripts/pm_corpus.py --person 戴康 --until 2026-04-05 --dump-titles
  .venv/bin/python scripts/pm_corpus.py --person 牟一凌 --article-id "research::..." --text
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from x_agent.persona.corpus import (  # noqa: E402
    Article, parse_iso_date, parse_research_title_date, normalize_until,
    _connect_ro, _stitch, DEFAULT_DB_PATH,
)

# 人物 → 各 source_type 的匹配键
# wechat/column/research_web 用 author 精确匹配（含 feed 名）；research 用 LIKE
PM_SOURCES: dict[str, dict] = {
    "张瑜":   {"wechat": ["一瑜中的"], "column": ["张瑜"], "research_web": ["张瑜"], "research_like": ["张瑜"]},
    "李迅雷": {"wechat": ["李迅雷金融与投资"], "column": ["李迅雷"], "research_web": [], "research_like": []},
    "罗志恒": {"wechat": ["粤开志恒宏观"], "column": [], "research_web": [], "research_like": ["罗志恒"]},
    "牟一凌": {"wechat": ["一凌策略研究"], "column": [], "research_web": [], "research_like": ["牟一凌"]},
    "戴康":   {"wechat": ["戴康的策略世界"], "column": [], "research_web": [], "research_like": []},
    "明明":   {"wechat": ["明晰笔谈"], "column": [], "research_web": [], "research_like": []},
    "郭磊":   {"wechat": [], "column": [], "research_web": ["郭磊"], "research_like": []},
}


def _fetch_meta_dated(conn, source_type: str, authors: list[str]) -> list[Article]:
    """wechat/column/research_web：author 精确匹配，日期取 extra_meta.date。"""
    out: list[Article] = []
    for author in authors:
        cur = conn.execute(
            "SELECT source_id, title, chunk_idx, content, extra_meta "
            "FROM chunks WHERE source_type=? AND author=? "
            "ORDER BY source_id, chunk_idx",
            (source_type, author),
        )
        by_sid: dict[str, list] = {}
        meta_by_sid: dict[str, tuple] = {}
        for sid, title, idx, content, em in cur:
            by_sid.setdefault(sid, []).append((idx, content))
            if sid not in meta_by_sid:
                meta_by_sid[sid] = (title, em)
        for sid, chunks in by_sid.items():
            title, em = meta_by_sid[sid]
            try:
                meta = json.loads(em or "{}")
            except (json.JSONDecodeError, TypeError):
                meta = {}
            date = parse_iso_date(meta.get("date", ""))
            if date is None:
                continue
            chunks.sort(key=lambda x: x[0])
            out.append(Article(
                article_id=f"{source_type}::{sid}", title=title or "", date=date,
                source_type=source_type, text=_stitch(chunks),
                url=meta.get("url", ""), extra={"broker": meta.get("broker", "")},
            ))
    return out


def _fetch_research_like(conn, authors: list[str]) -> list[Article]:
    """research：author LIKE 匹配，日期取标题前缀。按标题分组还原整篇。"""
    out: list[Article] = []
    for author in authors:
        cur = conn.execute(
            "SELECT source_id, title, chunk_idx, content, extra_meta "
            "FROM chunks WHERE source_type='research' AND author LIKE ? "
            "ORDER BY title, source_id, chunk_idx",
            (f"%{author}%",),
        )
        by_title: dict[str, list] = {}
        for sid, title, idx, content, em in cur:
            try:
                meta = json.loads(em or "{}")
            except (json.JSONDecodeError, TypeError):
                meta = {}
            page_start = meta.get("page_start", 0) or 0
            by_title.setdefault(title or sid, []).append((page_start, idx, sid, content))
        for title, chunks in by_title.items():
            date = parse_research_title_date(title)
            if date is None:
                continue
            chunks.sort(key=lambda x: (x[0], x[1]))
            out.append(Article(
                article_id=f"research::{title}", title=title, date=date,
                source_type="research", text=_stitch(chunks),
            ))
    return out


def build_corpus(person: str, until_date=None, db_path=None,
                 max_chars_per_article=None) -> list[Article]:
    if person not in PM_SOURCES:
        raise KeyError(f"未配置人物: {person}（可选 {list(PM_SOURCES)}）")
    src = PM_SOURCES[person]
    db_path = db_path or DEFAULT_DB_PATH
    conn = _connect_ro(db_path)
    try:
        arts: list[Article] = []
        arts += _fetch_meta_dated(conn, "wechat", src.get("wechat", []))
        arts += _fetch_meta_dated(conn, "column", src.get("column", []))
        arts += _fetch_meta_dated(conn, "research_web", src.get("research_web", []))
        arts += _fetch_research_like(conn, src.get("research_like", []))
    finally:
        conn.close()

    until = normalize_until(until_date)
    if until is not None:
        arts = [a for a in arts if a.date < until]
    if max_chars_per_article:
        for a in arts:
            if len(a.text) > max_chars_per_article:
                a.text = a.text[:max_chars_per_article]
    arts.sort(key=lambda a: (a.date, a.article_id))
    return arts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--person", required=True)
    ap.add_argument("--until", default=None)
    ap.add_argument("--list", action="store_true", help="打印统计")
    ap.add_argument("--dump-titles", action="store_true", help="打印 日期\\t来源\\t标题")
    ap.add_argument("--article-id", default=None)
    ap.add_argument("--text", action="store_true", help="配合 --article-id 打印全文")
    ap.add_argument("--max-chars", type=int, default=None)
    args = ap.parse_args()

    arts = build_corpus(args.person, args.until, max_chars_per_article=args.max_chars)
    if args.article_id:
        for a in arts:
            if a.article_id == args.article_id:
                print(f"# {a.title}\n[{a.date.date()}] {a.source_type}\n")
                print(a.text if args.text else a.text[:1500])
                return
        print("未找到 article_id", file=sys.stderr)
        return
    if args.dump_titles:
        for a in arts:
            print(f"{a.date.date()}\t{a.source_type}\t{a.title}\t{a.article_id}")
        return
    # 默认 / --list
    by_type: dict[str, int] = {}
    for a in arts:
        by_type[a.source_type] = by_type.get(a.source_type, 0) + 1
    span = (arts[0].date.date(), arts[-1].date.date()) if arts else (None, None)
    print(f"person={args.person} until={args.until} 文章数={len(arts)} 日期跨度={span} 分布={by_type}")


if __name__ == "__main__":
    main()
