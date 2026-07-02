"""entity_resolver 回归测试：解析歧义 / cashtag / A股口径 / 反向索引。

用临时 seeded DB，不依赖线上 output/x_agent.db（其内容随抓取变动）。
可 pytest 运行，也可直接当脚本跑。
"""
from __future__ import annotations

import os
import sys
import json
import sqlite3
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest  # noqa: E402

from x_agent.entity_resolver import (  # noqa: E402
    EntityResolver,
    build_security_mentions,
    get_security_view,
    ensure_schema,
)

# (symbol, market, name, sector, aliases)
_SEED_SECURITIES = [
    ("sz.300750", "a", "宁德时代", "Industrials", ["300750", "宁德时代"]),
    ("sh.600105", "a", "永鼎股份", "Industrials", ["600105", "永鼎股份"]),
    ("sz.000001", "a", "平安银行", "Financials", ["000001", "平安银行"]),
    ("NVDA", "us", "", "", []),
    ("QQQ", "us", "", "", []),
    ("BTC-USD", "crypto", "", "", ["$BTC"]),
    ("SOL-USD", "crypto", "", "", ["$SOL"]),
    # 制造一处同名歧义
    ("sh.600631", "a", "百联股份", "", ["600631", "百联股份"]),
    ("sh.600827", "a", "百联股份", "", ["600827", "百联股份"]),
]


def _seed_db(path: str):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE securities(symbol TEXT PRIMARY KEY, market TEXT, name TEXT DEFAULT '',"
        " sector_gics TEXT DEFAULT '', aliases TEXT DEFAULT '[]', has_parquet INTEGER DEFAULT 0,"
        " updated_at TEXT)"
    )
    conn.executemany(
        "INSERT INTO securities(symbol,market,name,sector_gics,aliases) VALUES (?,?,?,?,?)",
        [(s, m, n, sec, json.dumps(a, ensure_ascii=False)) for s, m, n, sec, a in _SEED_SECURITIES],
    )
    # 源表：tweets + signals
    conn.execute(
        "CREATE TABLE tweets(id TEXT PRIMARY KEY, author TEXT, text TEXT, created_at TEXT,"
        " url TEXT, source_label TEXT, metrics TEXT, fetched_at TEXT, group_tag TEXT, source TEXT)"
    )
    conn.execute(
        "CREATE TABLE signals(tweet_id TEXT PRIMARY KEY, category TEXT, score INTEGER,"
        " tickers TEXT, extracted TEXT)"
    )
    tweets = [
        ("t1", "trader", "long $NVDA and $BTC here", "2026-06-28T00:00:00Z", "twitter"),
        ("t2", "米开朗基瑞", "今天量化第一式识别 永鼎股份 600105，宁德时代也不错", "2026-06-27T00:00:00Z", "taoguba"),
        ("t3", "eng", "price target 300000 soon for $QQQ", "2026-06-26T00:00:00Z", "twitter"),
    ]
    for tid, au, tx, ca, src in tweets:
        conn.execute(
            "INSERT INTO tweets(id,author,text,created_at,source) VALUES (?,?,?,?,?)",
            (tid, au, tx, ca, src),
        )
    conn.execute("INSERT INTO signals VALUES (?,?,?,?,?)", ("t1", "strategy", 5, json.dumps(["$NVDA", "$BTC"]), "{}"))
    conn.execute("INSERT INTO signals VALUES (?,?,?,?,?)", ("t3", "strategy", 3, json.dumps(["$QQQ"]), "{}"))
    # research_reports：验证研报扫描分支 + 别名歧义在该分支不硬挂
    conn.execute(
        "CREATE TABLE research_reports(report_id TEXT PRIMARY KEY, stock_code TEXT, stock_name TEXT,"
        " title TEXT, org_name TEXT, analyst TEXT, rating TEXT, rating_raw TEXT, target_price REAL,"
        " published_at TEXT, url TEXT, summary TEXT, fetched_at TEXT)"
    )
    conn.execute(
        "INSERT INTO research_reports(report_id,stock_code,stock_name,published_at) VALUES (?,?,?,?)",
        ("r1", "300750", "宁德时代", "2026-06-20T00:00:00Z"),
    )
    conn.execute(
        "INSERT INTO research_reports(report_id,stock_code,stock_name,published_at) VALUES (?,?,?,?)",
        ("r2", "", "百联股份", "2026-06-21T00:00:00Z"),   # 歧义名，不应硬挂
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def seeded_db():
    d = tempfile.mkdtemp(prefix="entres_")
    path = os.path.join(d, "x_agent.db")
    _seed_db(path)
    yield path


@pytest.fixture()
def resolver(seeded_db):
    r = EntityResolver(seeded_db)
    yield r
    r.close()


# ---------------- 单提及解析 ----------------
def test_cashtag_crypto(resolver):
    assert resolver.resolve("$BTC").security_id == "BTC-USD"
    assert resolver.resolve("$btc").security_id == "BTC-USD"   # 大小写不敏感
    assert resolver.resolve("$SOL").security_id == "SOL-USD"


def test_cashtag_us_symbol(resolver):
    # 去 $ 猜美股：命中但只给中置信（0.75），非高置信 —— 同名 crypto 代币会误挂
    r = resolver.resolve("$NVDA")
    assert r.security_id == "NVDA"
    assert r.method == "cashtag_us"
    assert not r.high_conf and 0.5 < r.confidence < 0.9
    assert resolver.resolve("$QQQ").security_id == "QQQ"


def test_ashare_code(resolver):
    assert resolver.resolve("300750").security_id == "sz.300750"
    assert resolver.resolve("600105").security_id == "sh.600105"
    assert resolver.resolve("000001").security_id == "sz.000001"


def test_exact_symbol_and_name(resolver):
    assert resolver.resolve("NVDA").confidence == 1.0
    assert resolver.resolve("sz.300750").confidence == 1.0
    assert resolver.resolve("宁德时代").security_id == "sz.300750"


def test_ambiguous_name_not_hard_attached(resolver):
    r = resolver.resolve("百联股份")
    assert r.method.endswith("_ambiguous")
    assert r.confidence < 0.5
    assert set(r.candidates) == {"sh.600631", "sh.600827"}


def test_unknown_returns_none(resolver):
    assert resolver.resolve("$UNKNOWNXYZ") is None
    assert resolver.resolve("999999") is None
    assert resolver.resolve("") is None


# ---------------- 文本解析 + 假阳性门控 ----------------
def test_ashare_code_gated_off_in_non_cjk(resolver):
    # 英文推文里的 300000 是噪声，不应命中 sz.300750 之类
    res = resolver.resolve_text("price target 300000 for $QQQ", "twitter")
    ids = {r.security_id for r in res}
    assert ids == {"QQQ"}   # 只认 cashtag，不认裸数字


def test_cjk_text_resolves_code_and_name(resolver):
    res = resolver.resolve_text("永鼎股份 600105，宁德时代 也不错", "taoguba")
    ids = {r.security_id for r in res}
    assert "sh.600105" in ids
    assert "sz.300750" in ids   # 中文名 fuzzy


def test_name_fuzzy_only_for_cjk_sources(resolver):
    # twitter 源不做中文名子串扫描
    res = resolver.resolve_text("宁德时代 is great", "twitter")
    assert all(r.method != "name_fuzzy" for r in res)


# ---------------- 反向索引 + 视图 ----------------
def test_build_index_and_view(seeded_db):
    stats = build_security_mentions(seeded_db, rebuild=True)
    assert stats["total_rows"] > 0
    assert stats["by_source"].get("tweet", 0) > 0

    # NVDA：signal_ticker provenance；cashtag_us 为中置信，不计高置信
    view = get_security_view("NVDA", db_path=seeded_db)
    assert view["found"]
    assert view["mentions_by_source"]["tweet"]["count"] >= 1
    methods = {m["method"] for m in view["recent_mentions"]}
    assert "signal_ticker" in methods

    # 宁德时代：研报(高置信 code/name) + tgb 低置信 name_fuzzy 跨来源都进来
    v2 = get_security_view("sz.300750", db_path=seeded_db)
    assert v2["mention_total"] >= 1
    assert "research" in v2["mentions_by_source"]
    assert v2["mentions_by_source"]["research"]["high_conf"] >= 1

    # 歧义名 百联股份 在研报分支不应被硬挂
    conn = sqlite3.connect(seeded_db)
    amb = conn.execute(
        "SELECT COUNT(*) FROM security_mentions WHERE source_ref='r2'"
    ).fetchone()[0]
    conn.close()
    assert amb == 0

    # 幂等：重建两次行数一致
    s2 = build_security_mentions(seeded_db, rebuild=True)
    assert s2["total_rows"] == stats["total_rows"]


def test_no_signal_tweet_double_count(seeded_db):
    # t1 同时有 signals(\$NVDA,\$BTC) 和文本(\$NVDA \$BTC)，每标的对该 tweet 只应 1 行
    build_security_mentions(seeded_db, rebuild=True)
    conn = sqlite3.connect(seeded_db)
    n = conn.execute(
        "SELECT COUNT(*) FROM security_mentions WHERE source_ref='t1' AND security_id='NVDA'"
    ).fetchone()[0]
    conn.close()
    assert n == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
