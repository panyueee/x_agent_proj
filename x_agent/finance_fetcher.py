"""金融行情抓取模块：A股、美股、加密货币实时价格与日K线数据。

数据源（均为国内可访问的免费接口，无需鉴权）：
  - 新浪财经 hq.sinajs.cn → A股实时行情
  - baostock              → A股日K线
  - 东方财富 push2.eastmoney.com → 美股实时行情 + K线
  - gate.io API           → 加密货币实时行情 + K线（国内可访问）

所有方法独立异常捕获，单一来源失败不阻塞其他来源。
"""
from __future__ import annotations

import datetime as dt
import json
import re
import requests
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class PriceBar:
    """统一行情数据结构，覆盖实时快照与历史K线。"""
    symbol: str        # 代码，如 "600519"、"AAPL"、"BTC/USDT"
    market: str        # "a_shares" | "us_stocks" | "crypto"
    name: str          # 可读名称
    timestamp: str     # ISO8601 时间戳
    open: float
    high: float
    low: float
    close: float
    volume: float
    change_pct: float  # 涨跌幅百分比，如 2.5 表示 +2.5%


# ---- 内部工具函数 ----

def _now_iso() -> str:
    return dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_float(val, default=0.0):
    """容忍 None / NaN / 空字符串的安全转换。"""
    try:
        import math
        f = float(val)
        return default if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return default


_SINA_HEADERS = {"Referer": "https://finance.sina.com.cn"}
_EMC_HEADERS  = {"Referer": "https://eastmoney.com"}


class FinanceClient:
    """行情客户端，按数据源分组，每组独立容错。"""

    # ---- A股实时行情（新浪财经）----

    def fetch_a_shares(self, symbols, names=None):
        # type: (List[str], Optional[List[str]]) -> List[PriceBar]
        """
        拉取 A股实时行情。
        symbols: 纯数字代码，如 ["600519", "000858"]
        names:   对应的中文名称（可选）
        """
        name_map = dict(zip(symbols, names)) if names else {}

        def _prefix(code):
            return ("sh" if code.startswith(("6", "9")) else "sz") + code

        bars = []
        try:
            sina_codes = ",".join(_prefix(s) for s in symbols)
            url = f"http://hq.sinajs.cn/list={sina_codes}"
            resp = requests.get(url, headers=_SINA_HEADERS, timeout=10)
            resp.encoding = "gbk"
            ts = _now_iso()
            for line in resp.text.splitlines():
                # var hq_str_sh600519="贵州茅台,昨收,今开,最高,最低,当前价,成交量(手),..."
                m = re.search(r'hq_str_s[hz](\d+)="([^"]*)"', line)
                if not m:
                    continue
                code   = m.group(1)
                fields = m.group(2).split(",")
                if len(fields) < 9:
                    continue
                stock_name = fields[0]
                prev_close = _safe_float(fields[2])
                close      = _safe_float(fields[3])
                change_pct = ((close - prev_close) / prev_close * 100) if prev_close else 0.0
                bars.append(PriceBar(
                    symbol=code,
                    market="a_shares",
                    name=name_map.get(code) or stock_name,
                    timestamp=ts,
                    open=_safe_float(fields[1]),
                    high=_safe_float(fields[4]),
                    low=_safe_float(fields[5]),
                    close=close,
                    volume=_safe_float(fields[8]) * 100,  # 手 → 股
                    change_pct=change_pct,
                ))
        except Exception as e:
            print(f"[finance] A股实时行情失败: {e}")
        return bars

    # ---- A股日K线（baostock）----

    def _kline_a_shares(self, symbol, days):
        # type: (str, int) -> List[PriceBar]
        try:
            import baostock as bs
        except ImportError:
            print("[finance] baostock 未安装，A股K线跳过。运行: pip install baostock")
            return []
        try:
            prefix   = "sh." if symbol.startswith(("6", "9")) else "sz."
            end      = dt.date.today()
            start    = end - dt.timedelta(days=days + 10)
            bs.login()
            rs = bs.query_history_k_data_plus(
                prefix + symbol,
                "date,open,high,low,close,volume,pctChg",
                start_date=start.strftime("%Y-%m-%d"),
                end_date=end.strftime("%Y-%m-%d"),
                frequency="d", adjustflag="3",
            )
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
            bs.logout()
            bars = []
            for row in rows[-days:]:
                date_str, o, h, l, c, vol, pct = row
                bars.append(PriceBar(
                    symbol=symbol, market="a_shares", name=symbol,
                    timestamp=date_str + "T00:00:00Z",
                    open=_safe_float(o), high=_safe_float(h),
                    low=_safe_float(l), close=_safe_float(c),
                    volume=_safe_float(vol), change_pct=_safe_float(pct),
                ))
            return bars
        except Exception as e:
            print(f"[finance] A股K线 {symbol} 失败: {e}")
            return []

    # ---- 美股实时行情（东方财富）----

    def fetch_us_stocks(self, symbols):
        # type: (List[str]) -> List[PriceBar]
        """
        拉取美股实时行情。symbols: ["AAPL", "NVDA", ...]
        东方财富 secid 格式：105.AAPL（纳斯达克）或 106.MSFT（纽交所）
        大多数科技股在 105（NASDAQ）；这里统一先试 105，失败再试 106。
        """
        bars = []
        ts = _now_iso()
        for sym in symbols:
            bar = self._fetch_us_one(sym, ts)
            if bar:
                bars.append(bar)
        return bars

    def _fetch_us_one(self, sym, ts):
        # type: (str, str) -> Optional[PriceBar]
        for market_id in ("105", "106", "107"):
            try:
                url = (
                    "https://push2.eastmoney.com/api/qt/stock/get"
                    f"?secid={market_id}.{sym}"
                    "&fields=f43,f44,f45,f46,f57,f58,f170,f47"
                )
                r = requests.get(url, headers=_EMC_HEADERS, timeout=8)
                d = r.json().get("data") or {}
                if not d or d.get("f43") in (None, "-"):
                    continue
                close      = _safe_float(d["f43"]) / 1000   # 东方财富价格 × 1000 存储
                high       = _safe_float(d["f44"]) / 1000
                low        = _safe_float(d["f45"]) / 1000
                open_price = _safe_float(d["f46"]) / 1000
                change_pct = _safe_float(d.get("f170", 0)) / 100  # 百分比 × 100 存储
                volume     = _safe_float(d.get("f47", 0))
                name       = d.get("f58") or sym
                return PriceBar(
                    symbol=sym, market="us_stocks", name=name,
                    timestamp=ts,
                    open=open_price, high=high, low=low, close=close,
                    volume=volume, change_pct=change_pct,
                )
            except Exception:
                continue
        print(f"[finance] 美股 {sym} 行情获取失败")
        return None

    # ---- 美股日K线（东方财富）----

    def _kline_us_stocks(self, symbol, days):
        # type: (str, int) -> List[PriceBar]
        for market_id in ("105", "106", "107"):
            try:
                url = (
                    "https://push2his.eastmoney.com/api/qt/stock/kline/get"
                    f"?secid={market_id}.{symbol}"
                    "&fields1=f1,f2,f3,f4,f5,f6"
                    "&fields2=f51,f52,f53,f54,f55,f56"
                    f"&klt=101&fqt=1&end=20500101&lmt={days}"
                )
                r = requests.get(url, headers=_EMC_HEADERS, timeout=10)
                klines = (r.json().get("data") or {}).get("klines") or []
                if not klines:
                    continue
                bars = []
                prev_close = None
                for line in klines:
                    # "2026-06-24,open,close,high,low,volume"
                    parts = line.split(",")
                    if len(parts) < 6:
                        continue
                    date_str, o, c, h, l, vol = parts[:6]
                    close = _safe_float(c)
                    change_pct = ((close - prev_close) / prev_close * 100) if prev_close else 0.0
                    prev_close = close
                    bars.append(PriceBar(
                        symbol=symbol, market="us_stocks", name=symbol,
                        timestamp=date_str + "T00:00:00Z",
                        open=_safe_float(o), high=_safe_float(h),
                        low=_safe_float(l), close=close,
                        volume=_safe_float(vol), change_pct=change_pct,
                    ))
                return bars
            except Exception:
                continue
        print(f"[finance] 美股K线 {symbol} 获取失败")
        return []

    # ---- 加密货币实时行情（gate.io 公开接口）----

    def fetch_crypto(self, symbols):
        # type: (List[str]) -> List[PriceBar]
        """
        symbols: ccxt 格式 ["BTC/USDT", "ETH/USDT"]，内部转换为 gate.io 格式 BTC_USDT。
        """
        bars = []
        ts = _now_iso()
        for sym in symbols:
            pair = sym.replace("/", "_")
            try:
                url = f"https://api.gateio.ws/api/v4/spot/tickers?currency_pair={pair}"
                r = requests.get(url, timeout=10)
                data = r.json()
                if not data:
                    continue
                t = data[0]
                close      = _safe_float(t.get("last"))
                open_price = _safe_float(t.get("open_24h", close))
                high       = _safe_float(t.get("high_24h", close))
                low        = _safe_float(t.get("low_24h", close))
                volume     = _safe_float(t.get("base_volume", 0))
                change_pct = ((close - open_price) / open_price * 100) if open_price else 0.0
                bars.append(PriceBar(
                    symbol=sym, market="crypto", name=sym,
                    timestamp=ts,
                    open=open_price, high=high, low=low, close=close,
                    volume=volume, change_pct=change_pct,
                ))
            except Exception as e:
                print(f"[finance] 加密货币 {sym} 失败: {e}")
        return bars

    # ---- 加密货币日K线（gate.io）----

    def _kline_crypto(self, symbol, days):
        # type: (str, int) -> List[PriceBar]
        pair = symbol.replace("/", "_")
        try:
            url = f"https://api.gateio.ws/api/v4/spot/candlesticks?currency_pair={pair}&interval=1d&limit={days}"
            r = requests.get(url, timeout=10)
            raw = r.json()   # [[timestamp, volume, close, high, low, open], ...]
            bars = []
            prev_close = None
            for candle in raw:
                ts_ms = int(candle[0])
                vol   = _safe_float(candle[1])
                close = _safe_float(candle[2])
                high  = _safe_float(candle[3])
                low   = _safe_float(candle[4])
                open_ = _safe_float(candle[5])
                change_pct = ((close - prev_close) / prev_close * 100) if prev_close else 0.0
                prev_close = close
                ts_str = dt.datetime.utcfromtimestamp(ts_ms).strftime("%Y-%m-%dT%H:%M:%SZ")
                bars.append(PriceBar(
                    symbol=symbol, market="crypto", name=symbol,
                    timestamp=ts_str,
                    open=open_, high=high, low=low, close=close,
                    volume=vol, change_pct=change_pct,
                ))
            return bars
        except Exception as e:
            print(f"[finance] 加密K线 {symbol} 失败: {e}")
            return []

    # ---- K线统一入口 ----

    def fetch_kline(self, symbol, market, days=7):
        # type: (str, str, int) -> List[PriceBar]
        """拉取日K线，按 market 路由到对应数据源。"""
        if market == "a_shares":
            return self._kline_a_shares(symbol, days)
        if market == "us_stocks":
            return self._kline_us_stocks(symbol, days)
        if market == "crypto":
            return self._kline_crypto(symbol, days)
        print(f"[finance] 未知 market: {market}")
        return []
