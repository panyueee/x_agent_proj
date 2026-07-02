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

CREATE TABLE IF NOT EXISTS dragon_tiger(
  id           TEXT PRIMARY KEY,   -- hash(date+code+reason)
  date         TEXT,               -- 上榜日期 YYYY-MM-DD
  code         TEXT,               -- 股票代码
  name         TEXT,               -- 股票名称
  reason       TEXT,               -- 上榜原因
  buy_amt      REAL DEFAULT 0.0,   -- 买入额（元）
  sell_amt     REAL DEFAULT 0.0,   -- 卖出额（元）
  net_amt      REAL DEFAULT 0.0,   -- 净买入额（元）
  fetched_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_dragon_tiger_date ON dragon_tiger(date);
CREATE INDEX IF NOT EXISTS idx_dragon_tiger_code ON dragon_tiger(code);

CREATE TABLE IF NOT EXISTS north_flow(
  date         TEXT PRIMARY KEY,   -- 日期 YYYY-MM-DD
  net_flow_b   REAL DEFAULT 0.0,   -- 北向资金净流入（亿元）
  fetched_at   TEXT
);

CREATE TABLE IF NOT EXISTS concept_mappings(
  concept      TEXT PRIMARY KEY,   -- 概念板块名称（东方财富）
  gics         TEXT,               -- 映射到的 GICS 大类
  source       TEXT DEFAULT 'auto', -- seed / auto / llm / manual
  confirmed    INTEGER DEFAULT 0,  -- 1=人工确认，0=待确认
  updated_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_concept_gics ON concept_mappings(gics);

CREATE TABLE IF NOT EXISTS portfolio_weights(
  id           TEXT PRIMARY KEY,   -- hash(computed_at)
  computed_at  TEXT,
  method       TEXT,               -- black_litterman / max_sharpe / equal_weight
  weights      TEXT,               -- JSON {symbol: weight}
  views        TEXT DEFAULT '{}'   -- JSON {symbol: view_return}
);
CREATE INDEX IF NOT EXISTS idx_portfolio_computed ON portfolio_weights(computed_at);

CREATE TABLE IF NOT EXISTS panic_snapshots(
  id             TEXT PRIMARY KEY,   -- hash(computed_at)
  computed_at    TEXT,               -- ISO8601
  lookback_hours INTEGER,
  panic_score    REAL,               -- 0–100
  fear_count     INTEGER DEFAULT 0,
  greed_count    INTEGER DEFAULT 0,
  total_posts    INTEGER DEFAULT 0,
  dominant_emotion  TEXT,            -- panic / greed / neutral
  contrarian_signal TEXT,            -- buy / sell / neutral
  llm_report     TEXT DEFAULT ''     -- JSON 字符串（LLM 解读）
);
CREATE INDEX IF NOT EXISTS idx_panic_computed ON panic_snapshots(computed_at);

CREATE TABLE IF NOT EXISTS quarterly_financials(
  symbol        TEXT,
  report_date   TEXT,               -- YYYY-MM-DD（季报/年报日期）
  total_revenue REAL,               -- 营业总收入（元）
  per_share_cf  REAL,               -- 每股经营性现金流（元/股）
  fetched_at    TEXT,
  PRIMARY KEY (symbol, report_date)
);
CREATE INDEX IF NOT EXISTS idx_qf_symbol ON quarterly_financials(symbol);

CREATE TABLE IF NOT EXISTS fundamentals(
  symbol      TEXT,
  date        TEXT,               -- YYYY-MM-DD
  market_cap  REAL,               -- 总市值（元）
  float_cap   REAL,               -- 流通市值（元）
  pb          REAL,               -- 市净率
  book_price  REAL,               -- 1/PB（账价比）
  pe_ttm      REAL,               -- 市盈率-动态
  fetched_at  TEXT,
  PRIMARY KEY (symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_fund_symbol ON fundamentals(symbol);
CREATE INDEX IF NOT EXISTS idx_fund_date   ON fundamentals(date);

CREATE TABLE IF NOT EXISTS wencai_picks(
  id          TEXT PRIMARY KEY,   -- hash(date|query|code)：同日同查询同股票只存一次
  date        TEXT,               -- 选股日期 YYYY-MM-DD
  query       TEXT,               -- 问财自然语言查询原文
  label       TEXT DEFAULT '',    -- config 里给查询起的标签
  code        TEXT,               -- 股票代码（不带交易所后缀，如 600519）
  full_code   TEXT DEFAULT '',    -- 带后缀代码（600519.SH）
  name        TEXT,               -- 股票简称
  price       REAL,               -- 最新价
  change_pct  REAL,               -- 最新涨跌幅（%）
  metrics     TEXT DEFAULT '{}',  -- 其余指标列 JSON（列名已去日期后缀）
  fetched_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_wencai_date  ON wencai_picks(date);
CREATE INDEX IF NOT EXISTS idx_wencai_code  ON wencai_picks(code);
CREATE INDEX IF NOT EXISTS idx_wencai_query ON wencai_picks(query);

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

-- ── 证券主表 / 组合 / 持仓 / 风险快照（个人版 Aladdin 批次一，见 docs/aladdin/05）──

CREATE TABLE IF NOT EXISTS securities(
  symbol       TEXT PRIMARY KEY,   -- 统一主键，与 parquet 文件名对齐：sh.600519 / NVDA / BTC-USD
  market       TEXT,               -- a / us / hk / crypto / etf / index / cb / fx / futures / bond
  name         TEXT DEFAULT '',
  sector_gics  TEXT DEFAULT '',    -- 迁移自 sw_sector_cache
  aliases      TEXT DEFAULT '[]',  -- JSON: ["600519", "$MOUTAI", "贵州茅台"] 供信号 ticker 解析
  has_parquet  INTEGER DEFAULT 0,
  updated_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_sec_market ON securities(market);

CREATE TABLE IF NOT EXISTS portfolios(
  portfolio_id TEXT PRIMARY KEY,   -- 'real' / 'demo' / 'paper_signals' / 'paper_optimizer'
  name         TEXT, base_ccy TEXT DEFAULT 'CNY', created_at TEXT
);

CREATE TABLE IF NOT EXISTS positions(
  portfolio_id TEXT, date TEXT, symbol TEXT,
  quantity REAL, cost_price REAL, weight REAL,
  source TEXT DEFAULT 'manual',    -- manual / signal / optimizer
  note   TEXT DEFAULT '',
  PRIMARY KEY(portfolio_id, date, symbol)
);
CREATE INDEX IF NOT EXISTS idx_pos_latest ON positions(portfolio_id, date);

CREATE TABLE IF NOT EXISTS risk_snapshots(
  portfolio_id  TEXT,
  date          TEXT,               -- 快照日 YYYY-MM-DD
  vol_ann       REAL,
  var99_1d      REAL,
  te_ann        REAL,
  factor_vol    REAL, specific_vol REAL,
  exposures     TEXT,               -- JSON {factor: beta}
  risk_contrib  TEXT,               -- JSON {factor: ccr_pct}
  stock_contrib TEXT,               -- JSON [{symbol, name, pct}] top10
  method        TEXT DEFAULT 'ewma_factor_v1',
  computed_at   TEXT,
  PRIMARY KEY(portfolio_id, date)
);
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

    # ---- 龙虎榜 ----

    def save_dragon_tiger(self, record):
        # type: (dict) -> None
        """保存龙虎榜单条记录，以 date+code+reason 的 hash 去重。"""
        import hashlib
        key = "{}|{}|{}".format(
            record.get("date", ""),
            record.get("code", ""),
            record.get("reason", ""),
        )
        rid = hashlib.md5(key.encode()).hexdigest()
        self.conn.execute(
            "INSERT OR IGNORE INTO dragon_tiger "
            "(id, date, code, name, reason, buy_amt, sell_amt, net_amt, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                rid,
                record.get("date", ""),
                record.get("code", ""),
                record.get("name", ""),
                record.get("reason", ""),
                record.get("buy_amt", 0.0),
                record.get("sell_amt", 0.0),
                record.get("net_amt", 0.0),
                dt.datetime.utcnow().isoformat(),
            ),
        )
        self.conn.commit()

    def recent_dragon_tiger(self, limit=50):
        # type: (int) -> list
        """返回最近的龙虎榜记录，按日期倒序。"""
        return self.conn.execute(
            "SELECT date, code, name, reason, buy_amt, sell_amt, net_amt "
            "FROM dragon_tiger ORDER BY date DESC LIMIT ?",
            (limit,),
        ).fetchall()

    # ---- 北向资金 ----

    def save_north_flow(self, date, net_flow_b):
        # type: (str, float) -> None
        """保存北向资金净流入数据，以日期为主键（当日覆盖写入）。"""
        self.conn.execute(
            "INSERT OR REPLACE INTO north_flow (date, net_flow_b, fetched_at) VALUES (?,?,?)",
            (date, net_flow_b, dt.datetime.utcnow().isoformat()),
        )
        self.conn.commit()

    def latest_north_flow(self):
        # type: () -> Optional[dict]
        """返回最新一条北向资金数据，字典格式；无数据时返回 None。"""
        row = self.conn.execute(
            "SELECT date, net_flow_b FROM north_flow ORDER BY date DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return {"date": row[0], "net_flow_b": row[1]}

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

    # ── 概念板块映射 ──

    def save_concept_mapping(self, concept: str, gics: str,
                              source: str = "auto", confirmed: int = 0) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO concept_mappings "
            "(concept, gics, source, confirmed, updated_at) VALUES (?,?,?,?,?)",
            (concept, gics, source, confirmed, dt.datetime.utcnow().isoformat()),
        )
        self.conn.commit()

    def load_concept_mappings(self) -> dict[str, str]:
        """返回 {concept: gics}，包含所有已入库的映射。"""
        rows = self.conn.execute(
            "SELECT concept, gics FROM concept_mappings"
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    def unconfirmed_concepts(self) -> list[dict]:
        """返回待人工确认的概念列表。"""
        rows = self.conn.execute(
            "SELECT concept, gics, source, updated_at FROM concept_mappings "
            "WHERE confirmed=0 ORDER BY updated_at DESC"
        ).fetchall()
        return [{"concept": r[0], "gics": r[1], "source": r[2], "updated_at": r[3]}
                for r in rows]

    def confirm_concept(self, concept: str, gics: str) -> None:
        """人工确认并更正某个概念的映射。"""
        self.conn.execute(
            "INSERT OR REPLACE INTO concept_mappings "
            "(concept, gics, source, confirmed, updated_at) VALUES (?,?,'manual',1,?)",
            (concept, gics, dt.datetime.utcnow().isoformat()),
        )
        self.conn.commit()

    # ── 组合权重 ──

    def save_portfolio_weights(self, result: dict) -> None:
        import hashlib
        now = dt.datetime.utcnow().isoformat()
        pid = hashlib.md5(now.encode()).hexdigest()
        self.conn.execute(
            "INSERT OR IGNORE INTO portfolio_weights (id, computed_at, method, weights, views) VALUES (?,?,?,?,?)",
            (pid, now, result.get("method", ""),
             json.dumps(result.get("weights", {}), ensure_ascii=False),
             json.dumps(result.get("views", {}),   ensure_ascii=False)),
        )
        self.conn.commit()

    def latest_portfolio_weights(self) -> dict:
        row = self.conn.execute(
            "SELECT computed_at, method, weights, views FROM portfolio_weights ORDER BY computed_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return {}
        return {
            "computed_at": row[0], "method": row[1],
            "weights": json.loads(row[2] or "{}"),
            "views":   json.loads(row[3] or "{}"),
        }

    # ── Panic Index 快照 ──

    def save_panic_snapshot(self, data: dict) -> None:
        """保存 Panic Index 快照，以 computed_at 的 hash 去重。"""
        import hashlib
        pid = hashlib.md5(data["computed_at"].encode()).hexdigest()
        self.conn.execute(
            "INSERT OR IGNORE INTO panic_snapshots "
            "(id, computed_at, lookback_hours, panic_score, fear_count, greed_count, "
            "total_posts, dominant_emotion, contrarian_signal, llm_report) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                pid,
                data["computed_at"],
                data.get("lookback_hours", 24),
                data.get("panic_score", 50.0),
                data.get("fear_count", 0),
                data.get("greed_count", 0),
                data.get("total_posts", 0),
                data.get("dominant_emotion", "neutral"),
                data.get("contrarian_signal", "neutral"),
                json.dumps(data.get("llm_report") or {}, ensure_ascii=False),
            ),
        )
        self.conn.commit()

    def recent_panic_snapshots(self, limit: int = 10) -> list:
        """返回最近 N 条 Panic Index 快照，按时间倒序。"""
        rows = self.conn.execute(
            "SELECT computed_at, panic_score, fear_count, greed_count, total_posts, "
            "dominant_emotion, contrarian_signal, llm_report "
            "FROM panic_snapshots ORDER BY computed_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        results = []
        for r in rows:
            results.append({
                "computed_at":       r[0],
                "panic_score":       r[1],
                "fear_count":        r[2],
                "greed_count":       r[3],
                "total_posts":       r[4],
                "dominant_emotion":  r[5],
                "contrarian_signal": r[6],
                "llm_report":        json.loads(r[7] or "{}"),
            })
        return results

    # ── quarterly_financials ──────────────────────────────────────────────────

    def save_quarterly_financials(self, records: list) -> int:
        """批量写入季报数据，返回写入行数。"""
        now = dt.datetime.utcnow().isoformat()
        rows = [
            (r["symbol"], r["report_date"],
             r.get("total_revenue"), r.get("per_share_cf"),
             r.get("fetched_at", now))
            for r in records
        ]
        self.conn.executemany(
            "INSERT OR REPLACE INTO quarterly_financials "
            "(symbol, report_date, total_revenue, per_share_cf, fetched_at) "
            "VALUES (?,?,?,?,?)",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def latest_quarterly_date(self, symbol: str) -> str | None:
        """返回某品种最新季报日期，无数据时返回 None。"""
        row = self.conn.execute(
            "SELECT MAX(report_date) FROM quarterly_financials WHERE symbol=?",
            (symbol,),
        ).fetchone()
        return row[0] if row else None

    # ── fundamentals ──────────────────────────────────────────────────────────

    def save_fundamentals_batch(self, records: list) -> int:
        """批量写入基本面快照，返回写入行数。records 为 dict 列表。"""
        now = dt.datetime.utcnow().isoformat()
        rows = [
            (
                r["symbol"], r["date"],
                r.get("market_cap"), r.get("float_cap"),
                r.get("pb"), r.get("book_price"),
                r.get("pe_ttm"), now,
            )
            for r in records
        ]
        self.conn.executemany(
            "INSERT OR REPLACE INTO fundamentals "
            "(symbol, date, market_cap, float_cap, pb, book_price, pe_ttm, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def latest_fundamentals_date(self) -> str | None:
        """返回 fundamentals 表中最新的日期（YYYY-MM-DD），表为空时返回 None。"""
        row = self.conn.execute(
            "SELECT MAX(date) FROM fundamentals"
        ).fetchone()
        return row[0] if row else None

    def save_factor_returns(self, factor_ret_df) -> None:
        """将 toraniko 因子收益率 Polars DataFrame 存入 factor_returns 表。"""
        import json as _json
        rows = factor_ret_df.to_dicts()
        if not rows:
            return
        factor_cols = [c for c in factor_ret_df.columns if c != "date"]
        # 建表（动态列）
        col_defs = ", ".join(f'"{c}" REAL' for c in factor_cols)
        self.conn.execute(f"""
            CREATE TABLE IF NOT EXISTS factor_returns (
                date TEXT PRIMARY KEY,
                {col_defs}
            )
        """)
        placeholders = ", ".join("?" * (1 + len(factor_cols)))
        # SQL 与列名拼接在循环外只算一次，整批用 executemany 写入
        col_list = ", ".join(f'"{c}"' for c in factor_cols)
        sql = (
            f"INSERT OR REPLACE INTO factor_returns (date, {col_list}) "
            f"VALUES ({placeholders})"
        )
        params = [
            [str(row["date"])] + [row.get(c) for c in factor_cols]
            for row in rows
        ]
        self.conn.executemany(sql, params)
        self.conn.commit()

    # ── 问财选股 ──

    def save_wencai_pick(self, rec: dict) -> bool:
        """保存一条问财选股记录，以 date|query|code 的 hash 去重。
        返回 True 表示新写入，False 表示当日同查询同股票已存在。
        """
        import hashlib
        key = f"{rec.get('date', '')}|{rec.get('query', '')}|{rec.get('code', '')}"
        rid = hashlib.md5(key.encode()).hexdigest()
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO wencai_picks "
            "(id, date, query, label, code, full_code, name, price, change_pct, metrics, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                rid,
                rec.get("date", ""),
                rec.get("query", ""),
                rec.get("label", ""),
                rec.get("code", ""),
                rec.get("full_code", ""),
                rec.get("name", ""),
                rec.get("price"),
                rec.get("change_pct"),
                json.dumps(rec.get("metrics") or {}, ensure_ascii=False),
                rec.get("fetched_at", dt.datetime.utcnow().isoformat()),
            ),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def recent_wencai_picks(self, limit: int = 200) -> list:
        """返回最近的问财选股记录，按日期倒序。

        返回元素：(date, query, label, code, name, price, change_pct)
        """
        return self.conn.execute(
            "SELECT date, query, label, code, name, price, change_pct "
            "FROM wencai_picks ORDER BY date DESC, query, code LIMIT ?",
            (limit,),
        ).fetchall()

    def recent_supplier_updates(self, customer_name: str, limit: int = 20) -> list:
        """返回某核心公司的供应商动态。"""
        return self.conn.execute(
            "SELECT supplier_name, event_type, title, content, source, published_at, url "
            "FROM supplier_updates WHERE customer_name=? "
            "ORDER BY published_at DESC LIMIT ?",
            (customer_name, limit),
        ).fetchall()

    # ── 证券主表 / 组合 / 持仓（个人版 Aladdin 批次一）──

    def upsert_securities(self, records: list) -> int:
        """批量写入证券主表（INSERT OR REPLACE），返回写入行数。

        records 元素为 dict：symbol/market 必填，name/sector_gics/aliases/has_parquet 可选。
        """
        now = dt.datetime.utcnow().isoformat()
        rows = [
            (
                r["symbol"], r["market"],
                r.get("name", ""), r.get("sector_gics", ""),
                json.dumps(r.get("aliases", []), ensure_ascii=False),
                int(r.get("has_parquet", 0)), now,
            )
            for r in records
        ]
        self.conn.executemany(
            "INSERT OR REPLACE INTO securities "
            "(symbol, market, name, sector_gics, aliases, has_parquet, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def get_security(self, symbol: str) -> dict | None:
        row = self.conn.execute(
            "SELECT symbol, market, name, sector_gics, aliases, has_parquet "
            "FROM securities WHERE symbol=?", (symbol,)
        ).fetchone()
        if row is None:
            return None
        return {
            "symbol": row[0], "market": row[1], "name": row[2],
            "sector_gics": row[3], "aliases": json.loads(row[4] or "[]"),
            "has_parquet": row[5],
        }

    def upsert_portfolio(self, portfolio_id: str, name: str = "",
                         base_ccy: str = "CNY") -> None:
        """新建组合；已存在时保持 created_at 不变，仅更新名称/币种。"""
        self.conn.execute(
            "INSERT INTO portfolios (portfolio_id, name, base_ccy, created_at) "
            "VALUES (?,?,?,?) "
            "ON CONFLICT(portfolio_id) DO UPDATE SET name=excluded.name, base_ccy=excluded.base_ccy",
            (portfolio_id, name or portfolio_id, base_ccy,
             dt.datetime.utcnow().isoformat()),
        )
        self.conn.commit()

    def save_positions_snapshot(self, portfolio_id: str, date: str,
                                rows: list, source: str = "manual") -> int:
        """写入某组合某日的完整持仓快照（不可变账本：每次变更写新 date）。

        rows 元素为 dict：symbol 必填，quantity/cost_price/weight/note 可选。
        同 (portfolio_id, date, symbol) 覆盖写。
        """
        params = [
            (
                portfolio_id, date, r["symbol"],
                r.get("quantity"), r.get("cost_price"), r.get("weight"),
                r.get("source", source), r.get("note", ""),
            )
            for r in rows
        ]
        self.conn.executemany(
            "INSERT OR REPLACE INTO positions "
            "(portfolio_id, date, symbol, quantity, cost_price, weight, source, note) "
            "VALUES (?,?,?,?,?,?,?,?)",
            params,
        )
        self.conn.commit()
        return len(params)

    def latest_positions(self, portfolio_id: str, asof: str | None = None) -> list:
        """返回组合最近一次快照（date<=asof；asof 为空取最新），dict 列表。"""
        if asof:
            row = self.conn.execute(
                "SELECT MAX(date) FROM positions WHERE portfolio_id=? AND date<=?",
                (portfolio_id, asof),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT MAX(date) FROM positions WHERE portfolio_id=?",
                (portfolio_id,),
            ).fetchone()
        snap_date = row[0] if row else None
        if not snap_date:
            return []
        rows = self.conn.execute(
            "SELECT symbol, quantity, cost_price, weight, source, note, date "
            "FROM positions WHERE portfolio_id=? AND date=? ORDER BY symbol",
            (portfolio_id, snap_date),
        ).fetchall()
        return [
            {"symbol": r[0], "quantity": r[1], "cost_price": r[2],
             "weight": r[3], "source": r[4], "note": r[5], "date": r[6]}
            for r in rows
        ]

    # ── 风险快照 ──

    def save_risk_snapshot(self, snap: dict) -> None:
        """保存一天一组合的风险快照（同键覆盖写）。"""
        self.conn.execute(
            "INSERT OR REPLACE INTO risk_snapshots "
            "(portfolio_id, date, vol_ann, var99_1d, te_ann, factor_vol, specific_vol, "
            "exposures, risk_contrib, stock_contrib, method, computed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                snap["portfolio_id"], snap["date"],
                snap.get("vol_ann"), snap.get("var99_1d"), snap.get("te_ann"),
                snap.get("factor_vol"), snap.get("specific_vol"),
                json.dumps(snap.get("exposures") or {}, ensure_ascii=False),
                json.dumps(snap.get("risk_contrib") or {}, ensure_ascii=False),
                json.dumps(snap.get("stock_contrib") or [], ensure_ascii=False),
                snap.get("method", "ewma_factor_v1"),
                dt.datetime.utcnow().isoformat(),
            ),
        )
        self.conn.commit()

    def latest_risk_snapshot(self, portfolio_id: str | None = None) -> dict | None:
        """返回最新风险快照；portfolio_id 为空时跨组合取最新一条。"""
        if portfolio_id:
            row = self.conn.execute(
                "SELECT portfolio_id, date, vol_ann, var99_1d, te_ann, factor_vol, "
                "specific_vol, exposures, risk_contrib, stock_contrib, method "
                "FROM risk_snapshots WHERE portfolio_id=? ORDER BY date DESC LIMIT 1",
                (portfolio_id,),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT portfolio_id, date, vol_ann, var99_1d, te_ann, factor_vol, "
                "specific_vol, exposures, risk_contrib, stock_contrib, method "
                "FROM risk_snapshots ORDER BY date DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return {
            "portfolio_id": row[0], "date": row[1], "vol_ann": row[2],
            "var99_1d": row[3], "te_ann": row[4], "factor_vol": row[5],
            "specific_vol": row[6],
            "exposures": json.loads(row[7] or "{}"),
            "risk_contrib": json.loads(row[8] or "{}"),
            "stock_contrib": json.loads(row[9] or "[]"),
            "method": row[10],
        }
