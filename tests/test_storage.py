"""
x_agent/storage.py 的 pytest 测试套件。

覆盖高价值行为：
  - 在临时路径上打开 Store → 建表/建索引无异常；同路径重复打开幂等（IF NOT EXISTS）。
  - 去重：同一 tweet id 写两次不会重复；seen() 在写前/写后返回正确真值。
  - 往返：写入 tweet/signal 后读回，字段一致。
  - save_factor_returns（最近重构为 executemany）：单次写入多行全部落库且可重查；
    空输入、单行各覆盖一遍，专门守护该优化。
  - 打开时的列迁移 / ALTER 逻辑在全新库上不报错。

所有测试都使用 pytest tmp_path 下的临时数据库，绝不触碰真实
output/rag.db 或 output/x_agent.db，且不联网。

可用 pytest 运行：
    python -m pytest tests/test_storage.py -v
也可直接当脚本运行（无 pytest 依赖）：
    python tests/test_storage.py
"""
from __future__ import annotations

import os
import sqlite3
import sys

# 让 `import x_agent.storage` 在任意 cwd 下都能工作
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from x_agent.storage import Store
from x_agent.fetcher import Tweet
from x_agent.classifier import Signal


# ── 辅助 ──────────────────────────────────────────────────────────────────────

def _db_path(tmp_path) -> str:
    """tmp_path 下一个独立的临时数据库文件路径。"""
    return str(tmp_path / "test_store.db")


def _make_tweet(tweet_id: str = "t1", group_tag: str = "") -> Tweet:
    return Tweet(
        id=tweet_id,
        author="alice",
        author_id="uid_alice",
        text="宁德时代发布新一代电池 $CATL bullish",
        created_at="2026-06-30T08:00:00",
        url=f"https://x.com/alice/status/{tweet_id}",
        metrics={"like": 12, "rt": 3},
        source_label="serenity_following",
        group_tag=group_tag,
    )


def _make_signal(tweet_id: str = "t1") -> Signal:
    return Signal(
        tweet_id=tweet_id,
        category="finance",
        score=7,
        tickers=["CATL", "300750"],
        extracted={"theme": "动力电池", "sentiment": "bullish"},
    )


class _FakeDF:
    """模拟 toraniko 因子收益率 Polars DataFrame。

    save_factor_returns 只用到 `.columns` 与 `.to_dicts()` 两个接口，
    这里用纯 Python 结构复刻，避免引入 polars 依赖。
    """

    def __init__(self, columns, rows):
        self.columns = list(columns)
        self._rows = [dict(r) for r in rows]

    def to_dicts(self):
        return [dict(r) for r in self._rows]


# ── 1. 建表 / 幂等 ────────────────────────────────────────────────────────────

def test_open_creates_schema(tmp_path):
    path = _db_path(tmp_path)
    store = Store(path)
    # 文件应被创建
    assert os.path.exists(path)
    # 关键表均应存在
    names = {
        r[0] for r in store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    for tbl in ["tweets", "signals", "price_bars", "industry_nodes",
                "research_reports", "pipeline_events", "companies",
                "dragon_tiger", "north_flow", "fundamentals",
                "quarterly_financials", "company_persons"]:
        assert tbl in names, f"缺少表 {tbl}"


def test_open_creates_indexes(tmp_path):
    store = Store(_db_path(tmp_path))
    idx = {
        r[0] for r in store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }
    # schema 内索引 + __init__ 里单独建的 tweets 索引
    for name in ["idx_signals_cat", "idx_price_bars_market",
                 "idx_tweets_group", "idx_tweets_source"]:
        assert name in idx, f"缺少索引 {name}"


def test_reopen_same_path_idempotent(tmp_path):
    """同一路径重复打开不应崩溃（IF NOT EXISTS / ALTER 容错）。"""
    path = _db_path(tmp_path)
    s1 = Store(path)
    s1.save(_make_tweet("keep"), _make_signal("keep"))
    s1.conn.close()
    # 第二次打开同一已建库：建表 + 迁移列 + 建索引都应被安全跳过
    s2 = Store(path)
    assert s2.seen("keep") is True
    # 仍可正常写入
    s2.save(_make_tweet("keep2"), _make_signal("keep2"))
    assert s2.seen("keep2") is True


def test_tweets_has_migrated_columns(tmp_path):
    """ALTER 迁移列 group_tag / source 在全新库上不报错且列存在。"""
    store = Store(_db_path(tmp_path))
    cols = {r[1] for r in store.conn.execute("PRAGMA table_info(tweets)").fetchall()}
    assert "group_tag" in cols
    assert "source" in cols


# ── 2. 去重 ───────────────────────────────────────────────────────────────────

def test_seen_before_and_after_insert(tmp_path):
    store = Store(_db_path(tmp_path))
    assert store.seen("t1") is False
    store.save(_make_tweet("t1"), _make_signal("t1"))
    assert store.seen("t1") is True
    assert store.seen("does_not_exist") is False


def test_duplicate_insert_no_dup_rows(tmp_path):
    store = Store(_db_path(tmp_path))
    store.save(_make_tweet("dup"), _make_signal("dup"))
    store.save(_make_tweet("dup"), _make_signal("dup"))  # 同 id 再写一次
    cnt = store.conn.execute(
        "SELECT COUNT(*) FROM tweets WHERE id=?", ("dup",)
    ).fetchone()[0]
    assert cnt == 1
    sig_cnt = store.conn.execute(
        "SELECT COUNT(*) FROM signals WHERE tweet_id=?", ("dup",)
    ).fetchone()[0]
    assert sig_cnt == 1


# ── 3. 往返读写 ───────────────────────────────────────────────────────────────

def test_tweet_roundtrip_fields(tmp_path):
    store = Store(_db_path(tmp_path))
    tw = _make_tweet("rt1")
    store.save(tw, _make_signal("rt1"))
    row = store.conn.execute(
        "SELECT id, author, text, created_at, url, source_label, group_tag, source "
        "FROM tweets WHERE id=?", ("rt1",)
    ).fetchone()
    assert row is not None
    assert row[0] == tw.id
    assert row[1] == tw.author
    assert row[2] == tw.text
    assert row[3] == tw.created_at
    assert row[4] == tw.url
    assert row[5] == tw.source_label
    assert row[6] == ""          # 默认 group_tag
    assert row[7] == "twitter"   # 默认 source 映射


def test_source_mapping_from_group_tag(tmp_path):
    store = Store(_db_path(tmp_path))
    store.save(_make_tweet("xhs", group_tag="xiaohongshu"), _make_signal("xhs"))
    store.save(_make_tweet("tgb", group_tag="taoguba"), _make_signal("tgb"))
    src_xhs = store.conn.execute(
        "SELECT source FROM tweets WHERE id=?", ("xhs",)).fetchone()[0]
    src_tgb = store.conn.execute(
        "SELECT source FROM tweets WHERE id=?", ("tgb",)).fetchone()[0]
    assert src_xhs == "xiaohongshu"
    assert src_tgb == "taoguba"


def test_recent_signals_roundtrip(tmp_path):
    store = Store(_db_path(tmp_path))
    store.save(_make_tweet("s1"), _make_signal("s1"))
    rows = store.recent_signals(["finance"], limit=10)
    assert len(rows) == 1
    # (author, text, url, created_at, category, score, tickers, extracted)
    author, text, url, created_at, category, score, tickers, extracted = rows[0]
    assert author == "alice"
    assert category == "finance"
    assert score == 7
    # tickers / extracted 以 JSON 存储
    import json
    assert json.loads(tickers) == ["CATL", "300750"]
    assert json.loads(extracted)["theme"] == "动力电池"


# ── 4. save_factor_returns（executemany 优化守护） ──────────────────────────────

def test_save_factor_returns_multi_rows(tmp_path):
    store = Store(_db_path(tmp_path))
    df = _FakeDF(
        columns=["date", "mkt", "size", "value"],
        rows=[
            {"date": "2026-06-25", "mkt": 0.01, "size": -0.02, "value": 0.03},
            {"date": "2026-06-26", "mkt": 0.02, "size": -0.01, "value": 0.04},
            {"date": "2026-06-27", "mkt": -0.03, "size": 0.05, "value": -0.01},
        ],
    )
    store.save_factor_returns(df)
    rows = store.conn.execute(
        "SELECT date, mkt, size, value FROM factor_returns ORDER BY date"
    ).fetchall()
    assert len(rows) == 3, "executemany 应一次写入全部 3 行"
    assert rows[0] == ("2026-06-25", 0.01, -0.02, 0.03)
    assert rows[1] == ("2026-06-26", 0.02, -0.01, 0.04)
    assert rows[2] == ("2026-06-27", -0.03, 0.05, -0.01)


def test_save_factor_returns_single_row(tmp_path):
    store = Store(_db_path(tmp_path))
    df = _FakeDF(
        columns=["date", "mkt"],
        rows=[{"date": "2026-06-30", "mkt": 0.123}],
    )
    store.save_factor_returns(df)
    rows = store.conn.execute(
        "SELECT date, mkt FROM factor_returns"
    ).fetchall()
    assert rows == [("2026-06-30", 0.123)]


def test_save_factor_returns_empty(tmp_path):
    store = Store(_db_path(tmp_path))
    df = _FakeDF(columns=["date", "mkt"], rows=[])
    # 空输入应提前返回 None，且不建表、不报错
    assert store.save_factor_returns(df) is None
    tbl = store.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='factor_returns'"
    ).fetchone()
    assert tbl is None, "空输入不应创建 factor_returns 表"


def test_save_factor_returns_upsert_overwrites(tmp_path):
    """同一 date 再次写入应覆盖（INSERT OR REPLACE，主键 date）。"""
    store = Store(_db_path(tmp_path))
    store.save_factor_returns(_FakeDF(["date", "mkt"], [{"date": "2026-06-30", "mkt": 0.1}]))
    store.save_factor_returns(_FakeDF(["date", "mkt"], [{"date": "2026-06-30", "mkt": 0.9}]))
    rows = store.conn.execute("SELECT date, mkt FROM factor_returns").fetchall()
    assert rows == [("2026-06-30", 0.9)]


# ── 5. 不触碰真实数据库 ────────────────────────────────────────────────────────

def test_uses_isolated_temp_db(tmp_path):
    """确认临时库与项目 output/*.db 完全隔离。"""
    path = _db_path(tmp_path)
    store = Store(path)
    store.save(_make_tweet("iso"), _make_signal("iso"))
    assert os.path.abspath(path).startswith(os.path.abspath(str(tmp_path)))
    # 临时库可独立验证
    assert sqlite3.connect(path).execute(
        "SELECT COUNT(*) FROM tweets"
    ).fetchone()[0] == 1


# ── 独立运行入口（无 pytest 时） ──────────────────────────────────────────────

def _run_standalone() -> int:
    import tempfile
    import pathlib

    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        with tempfile.TemporaryDirectory() as d:
            try:
                fn(pathlib.Path(d))
                print(f"PASS  {fn.__name__}")
                passed += 1
            except AssertionError as e:
                print(f"FAIL  {fn.__name__}: {e}")
                failed += 1
            except Exception as e:  # noqa: BLE001
                print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
                failed += 1
    print(f"\n{passed} passed, {failed} failed (total {passed + failed})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_standalone())
