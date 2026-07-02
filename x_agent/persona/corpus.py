# -*- coding: utf-8 -*-
"""语料抽取：从 rag.db（只读）按人物拉取文章，chunk 拼回文章粒度，按时间排序。

关键约束：
- rag.db 有入库进程在写，本模块一律以 sqlite 只读模式打开，绝不写库；
- until_date 是防未来函数的硬边界：date < until_date 才会进入返回结果；
- 无法解析日期的文章一律排除（无法证明不泄漏未来信息）。
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "output" / "rag.db"

# 人物 ↔ 语料映射：wechat 的 author 字段是公众号 feed 名；研报 author 是"券商·作者串"
PERSON_SOURCES: dict[str, dict] = {
    "张瑜":   {"wechat_feeds": ["一瑜中的"], "research_authors": ["张瑜"]},
    "李迅雷": {"wechat_feeds": ["李迅雷金融与投资"], "research_authors": ["李迅雷"]},
    "罗志恒": {"wechat_feeds": ["粤开志恒宏观"], "research_authors": ["罗志恒"]},
    "牟一凌": {"wechat_feeds": ["一凌策略研究"], "research_authors": ["牟一凌"]},
    "芦哲":   {"wechat_feeds": [], "research_authors": ["芦哲"]},
}

# 研报标题日期前缀，如 "2026-06-28 A股策略周报：……"
_RESEARCH_TITLE_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+")


@dataclass
class Article:
    """文章粒度语料（chunks 已拼接）。date 统一为 UTC naive datetime。"""
    article_id: str
    title: str
    date: datetime
    source_type: str
    text: str
    url: str = ""
    extra: dict = field(default_factory=dict)


def parse_iso_date(raw: str) -> Optional[datetime]:
    """解析 ISO 8601（含尾缀 Z）→ UTC naive datetime；失败返回 None。"""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = (dt - dt.utcoffset()).replace(tzinfo=None)  # 归一为 UTC naive
    return dt


def parse_research_title_date(title: str) -> Optional[datetime]:
    """从研报标题前缀 'YYYY-MM-DD ' 解析日期。"""
    m = _RESEARCH_TITLE_DATE.match(title or "")
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d")
    except ValueError:
        return None


def normalize_until(until_date) -> Optional[datetime]:
    """until_date 支持 'YYYY-MM-DD' 字符串或 datetime；统一为 UTC naive datetime。"""
    if until_date is None:
        return None
    if isinstance(until_date, datetime):
        if until_date.tzinfo is not None:
            until_date = (until_date - until_date.utcoffset()).replace(tzinfo=None)
        return until_date
    return datetime.strptime(str(until_date)[:10], "%Y-%m-%d")


def _connect_ro(db_path) -> sqlite3.Connection:
    """只读打开 rag.db —— 库还在被入库进程写，绝不能以可写模式打开。"""
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _stitch(rows: list[tuple]) -> str:
    """按排好序的 chunks 拼回全文。rows: [(sort_key..., content)]"""
    return "\n".join(r[-1] for r in rows if r[-1])


def _fetch_wechat(conn: sqlite3.Connection, feeds: list[str]) -> list[Article]:
    articles: list[Article] = []
    for feed in feeds:
        cur = conn.execute(
            "SELECT source_id, title, chunk_idx, content, extra_meta "
            "FROM chunks WHERE source_type='wechat' AND author=? "
            "ORDER BY source_id, chunk_idx",
            (feed,),
        )
        by_sid: dict[str, list] = {}
        meta_by_sid: dict[str, tuple] = {}
        for sid, title, idx, content, extra_meta in cur:
            by_sid.setdefault(sid, []).append((idx, content))
            if sid not in meta_by_sid:
                meta_by_sid[sid] = (title, extra_meta)
        for sid, chunks in by_sid.items():
            title, extra_meta = meta_by_sid[sid]
            try:
                meta = json.loads(extra_meta or "{}")
            except (json.JSONDecodeError, TypeError):
                meta = {}
            date = parse_iso_date(meta.get("date", ""))
            if date is None:
                logger.warning("跳过无日期 wechat 文章: %s %s", sid, title)
                continue
            chunks.sort(key=lambda x: x[0])
            articles.append(Article(
                article_id=sid, title=title or "", date=date,
                source_type="wechat", text=_stitch(chunks),
                url=meta.get("url", ""),
            ))
    return articles


def _fetch_research(conn: sqlite3.Connection, authors: list[str]) -> list[Article]:
    """研报：同一报告可能被切成多个 page-range source_id，按标题分组还原。"""
    articles: list[Article] = []
    for author in authors:
        cur = conn.execute(
            "SELECT source_id, title, chunk_idx, content, extra_meta "
            "FROM chunks WHERE source_type='research' AND author LIKE ? "
            "ORDER BY title, source_id, chunk_idx",
            (f"%{author}%",),
        )
        by_title: dict[str, list] = {}
        for sid, title, idx, content, extra_meta in cur:
            try:
                meta = json.loads(extra_meta or "{}")
            except (json.JSONDecodeError, TypeError):
                meta = {}
            page_start = meta.get("page_start", 0) or 0
            by_title.setdefault(title or sid, []).append((page_start, idx, sid, content))
        for title, chunks in by_title.items():
            date = parse_research_title_date(title)
            if date is None:
                logger.warning("跳过无日期研报: %s", title)
                continue
            chunks.sort(key=lambda x: (x[0], x[1]))
            articles.append(Article(
                article_id=f"research::{title}", title=title, date=date,
                source_type="research", text=_stitch(chunks),
            ))
    return articles


def build_corpus(
    person: str,
    until_date=None,
    db_path=None,
    max_chars_per_article: Optional[int] = None,
) -> list[Article]:
    """按人物拉取全部语料（文章粒度、按时间升序）。

    until_date：硬边界，只保留 date < until_date 的文章（防未来函数）。
    max_chars_per_article：每篇正文截断（控制 LLM 成本）。
    """
    if person not in PERSON_SOURCES:
        raise KeyError(f"未配置的人物: {person}（可选: {list(PERSON_SOURCES)}）")
    src = PERSON_SOURCES[person]
    db_path = db_path or DEFAULT_DB_PATH
    conn = _connect_ro(db_path)
    try:
        articles = _fetch_wechat(conn, src.get("wechat_feeds", []))
        articles += _fetch_research(conn, src.get("research_authors", []))
    finally:
        conn.close()

    until = normalize_until(until_date)
    if until is not None:
        articles = [a for a in articles if a.date < until]  # 严格小于：边界日当天不入训练集

    if max_chars_per_article:
        for a in articles:
            if len(a.text) > max_chars_per_article:
                a.text = a.text[:max_chars_per_article]

    articles.sort(key=lambda a: (a.date, a.article_id))
    return articles
