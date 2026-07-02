# -*- coding: utf-8 -*-
"""语料抽取测试：until_date 时间过滤（未来函数防线）、chunk 拼接、日期解析。"""
import json
import sqlite3
from datetime import datetime

import pytest

from x_agent.persona.corpus import (
    build_corpus,
    normalize_until,
    parse_iso_date,
    parse_research_title_date,
)


@pytest.fixture
def db(tmp_path):
    """造一个最小 rag.db 快照：张瑜 3 篇 wechat + 1 篇研报（2 个 page-range chunk）。"""
    path = tmp_path / "rag.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE chunks (id TEXT PRIMARY KEY, source_id TEXT, source_type TEXT,"
        " title TEXT, author TEXT, chunk_idx INTEGER, total_chunks INTEGER,"
        " content TEXT, extra_meta TEXT)"
    )
    def meta(date):
        return json.dumps({"date": date, "url": "http://x"})
    rows = [
        # 文章A：两个 chunk，故意乱序插入，验证按 chunk_idx 拼接
        ("a1", "wechat:a", "wechat", "观点甲——主题甲", "一瑜中的", 1, 2, "甲下半", meta("2026-05-10T08:00:00.000Z")),
        ("a0", "wechat:a", "wechat", "观点甲——主题甲", "一瑜中的", 0, 2, "甲上半", meta("2026-05-10T08:00:00.000Z")),
        # 文章B：边界日当天（6-01 00:30 UTC）
        ("b0", "wechat:b", "wechat", "观点乙", "一瑜中的", 0, 1, "乙全文", meta("2026-06-01T00:30:00.000Z")),
        # 文章C：边界之后
        ("c0", "wechat:c", "wechat", "观点丙", "一瑜中的", 0, 1, "丙全文", meta("2026-06-15T08:00:00.000Z")),
        # 文章D：无日期，应被排除
        ("d0", "wechat:d", "wechat", "无日期文", "一瑜中的", 0, 1, "d全文", "{}"),
        # 研报：同一报告两个 page-range source_id
        ("r0", "research:em:h1:p1-5", "research", "2026-05-20 策略周报：测试", "华创证券·张瑜,某某", 0, 1,
         "研报前半", json.dumps({"page_start": 1, "page_end": 5})),
        ("r1", "research:em:h1:p6-9", "research", "2026-05-20 策略周报：测试", "华创证券·张瑜,某某", 0, 1,
         "研报后半", json.dumps({"page_start": 6, "page_end": 9})),
    ]
    conn.executemany("INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return path


def test_until_filter_is_strict(db):
    """until_date 是硬边界：>= until 的文章绝不能进训练语料。"""
    arts = build_corpus("张瑜", until_date="2026-06-01", db_path=db)
    titles = [a.title for a in arts]
    assert "观点丙" not in titles          # 边界之后排除
    assert "观点乙" not in titles          # 边界日当天（00:30 > 00:00）也排除
    assert any("观点甲" in t for t in titles)
    assert all(a.date < normalize_until("2026-06-01") for a in arts)


def test_no_until_returns_all_dated(db):
    arts = build_corpus("张瑜", db_path=db)
    titles = [a.title for a in arts]
    assert "观点丙" in titles
    assert "无日期文" not in titles        # 无日期一律排除
    # 按时间升序
    dates = [a.date for a in arts]
    assert dates == sorted(dates)


def test_chunk_stitch_order(db):
    arts = build_corpus("张瑜", db_path=db)
    a = next(x for x in arts if "观点甲" in x.title)
    assert a.text == "甲上半\n甲下半"


def test_research_grouped_by_title_and_dated(db):
    arts = build_corpus("张瑜", db_path=db)
    r = [x for x in arts if x.source_type == "research"]
    assert len(r) == 1                     # 两个 page-range 并成一篇
    assert r[0].text == "研报前半\n研报后半"
    assert r[0].date == datetime(2026, 5, 20)


def test_max_chars_truncation(db):
    arts = build_corpus("张瑜", db_path=db, max_chars_per_article=3)
    assert all(len(a.text) <= 3 for a in arts)


def test_parse_iso_date_utc_normalization():
    assert parse_iso_date("2026-07-01T01:36:48.000Z") == datetime(2026, 7, 1, 1, 36, 48)
    assert parse_iso_date("2026-07-01T09:00:00+08:00") == datetime(2026, 7, 1, 1, 0, 0)
    assert parse_iso_date("") is None
    assert parse_iso_date("not-a-date") is None


def test_parse_research_title_date():
    assert parse_research_title_date("2026-06-28 A股策略周报：两个世界") == datetime(2026, 6, 28)
    assert parse_research_title_date("无日期标题") is None


def test_unknown_person_raises(db):
    with pytest.raises(KeyError):
        build_corpus("不存在的人", db_path=db)
