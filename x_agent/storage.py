"""SQLite 存储层：去重保存推文、信号、金融行情、产业链、研报数据。"""
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

CREATE TABLE IF NOT EXISTS industry_nodes(
  code        TEXT PRIMARY KEY,
  name        TEXT,
  role        TEXT,   -- upstream / core / downstream / competitor
  chain       TEXT,   -- 所属产业链名称，如 "新能源汽车"
  notes       TEXT DEFAULT '',
  updated_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_nodes_chain ON industry_nodes(chain);

CREATE TABLE IF NOT EXISTS chain_events(
  id             TEXT PRIMARY KEY,
  chain          TEXT,
  title          TEXT,
  content        TEXT,
  source         TEXT,
  url            TEXT,
  published_at   TEXT,
  relevance_score REAL DEFAULT 0.0,
  fetched_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_chain       ON chain_events(chain);
CREATE INDEX IF NOT EXISTS idx_events_published   ON chain_events(published_at);

CREATE TABLE IF NOT EXISTS research_reports(
  report_id     TEXT PRIMARY KEY,
  stock_code    TEXT,
  stock_name    TEXT,
  title         TEXT,
  org_name      TEXT,
  analyst       TEXT,
  rating        TEXT,   -- buy / outperform / neutral / underperform / sell
  rating_raw    TEXT,   -- 原始评级文字
  target_price  REAL,
  published_at  TEXT,
  url           TEXT,
  summary       TEXT DEFAULT '',
  fetched_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_reports_stock   ON research_reports(stock_code);
CREATE INDEX IF NOT EXISTS idx_reports_pub     ON research_reports(published_at);
CREATE INDEX IF NOT EXISTS idx_reports_rating  ON research_reports(rating);

CREATE TABLE IF NOT EXISTS supplier_updates(
  id             TEXT PRIMARY KEY,
  supplier_code  TEXT DEFAULT '',
  supplier_name  TEXT,
  customer_name  TEXT,
  event_type     TEXT,   -- order / capacity / cooperation / risk / news
  title          TEXT,
  content        TEXT,
  source         TEXT,
  published_at   TEXT,
  url            TEXT DEFAULT '',
  fetched_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_supplier_name  ON supplier_updates(supplier_name);
CREATE INDEX IF NOT EXISTS idx_supplier_cust  ON supplier_updates(customer_name);
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

    # ---- 产业链 ----

    def save_industry_node(self, node) -> None:
        """新增或更新产业链节点（以 code 去重）。"""
        self.conn.execute(
            "INSERT OR REPLACE INTO industry_nodes "
            "(code, name, role, chain, notes, updated_at) VALUES (?,?,?,?,?,?)",
            (node.code, node.name, node.role, node.chain,
             node.notes, dt.datetime.utcnow().isoformat()),
        )
        self.conn.commit()

    def save_chain_event(self, event) -> None:
        """保存产业链事件，以 chain+url 的 hash 去重。"""
        import hashlib
        eid = hashlib.md5(f"{event.chain}|{event.url}|{event.title}".encode()).hexdigest()
        self.conn.execute(
            "INSERT OR IGNORE INTO chain_events "
            "(id, chain, title, content, source, url, published_at, relevance_score, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (eid, event.chain, event.title, event.content, event.source,
             event.url, event.published_at, event.relevance_score,
             dt.datetime.utcnow().isoformat()),
        )
        self.conn.commit()

    def chain_nodes(self, chain: str) -> list:
        """返回某产业链下所有节点列表。"""
        return self.conn.execute(
            "SELECT code, name, role, chain, notes, updated_at "
            "FROM industry_nodes WHERE chain=? ORDER BY role, name",
            (chain,),
        ).fetchall()

    def recent_chain_events(self, chain: str, limit: int = 30) -> list:
        """返回某产业链最新事件，按 published_at 倒序。"""
        return self.conn.execute(
            "SELECT chain, title, content, source, url, published_at, relevance_score "
            "FROM chain_events WHERE chain=? "
            "ORDER BY published_at DESC LIMIT ?",
            (chain, limit),
        ).fetchall()

    def all_chains(self) -> list:
        """返回已记录的所有产业链名称。"""
        rows = self.conn.execute(
            "SELECT DISTINCT chain FROM industry_nodes ORDER BY chain"
        ).fetchall()
        return [r[0] for r in rows]

    # ---- 研报 ----

    _RATING_MAP = {
        "买入": "buy", "强烈推荐": "buy", "强推": "buy",
        "增持": "outperform", "推荐": "outperform", "优于大市": "outperform",
        "中性": "neutral", "持有": "neutral", "观望": "neutral",
        "减持": "underperform", "低于大市": "underperform",
        "卖出": "sell", "回避": "sell",
    }

    def _normalize_rating(self, raw: str) -> str:
        raw = (raw or "").strip()
        for k, v in self._RATING_MAP.items():
            if k in raw:
                return v
        return "neutral"

    def save_report(self, report) -> None:
        """保存研报（以 report_id 去重）。"""
        rating = self._normalize_rating(report.rating)
        self.conn.execute(
            "INSERT OR IGNORE INTO research_reports "
            "(report_id, stock_code, stock_name, title, org_name, analyst, "
            "rating, rating_raw, target_price, published_at, url, summary, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (report.report_id, report.stock_code, report.stock_name, report.title,
             report.org_name, report.analyst, rating, report.rating,
             report.target_price, report.published_at, report.url,
             report.summary, dt.datetime.utcnow().isoformat()),
        )
        self.conn.commit()

    def save_supplier_update(self, update) -> None:
        """保存供应商动态，以 supplier+url+title hash 去重。"""
        import hashlib
        uid = hashlib.md5(
            f"{update.supplier_name}|{update.url}|{update.title}".encode()
        ).hexdigest()
        self.conn.execute(
            "INSERT OR IGNORE INTO supplier_updates "
            "(id, supplier_code, supplier_name, customer_name, event_type, "
            "title, content, source, published_at, url, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (uid, update.supplier_code, update.supplier_name, update.customer_name,
             update.event_type, update.title, update.content, update.source,
             update.published_at, update.url, dt.datetime.utcnow().isoformat()),
        )
        self.conn.commit()

    def recent_reports(self, stock_code: str, limit: int = 20) -> list:
        """返回某股票最新研报，按 published_at 倒序。"""
        return self.conn.execute(
            "SELECT stock_name, title, org_name, analyst, rating, rating_raw, "
            "target_price, published_at, url "
            "FROM research_reports WHERE stock_code=? "
            "ORDER BY published_at DESC LIMIT ?",
            (stock_code, limit),
        ).fetchall()

    def rating_summary(self, stock_code: str) -> dict:
        """统计某股票各评级数量，返回 {rating: count}。"""
        rows = self.conn.execute(
            "SELECT rating, COUNT(*) FROM research_reports "
            "WHERE stock_code=? GROUP BY rating",
            (stock_code,),
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    def recent_supplier_updates(self, customer_name: str, limit: int = 20) -> list:
        """返回某核心公司的供应商动态。"""
        return self.conn.execute(
            "SELECT supplier_name, event_type, title, content, source, published_at, url "
            "FROM supplier_updates WHERE customer_name=? "
            "ORDER BY published_at DESC LIMIT ?",
            (customer_name, limit),
        ).fetchall()
