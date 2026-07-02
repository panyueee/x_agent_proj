"""backtest 数据层：本地 parquet → 对齐统一日历的 date×symbol 宽表（MarketData）。

数据事实（详见 docs/backtest_design.md §1）：
  - 标准 schema（stock_history/*）：date, open, high, low, close, volume, amount,
    adj_close, symbol, market；date 为字符串列。
  - index_history/ 与 crypto_history/ 用 ``ticker`` 列（非 symbol），多 name/category
    列、无 market/amount 列 → 加载时重命名兼容。
  - A 股 parquet 的 adj_close 整列为 None（close 已是 baostock 前复权价）→ 复权因子取 1；
    否则 factor = adj_close / close，OHLC 全部乘同一 factor。
  - 停牌日在 parquet 中无行（对齐日历后为 NaN），也可能出现 volume=0 的行，
    两者都判为不可交易；价格 ffill，上市前的前导 NaN 保留且不可交易。

只依赖 pandas / numpy / 标准库，禁止 import x_agent。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

# 各市场对应的 parquet 子目录（相对 data_dir）
MARKET_DIRS = {"a": "stock_history/a", "us": "stock_history/us", "hk": "stock_history/hk",
               "crypto": "crypto_history", "index": "index_history", "etf": "etf_history",
               "futures": "futures_history", "fx": "fx_history", "bond": "bond_history",
               "cb": "cb_history"}

# 涨跌停判定的容差：对应 rqalpha 涨停价四舍五入的误差带
_LIMIT_TOL = 0.003

# a_share_names.json 缓存：{绝对路径: {"sh.600519": "贵州茅台", ...}}
_NAMES_CACHE: dict[str, dict] = {}


@dataclass
class MarketData:
    """对齐统一日历后的行情宽表集合，所有 DataFrame 均为 date×symbol。"""
    market: str
    calendar: pd.DatetimeIndex        # 所加载标的日期并集，升序
    open: pd.DataFrame                # 复权开盘价，ffill 后
    high: pd.DataFrame
    low: pd.DataFrame
    close: pd.DataFrame               # 复权收盘价，ffill 后；收益一律用它
    volume: pd.DataFrame              # 原始成交量，缺失填 0
    tradable: pd.DataFrame            # bool：当日有真实行情且 volume>0
    limit_up: pd.DataFrame            # bool：收盘涨停（仅 A 股可能 True，其余全 False）
    limit_down: pd.DataFrame
    open_limit_up: pd.DataFrame       # bool：开盘即涨停（开盘涨幅达限，近似一字板）
    open_limit_down: pd.DataFrame


# ---- 内部工具函数 ----

def _load_names(data_dir: Path) -> dict:
    """加载 data/a_share_names.json（代码→当前名称），进程内只读一次；文件缺失/损坏返回空 dict。"""
    key = str((data_dir / "a_share_names.json").resolve())
    if key not in _NAMES_CACHE:
        try:
            with open(key, encoding="utf-8") as f:
                _NAMES_CACHE[key] = json.load(f)
        except (OSError, json.JSONDecodeError):
            _NAMES_CACHE[key] = {}
    return _NAMES_CACHE[key]


def _read_one(path: Path) -> pd.DataFrame:
    """读取单个 parquet → 以 DatetimeIndex 为索引、OHLC 已复权的 DataFrame。

    兼容两种 schema：标准（symbol 列）与 index/crypto（ticker 列）。
    """
    df = pd.read_parquet(path)
    if "ticker" in df.columns and "symbol" not in df.columns:
        df = df.rename(columns={"ticker": "symbol"})

    # date 是字符串列（如 "2026-06-30"），转 datetime 后设为索引并去重排序
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    df = df[~df.index.duplicated(keep="last")]

    # 复权：adj_close 整列为空（A 股，close 已前复权）→ factor=1；
    # 否则 factor = adj_close/close，OHLC 全部乘同一 factor
    adj = pd.to_numeric(df["adj_close"], errors="coerce") if "adj_close" in df.columns else None
    if adj is not None and adj.notna().any():
        factor = (adj / df["close"]).fillna(1.0)
        for col in ("open", "high", "low", "close"):
            df[col] = df[col] * factor

    return df


def _slice(index: pd.DatetimeIndex, start: str | None, end: str | None) -> pd.DatetimeIndex:
    """按 start/end（含端点）裁剪日期索引。"""
    if start is not None:
        index = index[index >= pd.to_datetime(start)]
    if end is not None:
        index = index[index <= pd.to_datetime(end)]
    return index


# ---- 公开接口 ----

def limit_rate(symbol: str, name: str | None) -> float:
    """A 股单日涨跌停幅度：ST→5%，创业板/科创板→20%，北交所→30%，主板→10%。

    name 为当前证券名称（可能为 None）；局限：无历史 ST 时间线，用当前名称近似。
    """
    if name and "ST" in name:
        return 0.05
    code = symbol.split(".")[-1]
    if code[:3] in ("300", "301", "688", "689"):
        return 0.20
    if symbol.lower().startswith("bj"):
        return 0.30
    return 0.10


def list_symbols(market: str, data_dir: str | Path = "data") -> list[str]:
    """列出某市场目录下所有可用标的（parquet 文件名，不含扩展名），升序。"""
    if market not in MARKET_DIRS:
        raise ValueError(f"未知市场: {market!r}，可选: {sorted(MARKET_DIRS)}")
    root = Path(data_dir) / MARKET_DIRS[market]
    if not root.is_dir():
        return []
    return sorted(p.stem for p in root.glob("*.parquet"))


def load_market_data(market: str, symbols: list[str], start: str | None = None,
                     end: str | None = None, data_dir: str | Path = "data") -> MarketData:
    """加载多标的行情并对齐到统一日历（所有标的日期并集），返回 MarketData。

    - 缺文件抛 FileNotFoundError 并列出全部缺失标的。
    - 价格 ffill（停牌沿用最近价），但 tradable=False 标记缺行 / volume<=0；
      上市前的前导 NaN 保持 NaN 且不可交易。
    - 涨跌停仅 market=="a" 计算（基于前复权价的近似，除权日附近可能误判，可接受）。
    """
    if market not in MARKET_DIRS:
        raise ValueError(f"未知市场: {market!r}，可选: {sorted(MARKET_DIRS)}")
    if not symbols:
        raise ValueError("symbols 不能为空")

    data_dir = Path(data_dir)
    root = data_dir / MARKET_DIRS[market]

    # 先整体检查文件存在性，缺哪些一次性报清楚
    missing = [s for s in symbols if not (root / f"{s}.parquet").exists()]
    if missing:
        raise FileNotFoundError(f"{root} 下缺少 parquet: {missing}")

    frames = {s: _read_one(root / f"{s}.parquet") for s in symbols}

    # 统一日历：所有已加载标的的日期并集，升序，再按 start/end 裁剪
    calendar = pd.DatetimeIndex(sorted(set().union(*(df.index for df in frames.values()))))
    calendar = _slice(calendar, start, end)
    if len(calendar) == 0:
        raise ValueError(f"区间 [{start}, {end}] 内无任何交易数据")

    def _wide(col: str) -> pd.DataFrame:
        """按列名拼 date×symbol 宽表并对齐日历（缺行→NaN）。"""
        return pd.DataFrame(
            {s: df[col].reindex(calendar) for s, df in frames.items()}, columns=symbols)

    open_raw, high_raw = _wide("open"), _wide("high")
    low_raw, close_raw = _wide("low"), _wide("close")
    volume = _wide("volume").fillna(0.0)

    # 可交易 = 当日有真实行情（对齐前存在该行）且 volume>0；停牌两种形态都覆盖
    tradable = close_raw.notna() & (volume > 0)

    # 价格 ffill：停牌期沿用最近价用于估值；上市前的前导 NaN 由 ffill 语义天然保留
    open_px, high_px = open_raw.ffill(), high_raw.ffill()
    low_px, close_px = low_raw.ffill(), close_raw.ffill()

    falsy = pd.DataFrame(False, index=calendar, columns=symbols)
    if market == "a":
        # 涨跌停判定：与前收（ffill 后 close 的 shift(1)）比较，容差 0.003
        names = _load_names(data_dir)
        rates = pd.Series({s: limit_rate(s, names.get(s)) for s in symbols})
        prev_close = close_px.shift(1)
        thresh = rates - _LIMIT_TOL  # 各标的的触发阈值（按列广播）

        close_ret = close_px / prev_close - 1
        open_ret = open_px / prev_close - 1
        # 仅在当日真实成交时才可能是涨跌停（停牌 ffill 出来的 0 涨幅不会触发，但显式排除更稳）
        limit_up = close_ret.ge(thresh, axis=1) & tradable
        limit_down = close_ret.le(-thresh, axis=1) & tradable
        open_limit_up = open_ret.ge(thresh, axis=1) & tradable
        open_limit_down = open_ret.le(-thresh, axis=1) & tradable
    else:
        # 其余市场无涨跌停制度（或不建模），矩阵全 False（各自独立副本，避免共享可变对象）
        limit_up, limit_down = falsy, falsy.copy()
        open_limit_up, open_limit_down = falsy.copy(), falsy.copy()

    return MarketData(
        market=market, calendar=calendar,
        open=open_px, high=high_px, low=low_px, close=close_px,
        volume=volume, tradable=tradable,
        limit_up=limit_up, limit_down=limit_down,
        open_limit_up=open_limit_up, open_limit_down=open_limit_down,
    )


def load_benchmark(name: str = "000300_SS", start: str | None = None,
                   end: str | None = None, data_dir: str | Path = "data") -> pd.Series:
    """加载基准的复权收盘价序列（DatetimeIndex，Series.name=标的名）。

    依次尝试 index_history/{name}.parquet、crypto_history/{name}.parquet。
    注意：000300_SS 仅覆盖 2021-03-11 起，更早区间由调用方在重叠区间内算超额。
    """
    data_dir = Path(data_dir)
    candidates = [data_dir / MARKET_DIRS["index"] / f"{name}.parquet",
                  data_dir / MARKET_DIRS["crypto"] / f"{name}.parquet"]
    path = next((p for p in candidates if p.exists()), None)
    if path is None:
        raise FileNotFoundError(f"基准 {name!r} 不存在，已尝试: {[str(p) for p in candidates]}")

    df = _read_one(path)
    # 源数据尾部可能有 close=NaN 的脏行（实测 000300_SS 就有一行），直接剔除
    series = df["close"].dropna()
    series = series.loc[_slice(series.index, start, end)]
    series.name = name
    return series
