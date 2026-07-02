# -*- coding: utf-8 -*-
"""信号绩效计算：对每条可映射到标的的信号，算发出后 h 日真实前瞻收益与超额。

设计要点（Aladdin 路线图 3b）：
- **严格防未来函数**：入场 = 信号本地日 *严格之后* 的第一个交易日（searchsorted side='right'），
  收益窗口 close[entry+h]/close[entry]-1 完全落在信号日之后。哪怕信号在盘中发出，也顺延到
  下一交易日入场，宁可保守也绝不用信号当日收盘（避免盘后信号偷看当日/隔夜收盘）。
- **复用 backtest 数据层**：load_market_data / load_benchmark（本包→backtest 是允许方向，
  backtest 内部才禁止 import x_agent）。只加载信号真正引用到的标的，绝不碰全市场 12,899 个 US 文件。
- **数据驱动的 ticker 映射**：cashtag 先按加密（{SYM}-USD 存在）再按美股再按 ETF；6 位码按 A 股。
  不臆造，文件不存在就丢弃。
- 收益窗口不足（临近数据末端、信号太新）→ 该行丢弃，绝不补零/外推。

signal_performance 表（CREATE IF NOT EXISTS，Store 风格，busy_timeout 并发安全，不碰现有表）。
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.data import load_market_data, load_benchmark

# 各市场的基准与本地时区（信号时间戳先转本地再归日，避免错归前一天）
MARKET_BENCH = {"crypto": "BTC-USD", "a": "000300_SS", "us": "GSPC", "etf": "GSPC"}
MARKET_TZ = {"crypto": "UTC", "a": "Asia/Shanghai", "us": "US/Eastern", "etf": "US/Eastern"}

_CASHTAG_RE = re.compile(r"^\$([A-Za-z0-9]+)$")
_ASHARE_RE = re.compile(r"^[036]\d{5}$")


# ---------------------------------------------------------------------------
# ticker 映射（数据驱动，不臆造）
# ---------------------------------------------------------------------------

def map_signal_ticker(ticker: str, data_dir: str | Path = "data") -> tuple[str, str] | None:
    """把 signals.tickers 里的一个 ticker 文本映射到 (market, security_id)。

    - "$BTC" → ("crypto","BTC-USD")（crypto_history 存在时）；
    - 否则 "$NVDA" → ("us","NVDA")（stock_history/us 存在时）；再否则 ETF；
    - 6 位码：6 开头 → ("a","sh.XXXXXX")；0/3 开头 → ("a","sz.XXXXXX")（parquet 存在时）；
    - 任何一步文件不存在或无法识别 → None（调用方丢弃）。

    与 backtest.event_study.map_ticker 的区别：那里所有 cashtag 一律当 crypto；
    这里按本地行情文件是否存在做消歧，能救回大量美股 cashtag（$NVDA/$TSLA 等）。
    """
    if not isinstance(ticker, str):
        return None
    t = ticker.strip()
    d = Path(data_dir)
    m = _CASHTAG_RE.match(t)
    if m:
        sym = m.group(1).upper()
        if (d / "crypto_history" / f"{sym}-USD.parquet").exists():
            return ("crypto", f"{sym}-USD")
        if (d / "stock_history" / "us" / f"{sym}.parquet").exists():
            return ("us", sym)
        if (d / "etf_history" / f"{sym}.parquet").exists():
            return ("etf", sym)
        return None
    if _ASHARE_RE.match(t):
        sec = f"sh.{t}" if t[0] == "6" else f"sz.{t}"
        if (d / "stock_history" / "a" / f"{sec}.parquet").exists():
            return ("a", sec)
        return None
    return None


# ---------------------------------------------------------------------------
# 事件读取
# ---------------------------------------------------------------------------

def load_events(db_path: str = "output/x_agent.db", since: str | None = None,
                data_dir: str | Path = "data") -> pd.DataFrame:
    """读 signals JOIN tweets，映射 ticker，返回逐 (信号, 标的) 事件。

    columns: signal_id, market, security_id, signal_date(本地 naive 归日), score。
    - 只读连接（mode=ro）；created_at 空/不可解析的丢弃；
    - since（YYYY-MM-DD，按信号本地日）过滤；同 (信号,标的) 去重（同一 tweet 内重复 ticker）。
    """
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT s.tweet_id, s.tickers, s.score, t.created_at "
            "FROM signals s JOIN tweets t ON s.tweet_id = t.id "
            "WHERE t.created_at IS NOT NULL AND t.created_at != ''"
        ).fetchall()
    finally:
        con.close()

    recs: list[dict] = []
    since_ts = pd.to_datetime(since) if since else None
    for signal_id, tickers_json, score, created_at in rows:
        try:
            tickers = json.loads(tickers_json) if tickers_json else []
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(tickers, list) or not tickers:
            continue
        ts = pd.to_datetime(created_at, utc=True, errors="coerce", format="ISO8601")
        if pd.isna(ts):
            continue
        for tk in tickers:
            mapped = map_signal_ticker(tk, data_dir)
            if mapped is None:
                continue
            market, sec = mapped
            local = ts.tz_convert(MARKET_TZ[market]).tz_localize(None).normalize()
            if since_ts is not None and local < since_ts:
                continue
            recs.append({"signal_id": signal_id, "market": market,
                         "security_id": sec, "signal_date": local, "score": int(score or 0)})

    if not recs:
        return pd.DataFrame(columns=["signal_id", "market", "security_id", "signal_date", "score"])
    df = pd.DataFrame(recs)
    return df.drop_duplicates(subset=["signal_id", "security_id"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# 纯计算核心（可单测，无 IO）
# ---------------------------------------------------------------------------

def forward_returns(events: pd.DataFrame, close: pd.DataFrame, calendar: pd.DatetimeIndex,
                    bench: pd.Series | None, horizons: tuple[int, ...],
                    tradable: pd.DataFrame | None = None,
                    open_limit_up: pd.DataFrame | None = None) -> pd.DataFrame:
    """给定宽表 close(date×symbol)、日历、基准序列，逐事件逐 horizon 算前瞻收益。

    入场 entry = 信号本地日严格之后的第一个交易日（calendar.searchsorted(side='right')）。
    退出 exit = entry + h。收益 = close[exit]/close[entry]-1；超额 = 收益 - 基准同窗收益。
    - entry 越界 / exit 越界（收益窗口不足）/ 价格缺失或为 0 → 丢弃该行，绝不补值。
    - hit = 1 当超额>0（无基准时按收益>0）。
    - **tradable_entry**（复用 backtest 涨跌停/停牌约定）：入场日停牌或一字涨停 → 该收盘价无法
      成交，收益是虚的。不丢弃（对齐 event_study 的 close-to-close 口径），而是打 flag=0，
      调用方可据此过滤；tradable/open_limit_up 未提供（如 crypto）时 flag=None。
    返回逐行：signal_id, security_id, market, horizon, signal_date, entry_date,
              entry_close, exit_close, ret, excess, hit, score, tradable_entry。
    """
    out: list[dict] = []
    if len(events) == 0:
        return pd.DataFrame(out)
    # 基准对齐到市场日历后 ffill 填补偶发假日缺口；但 bench_last 之后属于"外推 ffill"，
    # 那段的超额是假的（如 000300 数据止于 06-26，却给 06-29 信号算超额=0），必须置 NaN。
    bench_aligned = bench.reindex(calendar).ffill() if bench is not None else None
    bench_last = bench.index.max() if bench is not None else None

    # 严格之后：side='right' 使信号当日不算入场日
    entry_pos = calendar.searchsorted(pd.DatetimeIndex(events["signal_date"]), side="right")
    for row, ep in zip(events.itertuples(index=False), entry_pos):
        sym = row.security_id
        if ep >= len(calendar) or sym not in close.columns:
            continue
        c0 = close.iloc[ep][sym]
        if pd.isna(c0) or c0 == 0:
            continue
        # 入场可成交性：停牌(不可交易) 或 开盘一字涨停 → 无法在该收盘价建仓
        tradable_entry = None
        if tradable is not None and sym in tradable.columns:
            ok = bool(tradable.iloc[ep][sym])
            if open_limit_up is not None and sym in open_limit_up.columns:
                ok = ok and not bool(open_limit_up.iloc[ep][sym])
            tradable_entry = int(ok)
        for h in horizons:
            xp = ep + h
            if xp >= len(calendar):
                continue  # 收益窗口不足 → 丢弃
            c1 = close.iloc[xp][sym]
            if pd.isna(c1) or c1 == 0:
                continue
            ret = c1 / c0 - 1.0
            excess = float("nan")
            # 仅当收益窗口两端都落在基准的真实覆盖区间内才算超额（不用外推 ffill 的假值）
            if bench_aligned is not None and calendar[xp] <= bench_last:
                b0, b1 = bench_aligned.iloc[ep], bench_aligned.iloc[xp]
                if pd.notna(b0) and pd.notna(b1) and b0 != 0:
                    excess = ret - (b1 / b0 - 1.0)
            base = excess if bench_aligned is not None and pd.notna(excess) else ret
            out.append({
                "signal_id": row.signal_id, "security_id": sym, "market": row.market,
                "horizon": int(h), "signal_date": pd.Timestamp(row.signal_date).date().isoformat(),
                "entry_date": calendar[ep].date().isoformat(),
                "entry_close": float(c0), "exit_close": float(c1),
                "ret": float(ret), "excess": (float(excess) if pd.notna(excess) else None),
                "hit": int(base > 0), "score": int(row.score),
                "tradable_entry": tradable_entry,
            })
    return pd.DataFrame(out)


# ---------------------------------------------------------------------------
# 编排：按市场分组加载行情，算全部信号绩效
# ---------------------------------------------------------------------------

def compute_performance(events: pd.DataFrame, horizons: tuple[int, ...] = (1, 5, 20),
                        data_dir: str | Path = "data") -> pd.DataFrame:
    """按市场分组：只加载引用到的标的行情 + 该市场基准，逐组算前瞻收益，纵向拼接。"""
    if len(events) == 0:
        return pd.DataFrame()
    parts: list[pd.DataFrame] = []
    for market, grp in events.groupby("market"):
        symbols = sorted(grp["security_id"].unique())
        try:
            md = load_market_data(market, symbols, data_dir=data_dir)
        except FileNotFoundError:
            # 个别标的缺文件：逐个筛掉再试一次
            ok = [s for s in symbols
                  if (Path(data_dir) / _market_rel(market) / f"{s}.parquet").exists()]
            if not ok:
                continue
            md = load_market_data(market, ok, data_dir=data_dir)
            grp = grp[grp["security_id"].isin(ok)]
        bench = None
        bench_name = MARKET_BENCH.get(market)
        if bench_name:
            try:
                bench = load_benchmark(bench_name, data_dir=data_dir)
            except FileNotFoundError:
                bench = None
        parts.append(forward_returns(grp, md.close, md.calendar, bench, horizons,
                                     tradable=md.tradable, open_limit_up=md.open_limit_up))
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


def _market_rel(market: str) -> str:
    from backtest.data import MARKET_DIRS
    return MARKET_DIRS[market]


# ---------------------------------------------------------------------------
# 落表（可写 x_agent.db，busy_timeout 并发安全，CREATE IF NOT EXISTS）
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS signal_performance(
  signal_id   TEXT,
  security_id TEXT,
  market      TEXT,
  horizon     INTEGER,
  signal_date TEXT,
  entry_date  TEXT,
  entry_close REAL,
  exit_close  REAL,
  ret         REAL,
  excess      REAL,
  hit         INTEGER,
  score       INTEGER,
  tradable_entry INTEGER,
  computed_at TEXT,
  PRIMARY KEY(signal_id, security_id, horizon)
)
"""


def write_performance(df: pd.DataFrame, db_path: str = "output/x_agent.db") -> int:
    """UPSERT 到 signal_performance。busy_timeout=30s 应对其他 agent 并发写库。"""
    con = sqlite3.connect(db_path, timeout=30.0)
    try:
        con.execute("PRAGMA busy_timeout=30000")
        con.execute(_DDL)
        # 幂等迁移：老版本表可能缺 tradable_entry 列
        existing = {r[1] for r in con.execute("PRAGMA table_info(signal_performance)")}
        if "tradable_entry" not in existing:
            con.execute("ALTER TABLE signal_performance ADD COLUMN tradable_entry INTEGER")
        if len(df) == 0:
            con.commit()
            return 0
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        cols = ["signal_id", "security_id", "market", "horizon", "signal_date",
                "entry_date", "entry_close", "exit_close", "ret", "excess", "hit", "score",
                "tradable_entry"]
        recs = [tuple(r[c] for c in cols) + (now,) for _, r in df.iterrows()]
        con.executemany(
            "INSERT INTO signal_performance("
            + ",".join(cols) + ",computed_at) VALUES (" + ",".join(["?"] * (len(cols) + 1)) + ") "
            "ON CONFLICT(signal_id,security_id,horizon) DO UPDATE SET "
            "market=excluded.market, signal_date=excluded.signal_date, "
            "entry_date=excluded.entry_date, entry_close=excluded.entry_close, "
            "exit_close=excluded.exit_close, ret=excluded.ret, excess=excluded.excess, "
            "hit=excluded.hit, score=excluded.score, "
            "tradable_entry=excluded.tradable_entry, computed_at=excluded.computed_at",
            recs,
        )
        con.commit()
        return len(recs)
    finally:
        con.close()
