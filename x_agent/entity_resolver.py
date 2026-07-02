"""标的实体解析器 —— 把散在各来源的标的提及挂到统一证券ID(securities.symbol)。

设计原则：**宁可漏不可错**。只往 securities 主表里已存在的 symbol 上解析，
绝不凭规则「凭空造」不在主表里的 symbol（例如 A股前缀推断已被 aliases 覆盖，
无需再造）。多义/低置信只标注，不硬挂。

核心能力：
- resolve(mention)          —— 单个提及 → Resolution(security_id, confidence, method)
- resolve_text(text, src)   —— 一段文本 → 多个 Resolution（cashtag / A股代码 / 中文名）
- build_security_mentions() —— 扫已有各表，落 security_mentions 反向索引
- get_security_view(sid)    —— 某标的在所有来源的跨来源汇总（含 parquet 价格快照）

只依赖标准库 + securities 主表；不触碰 rag.db。
"""
from __future__ import annotations

import os
import re
import json
import sqlite3
import datetime as dt
from dataclasses import dataclass, field
from typing import Optional

# 复用 classifier 里已编译好的正则，保持口径一致
try:
    from .classifier import TICKER_RE, ASHARE_RE
except Exception:  # 允许当脚本直接跑
    TICKER_RE = re.compile(r"\$[A-Za-z]{2,6}\b")   # $BTC $ETH $NVDA ...
    ASHARE_RE = re.compile(r'\b([036]\d{5})\b')     # 600519 / 000001 / 300750

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(_ROOT, "output", "x_agent.db")
PARQUET_ROOT = os.path.join(_ROOT, "data", "stock_history")

_CJK_RE = re.compile(r'[一-鿿]')
_ASHARE_FULL = re.compile(r'^[036]\d{5}$')

# 置信度分档
CONF_SYMBOL   = 1.0    # 直接命中主键
CONF_CODE     = 0.95   # A股裸代码（数字，唯一）
CONF_CASHTAG  = 0.92   # $XXX 命中 crypto/etf 主表里「已声明」的 $ 别名（权威映射）
CONF_CASHTAG_US = 0.75 # $XXX 去 $ 后猜成美股 ticker：纯推断，非主表声明。
                       # 同名 crypto 代币（$FET/$SUI/$ARB…不在我们 20 币主表里）会误挂到冷门美股，
                       # 故压到 0.9 高置信线以下 —— 宁可漏不可错。
CONF_ALIAS    = 0.9    # 精确别名/中文名唯一命中
CONF_AMBIG    = 0.3    # 多候选，标注不硬挂（不写入反向索引）
CONF_FUZZY    = 0.4    # 中文名子串（自由文本），低置信


@dataclass
class Resolution:
    security_id: str
    confidence: float
    method: str                       # symbol / cashtag / ashare_code / alias / name / *_ambiguous / name_fuzzy
    mention: str = ""                 # 原始命中串
    candidates: list[str] = field(default_factory=list)  # 歧义时的候选集

    @property
    def high_conf(self) -> bool:
        return self.confidence >= 0.9


class EntityResolver:
    """在内存里建 别名→symbol 索引，提供解析。构造一次可复用。"""

    def __init__(self, db_path: str = DEFAULT_DB, conn: Optional[sqlite3.Connection] = None):
        self.db_path = db_path
        self._own_conn = conn is None
        self.conn = conn or _connect(db_path)
        # symbol 全集（大小写敏感 + lower 副本）
        self._symbols: set[str] = set()
        self._symbols_lower: dict[str, str] = {}      # lower -> canonical symbol
        # 别名/名 索引：lower key -> set(symbol)
        self._alias_idx: dict[str, set[str]] = {}
        # 元数据：symbol -> (market, name, sector)
        self._meta: dict[str, tuple[str, str, str]] = {}
        # 中文名子串扫描用：[(name_lower, symbol)]，name 长度>=4 且唯一
        self._cjk_names: list[tuple[str, str]] = []
        self._load()

    # ---------- 索引构建 ----------
    def _load(self) -> None:
        rows = self.conn.execute(
            "SELECT symbol, market, name, sector_gics, aliases FROM securities"
        ).fetchall()
        name_owner: dict[str, set[str]] = {}
        for symbol, market, name, sector, aliases in rows:
            if not symbol:
                continue
            self._symbols.add(symbol)
            self._symbols_lower[symbol.lower()] = symbol
            self._meta[symbol] = (market or "", name or "", sector or "")
            keys: set[str] = set()
            if name:
                keys.add(name.strip().lower())
                name_owner.setdefault(name.strip(), set()).add(symbol)
            try:
                for a in (json.loads(aliases) if aliases else []):
                    if a and str(a).strip():
                        keys.add(str(a).strip().lower())
            except Exception:
                pass
            for k in keys:
                self._alias_idx.setdefault(k, set()).add(symbol)
        # 中文名子串扫描候选：长度>=4、唯一归属、含 CJK
        for nm, owners in name_owner.items():
            if len(owners) == 1 and len(nm) >= 4 and _CJK_RE.search(nm):
                self._cjk_names.append((nm, next(iter(owners))))

    # ---------- 单提及解析 ----------
    def resolve(self, mention: str) -> Optional[Resolution]:
        if mention is None:
            return None
        m = str(mention).strip()
        if not m:
            return None

        # 1) 直接命中主键（区分大小写优先，再不敏感）
        if m in self._symbols:
            return Resolution(m, CONF_SYMBOL, "symbol", m)
        low = m.lower()
        if low in self._symbols_lower:
            return Resolution(self._symbols_lower[low], CONF_SYMBOL, "symbol", m)

        # 2) cashtag  $XXX
        if m.startswith("$"):
            # 2a) crypto/etf 把 $ 存进了别名：$BTC -> BTC-USD
            if low in self._alias_idx:
                return self._from_index(low, m, CONF_CASHTAG, "cashtag")
            # 2b) 去 $ 后当美股 ticker：$NVDA -> NVDA（推断，中置信）
            core = m[1:]
            if core in self._symbols:
                return Resolution(core, CONF_CASHTAG_US, "cashtag_us", m)
            if core.lower() in self._symbols_lower:
                return Resolution(self._symbols_lower[core.lower()], CONF_CASHTAG_US, "cashtag_us", m)
            return None

        # 3) A股裸代码（6位数字，别名里已含 -> 唯一映射到 sh./sz.）
        if _ASHARE_FULL.match(m):
            if low in self._alias_idx:
                return self._from_index(low, m, CONF_CODE, "ashare_code")
            return None

        # 4) 精确别名 / 中文名
        if low in self._alias_idx:
            return self._from_index(low, m, CONF_ALIAS, "alias")

        return None

    def _from_index(self, key: str, mention: str, conf: float, method: str) -> Resolution:
        syms = sorted(self._alias_idx[key])
        if len(syms) == 1:
            return Resolution(syms[0], conf, method, mention)
        # 歧义：不硬挂，取第一个占位但标低置信 + 候选集
        return Resolution(syms[0], CONF_AMBIG, method + "_ambiguous", mention, candidates=syms)

    # ---------- 文本解析（抽多个提及） ----------
    def resolve_text(self, text: str, source: str = "") -> list[Resolution]:
        if not text:
            return []
        out: dict[str, Resolution] = {}   # security_id -> 最高置信的 Resolution

        def _add(res: Optional[Resolution]):
            if not res:
                return
            prev = out.get(res.security_id)
            if prev is None or res.confidence > prev.confidence:
                out[res.security_id] = res

        has_cjk = bool(_CJK_RE.search(text))

        # cashtag（含 $，全语境适用）
        for tag in TICKER_RE.findall(text):
            _add(self.resolve(tag))

        # A股裸代码：仅在含中文的文本里认（英文/crypto 推文里的 6 位数字多是噪声）
        if has_cjk:
            for code in ASHARE_RE.findall(text):
                _add(self.resolve(code))

        # 中文名子串：仅淘股吧/小红书这类中文源，长度>=4，低置信不硬挂
        if source in ("taoguba", "xiaohongshu") and has_cjk:
            for nm, sym in self._cjk_names:
                if nm in text:
                    _add(Resolution(sym, CONF_FUZZY, "name_fuzzy", nm))

        return list(out.values())

    def meta(self, symbol: str) -> tuple[str, str, str]:
        return self._meta.get(symbol, ("", "", ""))

    def close(self):
        if self._own_conn:
            self.conn.close()


# ============================================================
# 反向索引 security_mentions
# ============================================================
_MENTIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS security_mentions(
  security_id TEXT NOT NULL,        -- 对齐 securities.symbol
  source_type TEXT NOT NULL,        -- tweet / research / policy / ...
  source_ref  TEXT NOT NULL,        -- 源表主键（tweet.id / report_id / policy.id）
  mention     TEXT DEFAULT '',      -- 原始命中串
  method      TEXT DEFAULT '',      -- 命中方式（provenance）
  confidence  REAL DEFAULT 0,
  date        TEXT DEFAULT '',      -- 事件日期（created_at / published_at）
  created_at  TEXT DEFAULT '',
  PRIMARY KEY(security_id, source_type, source_ref, mention)
);
CREATE INDEX IF NOT EXISTS idx_secment_sid ON security_mentions(security_id);
CREATE INDEX IF NOT EXISTS idx_secment_src ON security_mentions(source_type);
CREATE INDEX IF NOT EXISTS idx_secment_date ON security_mentions(date);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_MENTIONS_SCHEMA)
    conn.commit()


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    # 与 storage.Store 一致：并发写容忍锁等待
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def build_security_mentions(db_path: str = DEFAULT_DB, rebuild: bool = True) -> dict:
    """扫已有各表，把能解析出的标的提及落进 security_mentions。幂等：rebuild=True 先清空。

    返回统计 dict 供 CLI/报告使用。
    """
    conn = _connect(db_path)
    ensure_schema(conn)
    resolver = EntityResolver(db_path, conn=conn)
    now = dt.datetime.now().isoformat(timespec="seconds")

    if rebuild:
        conn.execute("DELETE FROM security_mentions")
        conn.commit()

    stats = {
        "by_source": {},           # source_type -> 行数
        "by_method": {},           # method -> 行数
        "high_conf": 0,
        "low_conf": 0,
        "distinct_securities": set(),
        "scanned": {},             # source_type -> 扫描的源记录数
        "resolvable": {},          # source_type -> 有>=1命中的源记录数
    }
    rows: list[tuple] = []

    def _emit(sid, stype, ref, mention, method, conf, date):
        # 歧义匹配（多候选）不硬挂进反向索引 —— 宁可漏不可错，候选仅在 resolve() 里返回
        if method.endswith("_ambiguous"):
            return
        rows.append((sid, stype, str(ref), mention or "", method, float(conf), date or "", now))
        stats["by_source"][stype] = stats["by_source"].get(stype, 0) + 1
        stats["by_method"][method] = stats["by_method"].get(method, 0) + 1
        if conf >= 0.9:
            stats["high_conf"] += 1
        else:
            stats["low_conf"] += 1
        stats["distinct_securities"].add(sid)

    # ---- tweets（把 signals.tickers 并入同一 tweet，避免与 tweet 文本重复计数） ----
    sig_map: dict[str, list[str]] = {}
    for tid, tickers in conn.execute(
        "SELECT tweet_id, tickers FROM signals WHERE tickers IS NOT NULL AND tickers NOT IN ('','[]')"
    ):
        try:
            sig_map[tid] = [t for t in json.loads(tickers) if t]
        except Exception:
            pass

    tw = conn.execute("SELECT id, text, created_at, source FROM tweets").fetchall()
    stats["scanned"]["tweet"] = len(tw)
    n_res = 0
    for tid, text, created_at, source in tw:
        merged: dict[str, Resolution] = {}

        def _merge(res: Optional[Resolution], override_method=None):
            if not res:
                return
            r = res
            if override_method:
                r = Resolution(res.security_id, res.confidence, override_method, res.mention, res.candidates)
            prev = merged.get(r.security_id)
            if prev is None or r.confidence > prev.confidence:
                merged[r.security_id] = r

        # signals 抽出的 ticker：来自分类器的结构化抽取，provenance 标 signal_ticker
        # （置信度仍按 resolve() 的判定，与命中方式解耦）
        for tk in sig_map.get(tid, []):
            res = resolver.resolve(tk)
            if res and not res.method.endswith("_ambiguous"):
                _merge(res, override_method="signal_ticker")
        # 文本再补（cashtag/代码/中文名）
        for res in resolver.resolve_text(text or "", source or ""):
            _merge(res)

        if merged:
            n_res += 1
        for res in merged.values():
            _emit(res.security_id, "tweet", tid, res.mention, res.method, res.confidence, created_at)
    stats["resolvable"]["tweet"] = n_res

    # ---- research_reports（stock_code + stock_name；现多为空表，将来自动生效） ----
    try:
        rr = conn.execute(
            "SELECT report_id, stock_code, stock_name, published_at FROM research_reports"
        ).fetchall()
    except sqlite3.OperationalError:
        rr = []
    stats["scanned"]["research"] = len(rr)
    n_res = 0
    for rid, code, name, pub in rr:
        best: Optional[Resolution] = None
        for cand in (code, name):
            res = resolver.resolve(cand) if cand else None
            if res and (best is None or res.confidence > best.confidence):
                best = res
        if best:
            n_res += 1
            _emit(best.security_id, "research", rid, best.mention, best.method, best.confidence, pub)
    stats["resolvable"]["research"] = n_res

    # ---- policy_events（title 里的中文名，低置信标注） ----
    try:
        pe = conn.execute(
            "SELECT id, title, announce_date FROM policy_events"
        ).fetchall()
    except sqlite3.OperationalError:
        pe = []
    stats["scanned"]["policy"] = len(pe)
    n_res = 0
    for pid, title, adate in pe:
        got = resolver.resolve_text(title or "", source="taoguba")  # 借用中文名扫描
        if got:
            n_res += 1
        for res in got:
            _emit(res.security_id, "policy", pid, res.mention, res.method, res.confidence, adate)
    stats["resolvable"]["policy"] = n_res

    # 批量写入
    conn.executemany(
        "INSERT OR IGNORE INTO security_mentions"
        "(security_id, source_type, source_ref, mention, method, confidence, date, created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    stats["total_rows"] = len(rows)
    stats["distinct_securities"] = len(stats["distinct_securities"])
    resolver.close() if resolver._own_conn else None
    conn.close()
    return stats


# ============================================================
# 查询接口
# ============================================================
_MARKET_DIR = {"a": "a", "us": "us", "hk": "hk"}  # 目前仅这三类有 parquet


def _price_snapshot(symbol: str, market: str) -> Optional[dict]:
    """从 parquet 读最近收盘/涨跌。缺文件/依赖时静默跳过。"""
    d = _MARKET_DIR.get(market)
    if not d:
        return None
    path = os.path.join(PARQUET_ROOT, d, f"{symbol}.parquet")
    if not os.path.exists(path):
        return None
    try:
        import pandas as pd
        df = pd.read_parquet(path)
        if df.empty:
            return None
        last = df.iloc[-1]
        cols = {c.lower(): c for c in df.columns}
        def _g(row, name):
            c = cols.get(name)
            return None if c is None else row[c]
        close = _g(last, "close")
        # parquet 无 change_pct 列，用最近两根收盘算日涨跌
        change_pct = None
        prev = _g(last, "change_pct") or _g(last, "pct_chg")
        if prev is not None:
            change_pct = float(prev)
        elif close is not None and len(df) >= 2:
            pc = _g(df.iloc[-2], "close")
            if pc:
                change_pct = round((float(close) - float(pc)) / float(pc) * 100, 3)
        snap = {
            "last_close": float(close) if close is not None else None,
            "date": str(_g(last, "date") or _g(last, "timestamp") or last.name),
            "change_pct": change_pct,
            "rows": int(len(df)),
        }
        return snap
    except Exception:
        return None


def get_security_view(security_id: str, db_path: str = DEFAULT_DB, limit: int = 20) -> dict:
    """某标的在所有来源的跨来源汇总。给 dashboard / dossier / persona 复用。"""
    conn = _connect(db_path)
    row = conn.execute(
        "SELECT symbol, market, name, sector_gics, aliases FROM securities WHERE symbol=?",
        (security_id,),
    ).fetchone()
    view: dict = {
        "security_id": security_id,
        "found": row is not None,
        "market": row[1] if row else None,
        "name": row[2] if row else None,
        "sector_gics": row[3] if row else None,
        "aliases": json.loads(row[4]) if row and row[4] else [],
        "mentions_by_source": {},
        "mention_total": 0,
        "recent_mentions": [],
        "price_snapshot": None,
    }
    if not row:
        conn.close()
        return view

    try:
        for stype, cnt, hi in conn.execute(
            "SELECT source_type, COUNT(*), SUM(confidence>=0.9) "
            "FROM security_mentions WHERE security_id=? GROUP BY source_type",
            (security_id,),
        ):
            view["mentions_by_source"][stype] = {"count": cnt, "high_conf": hi or 0}
            view["mention_total"] += cnt
        view["recent_mentions"] = [
            {"source_type": st, "source_ref": sr, "mention": mm,
             "method": me, "confidence": cf, "date": da}
            for st, sr, mm, me, cf, da in conn.execute(
                "SELECT source_type, source_ref, mention, method, confidence, date "
                "FROM security_mentions WHERE security_id=? "
                "ORDER BY date DESC LIMIT ?",
                (security_id, limit),
            )
        ]
    except sqlite3.OperationalError:
        pass  # security_mentions 尚未建

    view["price_snapshot"] = _price_snapshot(security_id, view["market"] or "")
    conn.close()
    return view
