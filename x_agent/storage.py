"""SQLite 存储层：去重保存推文与信号。"""
from __future__ import annotations

import json
import sqlite3
import datetime as dt

from .fetcher import Tweet
from .classifier import Signal

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tweets(
  id TEXT PRIMARY KEY,
  author TEXT, text TEXT, created_at TEXT, url TEXT,
  source_label TEXT, metrics TEXT, fetched_at TEXT,
  group_tag TEXT DEFAULT '',
  source TEXT DEFAULT 'twitter'
);
CREATE TABLE IF NOT EXISTS signals(
  tweet_id TEXT PRIMARY KEY,
  category TEXT, score INTEGER, tickers TEXT, extracted TEXT,
  FOREIGN KEY(tweet_id) REFERENCES tweets(id)
);
CREATE INDEX IF NOT EXISTS idx_signals_cat ON signals(category);
"""


class Store:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        # 先迁移旧表（加列），再建索引，避免索引引用不存在的列
        for col, default in [("group_tag", "''"), ("source", "'twitter'")]:
            try:
                self.conn.execute(f"ALTER TABLE tweets ADD COLUMN {col} TEXT DEFAULT {default}")
                self.conn.commit()
            except Exception:
                pass
        self.conn.executescript(_SCHEMA)
        for idx in [
            "CREATE INDEX IF NOT EXISTS idx_tweets_group ON tweets(group_tag)",
            "CREATE INDEX IF NOT EXISTS idx_tweets_source ON tweets(source)",
        ]:
            try:
                self.conn.execute(idx)
                self.conn.commit()
            except Exception:
                pass

    def seen(self, tweet_id: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM tweets WHERE id=?", (tweet_id,))
        return cur.fetchone() is not None

    def save(self, tweet: Tweet, signal: Signal) -> None:
        source = "xiaohongshu" if tweet.group_tag == "xiaohongshu" else "twitter"
        self.conn.execute(
            "INSERT OR REPLACE INTO tweets VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                tweet.id, tweet.author, tweet.text, tweet.created_at, tweet.url,
                tweet.source_label, json.dumps(tweet.metrics),
                dt.datetime.utcnow().isoformat(),
                tweet.group_tag, source,
            ),
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO signals VALUES (?,?,?,?,?)",
            (
                signal.tweet_id, signal.category, signal.score,
                json.dumps(signal.tickers),
                json.dumps(signal.extracted, ensure_ascii=False),
            ),
        )
        self.conn.commit()

    def recent_signals(self, categories: list[str], limit: int = 80):
        placeholders = ",".join("?" * len(categories))
        q = (
            "SELECT t.author, t.text, t.url, t.created_at, "
            "s.category, s.score, s.tickers, s.extracted "
            "FROM signals s JOIN tweets t ON t.id = s.tweet_id "
            f"WHERE s.category IN ({placeholders}) "
            "ORDER BY t.created_at DESC LIMIT ?"
        )
        return self.conn.execute(q, (*categories, limit)).fetchall()
