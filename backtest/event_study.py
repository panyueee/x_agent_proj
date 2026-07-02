# -*- coding: utf-8 -*-
"""signals 表事件研究：ticker 映射、事件读取、事件收益统计。

接口冻结于 docs/backtest_design.md §3.5，禁止 import x_agent。
数据事实（已核实）：
- signals 表 260 行，tickers 为 JSON 数组（如 '["$SOL"]'、'["603881"]'）；
- 时间戳需 JOIN tweets.created_at（ISO 格式，可能为空串）；
- output/x_agent.db 只读（另有进程写 rag.db，绝不能碰）。
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:  # 仅类型标注用，避免运行期依赖 data.py
    from .data import MarketData

# ---------------------------------------------------------------------------
# ticker 映射
# ---------------------------------------------------------------------------

# $ 开头的加密符号：字母/数字组合
_CRYPTO_RE = re.compile(r"^\$([A-Za-z0-9]+)$")
# 6 位纯数字的 A 股代码
_ASHARE_RE = re.compile(r"^\d{6}$")


def map_ticker(ticker: str) -> tuple[str, str] | None:
    """把 signals 表里的 ticker 文本映射为 (market, symbol)。

    - "$BTC" → ("crypto", "BTC-USD")（统一大写）
    - 6 位数字：6 开头 → ("a", "sh.XXXXXX")；0/3 开头 → ("a", "sz.XXXXXX")
    - 其余（无法识别的前缀、非 0/3/6 开头的 6 位码等）返回 None，调用方丢弃
    """
    if not isinstance(ticker, str):
        return None
    t = ticker.strip()
    m = _CRYPTO_RE.match(t)
    if m:
        return ("crypto", m.group(1).upper() + "-USD")
    if _ASHARE_RE.match(t):
        if t[0] == "6":
            return ("a", f"sh.{t}")
        if t[0] in ("0", "3"):
            return ("a", f"sz.{t}")
        return None
    return None


# ---------------------------------------------------------------------------
# 事件读取
# ---------------------------------------------------------------------------

def load_signal_events(db_path: str = "output/x_agent.db", market: str = "crypto",
                       min_score: int = 0) -> pd.DataFrame:
    """从 signals 表读取事件，返回 columns: date, symbol, score。

    - sqlite 严格只读连接（URI mode=ro），绝不写库；
    - JOIN tweets 取 created_at，空串/无法解析的行丢弃；
    - tickers JSON 数组逐个 map_ticker，只保留映射到目标 market 的标的；
    - date 归一化到自然日（去时分秒；market=="a" 时先转北京时间再取日期，
      对齐交易日历的"当日或其后第一个交易日"由下游 event_study / 策略完成）；
    - 同日同标的去重，保留最高 score。
    """
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT s.tickers, s.score, t.created_at "
            "FROM signals s JOIN tweets t ON s.tweet_id = t.id "
            "WHERE s.score >= ?", (min_score,)
        ).fetchall()
    finally:
        con.close()

    records: list[tuple[str, str, int]] = []  # (created_at, symbol, score)
    for tickers_json, score, created_at in rows:
        if not created_at:  # 空串 / None 丢弃
            continue
        try:
            tickers = json.loads(tickers_json) if tickers_json else []
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(tickers, list):
            continue
        for tk in tickers:
            mapped = map_ticker(tk)
            if mapped is None or mapped[0] != market:
                continue
            records.append((created_at, mapped[1], int(score or 0)))

    if not records:
        return pd.DataFrame(columns=["date", "symbol", "score"])

    df = pd.DataFrame(records, columns=["created_at", "symbol", "score"])
    # ISO 时间戳解析（带 Z 后缀 → UTC）；解析失败的丢弃
    ts = pd.to_datetime(df["created_at"], utc=True, errors="coerce", format="ISO8601")
    if market == "a":
        # A 股信号按北京时间归日，避免 UTC 晚间信号错归前一天
        ts = ts.dt.tz_convert("Asia/Shanghai")
    df["date"] = ts.dt.tz_localize(None).dt.normalize()
    df = df.dropna(subset=["date"])

    # 同日同标的去重，保留最高分
    df = (df.groupby(["date", "symbol"], as_index=False)["score"].max()
            .sort_values(["date", "symbol"])
            .reset_index(drop=True))
    return df[["date", "symbol", "score"]]


def align_event_dates(dates: pd.Series, calendar: pd.DatetimeIndex) -> pd.Series:
    """把事件日归一到交易日历：取信号日当日或其后第一个交易日；越界返回 NaT。"""
    pos = calendar.searchsorted(pd.DatetimeIndex(dates))
    aligned = pd.Series(pd.NaT, index=dates.index, dtype="datetime64[ns]")
    ok = pos < len(calendar)
    aligned[ok] = calendar[pos[ok]]
    return aligned


# ---------------------------------------------------------------------------
# 事件研究统计
# ---------------------------------------------------------------------------

def event_study(events: pd.DataFrame, data: "MarketData", benchmark: pd.Series | None = None,
                horizons: tuple[int, ...] = (1, 3, 5, 10)) -> pd.DataFrame:
    """事件后 h 日收益统计，每个 horizon 一行。

    事件日 t 的 h 日收益 = close[t+h]/close[t] - 1，t 为信号日或其后第一个交易日。
    返回 columns: n_events, avg_return, median_return, win_rate,
    avg_excess（有基准时）, t_stat；index 为 horizon。
    t_stat = mean / (std / sqrt(n))，n<2 时 NaN。
    """
    cal = data.calendar
    close = data.close
    bench = None
    if benchmark is not None:
        # 基准对齐到回测日历后 ffill，缺失区间的超额记 NaN
        bench = benchmark.reindex(cal).ffill()

    # 事件对齐到日历上的位置（越界 / 标的不在数据里的丢弃）
    positions: list[tuple[int, str]] = []
    if len(events) > 0:
        pos_arr = cal.searchsorted(pd.DatetimeIndex(events["date"]))
        for p, sym in zip(pos_arr, events["symbol"]):
            if p < len(cal) and sym in close.columns:
                positions.append((int(p), sym))

    out_rows = []
    for h in horizons:
        rets: list[float] = []
        excesses: list[float] = []
        for p, sym in positions:
            if p + h >= len(cal):
                continue
            c0 = close.iloc[p][sym]
            c1 = close.iloc[p + h][sym]
            if pd.isna(c0) or pd.isna(c1) or c0 == 0:
                continue
            r = c1 / c0 - 1.0
            rets.append(r)
            if bench is not None:
                b0, b1 = bench.iloc[p], bench.iloc[p + h]
                if pd.notna(b0) and pd.notna(b1) and b0 != 0:
                    excesses.append(r - (b1 / b0 - 1.0))
        arr = np.asarray(rets, dtype=float)
        n = len(arr)
        if n >= 2 and arr.std(ddof=1) > 0:
            t_stat = arr.mean() / (arr.std(ddof=1) / np.sqrt(n))
        else:
            t_stat = float("nan")
        row = {
            "n_events": n,
            "avg_return": arr.mean() if n else float("nan"),
            "median_return": float(np.median(arr)) if n else float("nan"),
            "win_rate": float((arr > 0).mean()) if n else float("nan"),
        }
        if benchmark is not None:
            row["avg_excess"] = float(np.mean(excesses)) if excesses else float("nan")
        row["t_stat"] = t_stat
        out_rows.append(row)

    result = pd.DataFrame(out_rows, index=pd.Index(horizons, name="horizon"))
    return result
