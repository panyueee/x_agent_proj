"""SQLite 存储层：去重保存推文、信号与金融行情数据。"""
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
CREATE TABLE IF NOT EXISTS price_bars(
  symbol TEXT,
  market TEXT,
  timestamp TEXT,
  open REAL,
  high REAL,
  low REAL,
  close REAL,
  volume REAL,
  change_pct REAL,
  name TEXT,
  fetched_at TEXT,
  PRIMARY KEY (symbol, timestamp)
);
CREATE INDEX IF NOT EXISTS idx_price_bars_market ON price_bars(market);
CREATE INDEX IF NOT EXISTS idx_price_bars_symbol ON price_bars(symbol);
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
        if tweet.group_tag == "xiaohongshu":
            source = "xiaohongshu"
        elif tweet.group_tag == "taoguba":
            source = "taoguba"
        else:
            source = "twitter"
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

    def recent_signals(self, categories, limit: int = 80):
        placeholders = ",".join("?" * len(categories))
        q = (
            "SELECT t.author, t.text, t.url, t.created_at, "
            "s.category, s.score, s.tickers, s.extracted "
            "FROM signals s JOIN tweets t ON t.id = s.tweet_id "
            f"WHERE s.category IN ({placeholders}) "
            "ORDER BY t.created_at DESC LIMIT ?"
        )
        return self.conn.execute(q, (*categories, limit)).fetchall()

    # ---- 金融行情存取 ----

    def save_price_bar(self, bar) -> None:
        """保存或覆盖一条行情记录（以 symbol+timestamp 为主键去重）。"""
        self.conn.execute(
            "INSERT OR REPLACE INTO price_bars "
            "(symbol, market, timestamp, open, high, low, close, volume, change_pct, name, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                bar.symbol, bar.market, bar.timestamp,
                bar.open, bar.high, bar.low, bar.close,
                bar.volume, bar.change_pct, bar.name,
                dt.datetime.utcnow().isoformat(),
            ),
        )
        self.conn.commit()

    def recent_price_bars(self, market: str, limit: int = 50):
        """返回指定市场最新的行情记录列表（每个品种取最新一条）。

        返回元素：(symbol, name, market, timestamp, open, high, low, close, volume, change_pct)
        """
        q = (
            "SELECT p.symbol, p.name, p.market, p.timestamp, "
            "p.open, p.high, p.low, p.close, p.volume, p.change_pct "
            "FROM price_bars p "
            "INNER JOIN ("
            "  SELECT symbol, MAX(timestamp) AS max_ts "
            "  FROM price_bars WHERE market=? GROUP BY symbol"
            ") latest ON p.symbol = latest.symbol AND p.timestamp = latest.max_ts "
            "ORDER BY p.symbol LIMIT ?"
        )
        return self.conn.execute(q, (market, limit)).fetchall()

    def latest_price(self, symbol: str):
        """返回指定品种的最新一条行情，以字典形式返回，找不到时返回 None。"""
        q = (
            "SELECT symbol, name, market, timestamp, open, high, low, close, volume, change_pct "
            "FROM price_bars WHERE symbol=? ORDER BY timestamp DESC LIMIT 1"
        )
        row = self.conn.execute(q, (symbol,)).fetchone()
        if row is None:
            return None
        keys = ["symbol", "name", "market", "timestamp", "open", "high", "low", "close", "volume", "change_pct"]
        return dict(zip(keys, row))
