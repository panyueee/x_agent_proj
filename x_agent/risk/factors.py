"""简版因子库：全历史 A 股因子日收益（纯 pandas，不依赖 toraniko）。

因子定义（v1，取舍见 docs/aladdin/05-personal-roadmap.md §3）：
  - mkt  : 全 A 等权日收益；
  - size : 规模代理 = 60 日均成交额（close*volume），底 3 成(小) - 顶 3 成(大) 等权多空；
  - mom  : 12-1 月动量（t-252 → t-21 收益），顶 3 成 - 底 3 成；
  - vol  : 60 日收益波动，低 3 成 - 高 3 成；
  - 行业 : 该 GICS 行业等权收益 - mkt（超额形式，避免与 mkt 共线）。

信号一律 shift(1)（用昨日信息分组，防前视）；停牌日（tradable=False）收益记 NaN，
不进任何分组。结果缓存 output/factors/factor_returns_{market}.parquet。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.data import list_symbols, load_market_data

STYLE_FACTORS = ["mkt", "size", "mom", "vol"]

# 与 x_agent/factor_model.py 的 ALL_SECTORS（sw_sector_cache 值域）一致
GICS_11 = [
    "Communication Services", "Consumer Discretionary", "Consumer Staples",
    "Energy", "Financials", "Health Care", "Industrials", "Materials",
    "Real Estate", "Technology", "Utilities",
]

FACTORS = STYLE_FACTORS + GICS_11


def load_sector_map(db_path: str = "output/x_agent.db") -> dict[str, str]:
    """sw_sector_cache: 6 位裸代码 → GICS；键统一转成 parquet symbol（sh./sz.）。

    前缀规则与 backtest.event_study.map_ticker 对齐：6 开头→sh，0/3 开头→sz；
    其余（北交所等）跳过。表不存在/库缺失时返回空 dict（调用方全部落 Others）。
    """
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT symbol, gics_sector FROM sw_sector_cache"
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return {}

    out: dict[str, str] = {}
    for code, sector in rows:
        code = str(code).strip()
        if len(code) != 6 or not code.isdigit():
            continue
        if code[0] == "6":
            out[f"sh.{code}"] = sector
        elif code[0] in ("0", "3"):
            out[f"sz.{code}"] = sector
    return out


# ---- 内部工具 ----

def _load_wide(market: str, symbols: list[str], start: str | None, end: str | None,
               data_dir: str | Path, chunk: int = 500):
    """分批 load_market_data → (close, volume, tradable) 三张全市场宽表（float32）。"""
    closes, volumes, tradables = [], [], []
    for i in range(0, len(symbols), chunk):
        batch = symbols[i:i + chunk]
        md = load_market_data(market, batch, start=start, end=end, data_dir=data_dir)
        closes.append(md.close.astype("float32"))
        volumes.append(md.volume.astype("float32"))
        tradables.append(md.tradable)

    # 各批次日历可能不同：对齐到并集日历后拼接
    calendar = pd.DatetimeIndex(sorted(set().union(*(c.index for c in closes))))
    close = pd.concat([c.reindex(calendar) for c in closes], axis=1).ffill()
    volume = pd.concat([v.reindex(calendar).fillna(0.0) for v in volumes], axis=1)
    tradable = pd.concat([t.reindex(calendar, fill_value=False) for t in tradables], axis=1)
    return close, volume, tradable


def _ls_return(ret: pd.DataFrame, signal: pd.DataFrame,
               long_low: bool, q: float = 0.30) -> pd.Series:
    """按信号截面分位做等权多空：long_low=True 时 低分位组 - 高分位组。"""
    sig = signal.where(ret.notna())
    lo = sig.quantile(q, axis=1)
    hi = sig.quantile(1.0 - q, axis=1)
    low_ret = ret.where(sig.le(lo, axis=0)).mean(axis=1)
    high_ret = ret.where(sig.ge(hi, axis=0)).mean(axis=1)
    return (low_ret - high_ret) if long_low else (high_ret - low_ret)


def _latest_data_date(market: str, symbols: list[str],
                      data_dir: str | Path) -> pd.Timestamp | None:
    """用一只参考标的的 parquet 尾行日期近似"数据最新交易日"（判缓存新鲜度用）。"""
    ref = "sh.600519" if market == "a" and "sh.600519" in symbols else symbols[0]
    try:
        md = load_market_data(market, [ref], data_dir=data_dir)
        return md.calendar[-1]
    except Exception:
        return None


# ---- 公开接口 ----

def compute_factor_returns(close: pd.DataFrame, volume: pd.DataFrame,
                           tradable: pd.DataFrame,
                           sector_map: dict[str, str],
                           min_breadth: int = 1) -> pd.DataFrame:
    """由 (close, volume, tradable) 宽表算 date×factor 因子日收益（核心纯数学，可单测）。

    稳健性处理：
    - |日收益| >= 95% 视为脏数据（前复权异常）或新股首日，剔除出截面；
    - 当日有效收益数 < min_breadth 的"日历薄日"（个别标的的节假日脏行撑出的
      union 日历日）整日剔除。
    """
    ret = close.pct_change(fill_method=None)
    ret = ret.where(tradable)                 # 停牌日收益不参与截面
    ret = ret.where(ret.abs() < 0.95)         # 脏数据 / 新股首日剔除
    breadth = ret.notna().sum(axis=1)
    ret.loc[breadth < min_breadth, :] = np.nan

    out = pd.DataFrame(index=ret.index)
    out["mkt"] = ret.mean(axis=1)

    # size：60 日均成交额代理（shift(1) 防前视），小 - 大
    adv = (close * volume).rolling(60, min_periods=30).mean().shift(1)
    out["size"] = _ls_return(ret, adv, long_low=True)

    # mom：12-1 月动量，高 - 低
    mom_sig = (close.shift(21) / close.shift(252) - 1.0)
    out["mom"] = _ls_return(ret, mom_sig, long_low=False)

    # vol：60 日波动，低 - 高
    vol_sig = ret.rolling(60, min_periods=40).std().shift(1)
    out["vol"] = _ls_return(ret, vol_sig, long_low=True)

    # 行业：行业等权收益 - mkt（超额形式）
    by_sector: dict[str, list[str]] = {}
    for sym in ret.columns:
        sec = sector_map.get(sym)
        if sec in GICS_11:
            by_sector.setdefault(sec, []).append(sym)
    for sec in GICS_11:
        cols = by_sector.get(sec, [])
        out[sec] = (ret[cols].mean(axis=1) - out["mkt"]) if cols else np.nan

    # 去掉起始暖机段（mom 需要 252 日历史）：style 因子齐了才保留
    out = out.dropna(subset=STYLE_FACTORS)
    return out.astype("float64")


def build_factor_returns(market: str = "a", start: str = "2005-01-01",
                         end: str | None = None,
                         data_dir: str | Path = "data",
                         cache_dir: str | Path = "output/factors",
                         db_path: str = "output/x_agent.db",
                         sector_map: dict[str, str] | None = None,
                         symbols: list[str] | None = None,
                         chunk: int = 500,
                         force: bool = False) -> pd.DataFrame:
    """全历史因子日收益 date×factor。缓存新鲜（尾日期 >= 数据最新交易日或请求 end）直接读。

    "收盘冻结"跑批入口：每晚数据更新后跑一次，白天所有查询只读缓存。
    """
    cache_dir = Path(cache_dir)
    cache_path = cache_dir / f"factor_returns_{market}.parquet"
    symbols = symbols or list_symbols(market, data_dir)
    if not symbols:
        raise FileNotFoundError(f"{data_dir} 下没有 {market} 市场的 parquet 数据")

    if cache_path.exists() and not force:
        cached = pd.read_parquet(cache_path)
        cached.index = pd.to_datetime(cached.index)
        target = pd.to_datetime(end) if end else _latest_data_date(market, symbols, data_dir)
        if target is None or (len(cached) and cached.index[-1] >= target):
            if end:
                cached = cached.loc[:pd.to_datetime(end)]
            return cached.loc[pd.to_datetime(start):]

    if sector_map is None:
        sector_map = load_sector_map(db_path)

    # 多留 400 个自然日暖机，保证 start 当天动量/波动信号可用
    warm_start = (pd.to_datetime(start) - pd.Timedelta(days=400)).strftime("%Y-%m-%d")
    close, volume, tradable = _load_wide(market, symbols, warm_start, end, data_dir, chunk)
    min_breadth = min(100, max(1, len(symbols) // 2))
    fr = compute_factor_returns(close, volume, tradable, sector_map,
                                min_breadth=min_breadth)
    fr = fr.loc[pd.to_datetime(start):]

    cache_dir.mkdir(parents=True, exist_ok=True)
    fr.to_parquet(cache_path)
    return fr
