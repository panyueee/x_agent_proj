"""TradingView K线补源模块（基于社区库 tradingview-datafeed，import 名 tvDatafeed）。

用途：yfinance 被墙 / 缺标的时的备用 K 线源。免登录（匿名模式）即可拉取
大部分交易所标的的日线/分钟线，实测单次最多可拉 5000+ 根（官方口径 5000）。

实测记录（2026-07-02，匿名模式）：
  - SSE:600519 / NASDAQ:AAPL / BINANCE:BTCUSDT 日线均可正常返回
  - 返回 DataFrame：datetime 索引 + [symbol, open, high, low, close, volume]
  - n_bars=5000 日线正常；6000 也能返回（不保证长期稳定，建议 ≤5000）
  - 匿名模式部分小众标的/更长历史可能受限，可设 TV_USERNAME/TV_PASSWORD
    环境变量走账号登录（免费账号即可，无需付费）

对外接口：
  get_klines(symbol, exchange="", interval="1D", n_bars=500) -> pd.DataFrame
  返回与 data/stock_history parquet 完全相同的 schema：
    date / open / high / low / close / volume / amount / adj_close / symbol / market
  失败返回空 DataFrame（含全部列），绝不抛异常炸主流程。

符号写法：get_klines("600519", "SSE") 或 get_klines("SSE:600519") 均可。
"""
from __future__ import annotations

import logging
import os
import re

import pandas as pd

logger = logging.getLogger(__name__)

# 与 data/stock_history/*.parquet 一致的列顺序
KLINE_COLUMNS = ["date", "open", "high", "low", "close", "volume",
                 "amount", "adj_close", "symbol", "market"]

# 交易所 → market 标签（与 data/ 下各 history 目录命名一致）
_EXCHANGE_MARKET = {
    "SSE": "a", "SZSE": "a",
    "HKEX": "hk",
    "NASDAQ": "us", "NYSE": "us", "AMEX": "us", "BATS": "us", "OTC": "us",
    "BINANCE": "crypto", "OKX": "crypto", "COINBASE": "crypto",
    "BYBIT": "crypto", "BITGET": "crypto", "KRAKEN": "crypto", "HUOBI": "crypto",
}

# 加密货币计价后缀 → 统一成 crypto_history 的 "<BASE>-USD" 命名
_CRYPTO_QUOTE_RE = re.compile(r"^(.+?)(USDT|USDC|BUSD|USD)$")


def _ensure_ssl_cert() -> None:
    """macOS + Python 3.14 下 websocket-client 走系统 SSL 会缺 CA 证书，
    这里兜底指到 certifi 的证书包（已设置且文件存在则不动）。"""
    cur = os.environ.get("SSL_CERT_FILE")
    if cur and os.path.exists(cur):
        return
    try:
        import certifi
        os.environ["SSL_CERT_FILE"] = certifi.where()
    except Exception:  # certifi 不在也不阻塞，让底层自己报错
        pass


def _interval_of(interval: str):
    """把 "1D"/"1h"/"5m" 等字符串映射到 tvDatafeed.Interval 枚举。"""
    from tvDatafeed import Interval
    table = {
        "1m": Interval.in_1_minute,  "3m": Interval.in_3_minute,
        "5m": Interval.in_5_minute,  "15m": Interval.in_15_minute,
        "30m": Interval.in_30_minute, "45m": Interval.in_45_minute,
        "1h": Interval.in_1_hour,    "2h": Interval.in_2_hour,
        "3h": Interval.in_3_hour,    "4h": Interval.in_4_hour,
        "1d": Interval.in_daily,     "d": Interval.in_daily, "1D": Interval.in_daily,
        "1w": Interval.in_weekly,    "w": Interval.in_weekly, "1W": Interval.in_weekly,
        "1M": Interval.in_monthly,   "M": Interval.in_monthly,
    }
    # 先精确匹配（区分 1m 分钟 / 1M 月线），再小写兜底
    if interval in table:
        return table[interval]
    key = interval.lower()
    if key in table:
        return table[key]
    raise ValueError(f"不支持的 interval: {interval}")


def _normalize_symbol(symbol: str, exchange: str, market: str) -> str:
    """把 TV 符号统一成 data/ 下 parquet 的命名习惯，方便下游直接对齐。

      a      : SSE:600519  -> sh.600519 / SZSE:000858 -> sz.000858
      hk     : HKEX:700    -> 0700.HK
      crypto : BTCUSDT     -> BTC-USD
      us/其他: 原样返回
    """
    if market == "a" and symbol.isdigit():
        prefix = "sh" if exchange == "SSE" else "sz"
        return f"{prefix}.{symbol}"
    if market == "hk" and symbol.isdigit():
        return f"{int(symbol):04d}.HK"
    if market == "crypto":
        m = _CRYPTO_QUOTE_RE.match(symbol.upper())
        if m:
            return f"{m.group(1)}-USD"
    return symbol


class TvClient:
    """TradingView 数据客户端。懒加载底层连接，账号可选（匿名即可用）。"""

    def __init__(self, username: str | None = None, password: str | None = None):
        self._username = username or os.environ.get("TV_USERNAME")
        self._password = password or os.environ.get("TV_PASSWORD")
        self._tv = None  # 懒初始化，避免 import 即建 websocket

    def _get_tv(self):
        if self._tv is None:
            _ensure_ssl_cert()
            from tvDatafeed import TvDatafeed
            self._tv = TvDatafeed(username=self._username, password=self._password)
        return self._tv

    def get_klines(self, symbol: str, exchange: str = "",
                   interval: str = "1D", n_bars: int = 500) -> pd.DataFrame:
        """拉取 K 线并转成 stock_history parquet 同款 schema。

        symbol   : "600519" / "AAPL" / "BTCUSDT"，也接受 "SSE:600519" 合写
        exchange : "SSE" / "NASDAQ" / "BINANCE" 等；合写时可留空
        interval : "1D"（日线）/ "1h" / "5m" / "1W" / "1M" 等
        n_bars   : 根数，单次上限约 5000

        任何失败（网络/标的不存在/被限流）都返回空 DataFrame 并记 warning。
        """
        if ":" in symbol and not exchange:
            exchange, symbol = symbol.split(":", 1)
        exchange = exchange.upper().strip()
        symbol = symbol.strip()

        empty = pd.DataFrame(columns=KLINE_COLUMNS)
        if not symbol or not exchange:
            logger.warning("tv_fetcher: symbol/exchange 不完整: %r %r", symbol, exchange)
            return empty

        try:
            iv = _interval_of(interval)
            raw = self._get_tv().get_hist(symbol=symbol, exchange=exchange,
                                          interval=iv, n_bars=n_bars)
        except Exception as e:
            logger.warning("tv_fetcher: 拉取 %s:%s 失败: %s", exchange, symbol, e)
            return empty
        if raw is None or len(raw) == 0:
            logger.warning("tv_fetcher: %s:%s 返回空数据", exchange, symbol)
            return empty

        market = _EXCHANGE_MARKET.get(exchange, exchange.lower())
        norm_symbol = _normalize_symbol(symbol, exchange, market)
        daily_like = interval.upper().endswith(("D", "W")) or interval == "1M" or interval == "M"

        df = raw.reset_index()
        ts = pd.to_datetime(df["datetime"])
        out = pd.DataFrame({
            # 日线及以上只留日期字符串，与 parquet 一致；分钟/小时线保留完整时间
            "date": ts.dt.strftime("%Y-%m-%d" if daily_like else "%Y-%m-%d %H:%M:%S"),
            "open": df["open"].astype(float),
            "high": df["high"].astype(float),
            "low": df["low"].astype(float),
            "close": df["close"].astype(float),
            "volume": df["volume"].fillna(0).round().astype("int64"),
            "amount": None,      # TV 不提供成交额
            "adj_close": None,   # TV 日线默认已做拆股复权，此列留空以示来源差异
            "symbol": norm_symbol,
            "market": market,
        })
        return out[KLINE_COLUMNS]


# 模块级默认客户端（懒建），供简单调用
_default_client: TvClient | None = None


def get_klines(symbol: str, exchange: str = "",
               interval: str = "1D", n_bars: int = 500) -> pd.DataFrame:
    """模块级快捷入口，见 TvClient.get_klines。"""
    global _default_client
    if _default_client is None:
        _default_client = TvClient()
    return _default_client.get_klines(symbol, exchange, interval, n_bars)
