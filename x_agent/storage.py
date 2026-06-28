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

CREATE TABLE IF NOT EXISTS pipeline_events(
  id           TEXT PRIMARY KEY,   -- hash(source_agent+event_type+payload_key)
  source_agent TEXT,               -- 触发方：x / industry / research
  target_agent TEXT,               -- 接收方：industry / research
  event_type   TEXT,               -- industry_trigger / research_trigger
  payload      TEXT,               -- JSON：触发内容（话题、股票代码等）
  status       TEXT DEFAULT 'pending',  -- pending / processing / done / error
  created_at   TEXT,
  processed_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_pipeline_status ON pipeline_events(status);
CREATE INDEX IF NOT EXISTS idx_pipeline_target ON pipeline_events(target_agent, status);

CREATE TABLE IF NOT EXISTS industry_insights(
  id            TEXT PRIMARY KEY,   -- hash(tweet_id)
  tweet_id      TEXT,
  source        TEXT,               -- twitter / xiaohongshu / taoguba
  chain         TEXT,
  companies     TEXT,               -- JSON: [{code, name, role}]
  relationships TEXT,               -- JSON: [{from, to, type, detail}]
  events        TEXT,               -- JSON: [{title, content}]
  raw_text      TEXT,               -- 原始帖子摘要
  confidence    REAL DEFAULT 0.0,
  extracted_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_insights_chain  ON industry_insights(chain);
CREATE INDEX IF NOT EXISTS idx_insights_source ON industry_insights(source);

CREATE TABLE IF NOT EXISTS companies(
  credit_code   TEXT PRIMARY KEY,   -- 统一社会信用代码
  name          TEXT,               -- 企业名称
  legal_rep     TEXT,               -- 法定代表人
  reg_capital   TEXT,               -- 注册资本
  established   TEXT,               -- 成立日期
  status        TEXT,               -- 经营状态
  industry      TEXT,               -- 所属行业
  address       TEXT,
  phone         TEXT,
  email         TEXT,
  scope         TEXT,               -- 经营范围
  raw_json      TEXT,               -- 完整原始 JSON
  fetched_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_companies_name ON companies(name);

CREATE TABLE IF NOT EXISTS company_persons(
  id            TEXT PRIMARY KEY,   -- hash(credit_code + name + role)
  credit_code   TEXT,               -- 所属企业
  name          TEXT,               -- 人员姓名
  role          TEXT,               -- legal_rep / shareholder / executive / investor
  title         TEXT,               -- 职位/头衔
  share_ratio   TEXT DEFAULT '',    -- 持股比例（股东）
  invest_amount TEXT DEFAULT '',    -- 投资金额
  fetched_at    TEXT,
  FOREIGN KEY(credit_code) REFERENCES companies(credit_code)
);
CREATE INDEX IF NOT EXISTS idx_persons_company ON company_persons(credit_code);
CREATE INDEX IF NOT EXISTS idx_persons_name    ON company_persons(name);
CREATE INDEX IF NOT EXISTS idx_persons_role    ON company_persons(role);
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
        elif tweet.group_tag == "onchain":
            # 链上数据来自 Dune Analytics，source 标记为 onchain
            source = "onchain"
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

    # ---- pipeline 事件队列 ----

    def push_pipeline_event(self, source_agent: str, target_agent: str,
                             event_type: str, payload: dict, idempotency_key: str = "") -> bool:
        """写入一条管道事件，以 idempotency_key 去重（相同 key 只写一次）。
        返回 True 表示新写入，False 表示已存在。
        """
        import hashlib
        key = idempotency_key or f"{source_agent}|{event_type}|{json.dumps(payload, sort_keys=True)}"
        eid = hashlib.md5(key.encode()).hexdigest()
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO pipeline_events "
            "(id, source_agent, target_agent, event_type, payload, status, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (eid, source_agent, target_agent, event_type,
             json.dumps(payload, ensure_ascii=False),
             "pending", dt.datetime.utcnow().isoformat()),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def pending_pipeline_events(self, target_agent: str) -> list:
        """取出指定接收方的所有 pending 事件，返回 dict 列表。"""
        rows = self.conn.execute(
            "SELECT id, source_agent, event_type, payload, created_at "
            "FROM pipeline_events WHERE target_agent=? AND status='pending' "
            "ORDER BY created_at",
            (target_agent,),
        ).fetchall()
        return [
            {"id": r[0], "source_agent": r[1], "event_type": r[2],
             "payload": json.loads(r[3]), "created_at": r[4]}
            for r in rows
        ]

    def mark_pipeline_event(self, event_id: str, status: str = "done") -> None:
        self.conn.execute(
            "UPDATE pipeline_events SET status=?, processed_at=? WHERE id=?",
            (status, dt.datetime.utcnow().isoformat(), event_id),
        )
        self.conn.commit()

    # ---- 产业链学习洞察 ----

    def insight_exists(self, tweet_id: str) -> bool:
        """判断某条推文是否已经被提取过洞察。"""
        return self.conn.execute(
            "SELECT 1 FROM industry_insights WHERE tweet_id=?", (tweet_id,)
        ).fetchone() is not None

    def save_insight(self, tweet_id: str, source: str, chain: str,
                     companies: list, relationships: list, events: list,
                     raw_text: str, confidence: float) -> None:
        import hashlib
        iid = hashlib.md5(tweet_id.encode()).hexdigest()
        self.conn.execute(
            "INSERT OR IGNORE INTO industry_insights "
            "(id, tweet_id, source, chain, companies, relationships, events, "
            "raw_text, confidence, extracted_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (iid, tweet_id, source, chain,
             json.dumps(companies, ensure_ascii=False),
             json.dumps(relationships, ensure_ascii=False),
             json.dumps(events, ensure_ascii=False),
             raw_text, confidence,
             dt.datetime.utcnow().isoformat()),
        )
        self.conn.commit()

    def unprocessed_signals(self, min_score: int = 4,
                             sources: tuple = ("twitter", "xiaohongshu", "taoguba"),
                             limit: int = 50) -> list:
        """返回尚未被学习处理的高分信号帖（finance/strategy 类）。"""
        placeholders = ",".join("?" * len(sources))
        rows = self.conn.execute(
            f"SELECT t.id, t.text, t.source, t.author, t.created_at "
            f"FROM signals s JOIN tweets t ON t.id=s.tweet_id "
            f"WHERE s.score >= ? AND t.source IN ({placeholders}) "
            f"AND s.category IN ('strategy','finance','both','strategy+finance') "
            f"AND t.id NOT IN (SELECT tweet_id FROM industry_insights) "
            f"ORDER BY s.score DESC, t.created_at DESC LIMIT ?",
            (min_score, *sources, limit),
        ).fetchall()
        return [{"id": r[0], "text": r[1], "source": r[2],
                 "author": r[3], "created_at": r[4]} for r in rows]

    def recent_insights(self, chain: str = "", limit: int = 20) -> list:
        """返回最近的产业链洞察，可按 chain 过滤。"""
        if chain:
            rows = self.conn.execute(
                "SELECT chain, companies, relationships, events, source, confidence, extracted_at "
                "FROM industry_insights WHERE chain=? ORDER BY extracted_at DESC LIMIT ?",
                (chain, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT chain, companies, relationships, events, source, confidence, extracted_at "
                "FROM industry_insights ORDER BY extracted_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {"chain": r[0], "companies": json.loads(r[1] or "[]"),
             "relationships": json.loads(r[2] or "[]"),
             "events": json.loads(r[3] or "[]"),
             "source": r[4], "confidence": r[5], "extracted_at": r[6]}
            for r in rows
        ]

    # ---- 企查查企业与人员 ----

    def save_company(self, company: dict) -> None:
        """保存企业工商信息，以统一社会信用代码去重更新。"""
        self.conn.execute(
            "INSERT OR REPLACE INTO companies "
            "(credit_code, name, legal_rep, reg_capital, established, status, "
            "industry, address, phone, email, scope, raw_json, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (company["credit_code"], company.get("name", ""),
             company.get("legal_rep", ""), company.get("reg_capital", ""),
             company.get("established", ""), company.get("status", ""),
             company.get("industry", ""), company.get("address", ""),
             company.get("phone", ""), company.get("email", ""),
             company.get("scope", ""), company.get("raw_json", ""),
             dt.datetime.utcnow().isoformat()),
        )
        self.conn.commit()

    def save_company_person(self, credit_code: str, name: str, role: str,
                             title: str = "", share_ratio: str = "",
                             invest_amount: str = "") -> None:
        """保存企业人员，以 credit_code+name+role 去重。"""
        import hashlib
        pid = hashlib.md5(f"{credit_code}|{name}|{role}".encode()).hexdigest()
        self.conn.execute(
            "INSERT OR REPLACE INTO company_persons "
            "(id, credit_code, name, role, title, share_ratio, invest_amount, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (pid, credit_code, name, role, title, share_ratio, invest_amount,
             dt.datetime.utcnow().isoformat()),
        )
        self.conn.commit()

    def company_persons(self, credit_code: str) -> list:
        """返回某企业所有人员信息。"""
        return self.conn.execute(
            "SELECT name, role, title, share_ratio, invest_amount "
            "FROM company_persons WHERE credit_code=? ORDER BY role, name",
            (credit_code,),
        ).fetchall()

    def recent_supplier_updates(self, customer_name: str, limit: int = 20) -> list:
        """返回某核心公司的供应商动态。"""
        return self.conn.execute(
            "SELECT supplier_name, event_type, title, content, source, published_at, url "
            "FROM supplier_updates WHERE customer_name=? "
            "ORDER BY published_at DESC LIMIT ?",
            (customer_name, limit),
        ).fetchall()
