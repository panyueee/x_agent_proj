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
import time
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

    # ---- 全球指数实时行情（东方财富）----

    def fetch_indices(self, indices_cfg):
        # type: (List[dict]) -> List[PriceBar]
        """
        拉取全球指数行情。
        indices_cfg: [{"symbol": "HSI", "name": "恒生指数", "secid": "100.HSI"}, ...]
        secid 在 config.yaml 中配置，格式为 "<市场代码>.<代码>"。
        东方财富 f43 字段对指数同样以整数×1000 存储点位。
        """
        bars = []
        ts = _now_iso()
        for item in indices_cfg:
            sym   = item["symbol"]
            name  = item.get("name", sym)
            secid = item.get("secid")
            if not secid:
                print(f"[finance] 指数 {sym} 缺少 secid，跳过")
                continue
            try:
                url = (
                    "https://push2.eastmoney.com/api/qt/stock/get"
                    f"?secid={secid}"
                    "&fields=f43,f44,f45,f46,f57,f58,f170,f47"
                )
                r = requests.get(url, headers=_EMC_HEADERS, timeout=8)
                d = r.json().get("data") or {}
                if not d or d.get("f43") in (None, "-", 0):
                    print(f"[finance] 指数 {sym} ({secid}) 无数据")
                    continue
                close      = _safe_float(d["f43"]) / 1000
                high       = _safe_float(d["f44"]) / 1000
                low        = _safe_float(d["f45"]) / 1000
                open_price = _safe_float(d["f46"]) / 1000
                change_pct = _safe_float(d.get("f170", 0)) / 100
                volume     = _safe_float(d.get("f47", 0))
                bars.append(PriceBar(
                    symbol=sym, market="index", name=name,
                    timestamp=ts,
                    open=open_price, high=high, low=low, close=close,
                    volume=volume, change_pct=change_pct,
                ))
            except Exception as e:
                print(f"[finance] 指数 {sym} 行情失败: {e}")
        return bars

    # ---- 指数日K线（东方财富）----

    def _kline_indices(self, symbol, secid, days):
        # type: (str, str, int) -> List[PriceBar]
        try:
            url = (
                "https://push2his.eastmoney.com/api/qt/stock/kline/get"
                f"?secid={secid}"
                "&fields1=f1,f2,f3,f4,f5,f6"
                "&fields2=f51,f52,f53,f54,f55,f56"
                f"&klt=101&fqt=1&end=20500101&lmt={days}"
            )
            r = requests.get(url, headers=_EMC_HEADERS, timeout=10)
            klines = (r.json().get("data") or {}).get("klines") or []
            if not klines:
                return []
            bars = []
            prev_close = None
            for line in klines:
                parts = line.split(",")
                if len(parts) < 6:
                    continue
                date_str, o, c, h, l, vol = parts[:6]
                close = _safe_float(c)
                change_pct = ((close - prev_close) / prev_close * 100) if prev_close else 0.0
                prev_close = close
                bars.append(PriceBar(
                    symbol=symbol, market="index", name=symbol,
                    timestamp=date_str + "T00:00:00Z",
                    open=_safe_float(o), high=_safe_float(h),
                    low=_safe_float(l), close=close,
                    volume=_safe_float(vol), change_pct=change_pct,
                ))
            return bars
        except Exception as e:
            print(f"[finance] 指数K线 {symbol} 失败: {e}")
            return []

    # ---- K线统一入口 ----

    def fetch_kline(self, symbol, market, days=7, secid=None):
        # type: (str, str, int, Optional[str]) -> List[PriceBar]
        """拉取日K线，按 market 路由到对应数据源。index 市场需传 secid。"""
        if market == "a_shares":
            return self._kline_a_shares(symbol, days)
        if market == "us_stocks":
            return self._kline_us_stocks(symbol, days)
        if market == "crypto":
            return self._kline_crypto(symbol, days)
        if market == "index":
            if not secid:
                print(f"[finance] 指数K线 {symbol} 需要 secid")
                return []
            return self._kline_indices(symbol, secid, days)
        print(f"[finance] 未知 market: {market}")
        return []

    # ---- A股实时行情（带 AKShare fallback）----

    def get_a_share_quote(self, code, name):
        # type: (str, str) -> Optional[dict]
        """
        获取单只 A 股实时行情 dict，先走新浪财经，失败时自动 fallback 到 AKShare。
        返回字段：price / change_pct / volume / open / high / low / close / name / symbol / timestamp
        """
        # 优先走新浪财经（已有实现）
        try:
            bars = self.fetch_a_shares([code], [name])
            if bars:
                b = bars[0]
                return {
                    "symbol": b.symbol,
                    "name": b.name,
                    "timestamp": b.timestamp,
                    "price": b.close,
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume,
                    "change_pct": b.change_pct,
                }
        except Exception as e:
            print(f"[finance] 东财失败，切换 AKShare: {code}（{e}）")

        # fallback 到 AKShare
        print(f"[finance] 东财失败，切换 AKShare: {code}")
        ak_client = AKShareClient()
        return ak_client.get_a_share_quote(code, name)


# ---- AKShare 客户端 ----

class AKShareClient:
    """
    AKShare 数据客户端，作为东方财富的备份数据源，
    并补充龙虎榜、北向资金等东财未覆盖的数据。
    接口签名与 FinanceClient 相关方法保持一致。
    """

    @staticmethod
    def _import_ak():
        """延迟导入 akshare，避免未安装时影响其他模块加载。"""
        try:
            import akshare as ak
            return ak
        except ImportError:
            raise ImportError("akshare 未安装，请运行: pip install akshare")

    def get_a_share_quote(self, code, name):
        # type: (str, str) -> Optional[dict]
        """
        通过 AKShare 获取单只 A 股实时行情。
        返回字段与 FinanceClient.get_a_share_quote 一致。
        code: 纯数字代码，如 "600519"
        """
        try:
            ak = self._import_ak()
            # 东方财富实时行情接口：返回全市场 A 股
            df = ak.stock_zh_a_spot_em()
            # 代码列名为 "代码"
            row = df[df["代码"] == code]
            if row.empty:
                print(f"[akshare] A股 {code} 未找到行情数据")
                return None
            r = row.iloc[0]
            close = _safe_float(r.get("最新价", 0))
            prev_close = _safe_float(r.get("昨收", 0))
            change_pct = ((close - prev_close) / prev_close * 100) if prev_close else 0.0
            return {
                "symbol": code,
                "name": name or str(r.get("名称", code)),
                "timestamp": _now_iso(),
                "price": close,
                "open": _safe_float(r.get("今开", close)),
                "high": _safe_float(r.get("最高", close)),
                "low": _safe_float(r.get("最低", close)),
                "close": close,
                "volume": _safe_float(r.get("成交量", 0)),
                "change_pct": change_pct,
            }
        except Exception as e:
            print(f"[akshare] A股 {code} 行情获取失败: {e}")
            return None

    def fetch_quarterly_financials(self, symbols: list) -> list:
        """
        拉取 A 股季报/年报营收和每股现金流，用于计算 P/S 和 P/CF。
        数据频率：季度（按报告期），结果按 symbol+report_date 去重存储。

        返回 list[dict]，字段：
          symbol / report_date / total_revenue / per_share_cf / fetched_at
        """
        ak = self._import_ak()
        import math

        def _suffix(code: str) -> str:
            return code + (".SH" if code.startswith(("6", "9")) else ".SZ")

        results = []
        for symbol in symbols:
            try:
                df = ak.stock_financial_analysis_indicator_em(
                    symbol=_suffix(symbol), indicator="按报告期"
                )
                if df is None or df.empty:
                    continue

                fetched_at = dt.datetime.utcnow().isoformat()
                for _, row in df.iterrows():
                    report_date = str(row.get("REPORT_DATE", ""))[:10]
                    if not report_date:
                        continue

                    def _f(col):
                        v = row.get(col)
                        try:
                            f = float(v)
                            return None if (math.isnan(f) or math.isinf(f)) else f
                        except (TypeError, ValueError):
                            return None

                    results.append({
                        "symbol":        symbol,
                        "report_date":   report_date,
                        "total_revenue": _f("TOTALOPERATEREVE"),  # 营业总收入（元）
                        "per_share_cf":  _f("FCFF_BACK"),         # 企业自由现金流（元）
                        "fetched_at":    fetched_at,
                    })
                time.sleep(0.3)
            except Exception as e:
                print(f"[akshare] 季报 {symbol} 获取失败: {e}")

        print(f"[akshare] 季报数据获取 {len(results)} 条（{len(symbols)} 只）")
        return results

    def get_dragon_tiger(self, days=1):
        # type: (int) -> List[dict]
        """
        拉取 A 股龙虎榜数据（近 N 日），使用 ak.stock_lhb_detail_em。
        返回 list[dict]，每条包含：date / code / name / reason / buy_amt / sell_amt / net_amt
        """
        try:
            ak = self._import_ak()
            import pandas as pd
            end_date = dt.date.today()
            start_date = end_date - dt.timedelta(days=days)
            result = []
            # AKShare 接口：start_date / end_date 格式 "YYYYMMDD"
            df = ak.stock_lhb_detail_em(
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
            )
            if df is None or df.empty:
                print("[akshare] 龙虎榜：无数据返回")
                return []
            for _, row in df.iterrows():
                try:
                    # 字段名以实际接口为准，做容错映射
                    date_val = str(row.get("上榜日", row.get("date", "")))
                    code_val = str(row.get("代码", row.get("code", "")))
                    name_val = str(row.get("名称", row.get("name", "")))
                    reason_val = str(row.get("上榜原因", row.get("reason", "")))
                    buy_amt = _safe_float(row.get("买入额", row.get("buy_amt", 0)))
                    sell_amt = _safe_float(row.get("卖出额", row.get("sell_amt", 0)))
                    net_amt = buy_amt - sell_amt
                    result.append({
                        "date": date_val,
                        "code": code_val,
                        "name": name_val,
                        "reason": reason_val,
                        "buy_amt": buy_amt,
                        "sell_amt": sell_amt,
                        "net_amt": net_amt,
                    })
                except Exception as row_e:
                    print(f"[akshare] 龙虎榜行处理异常: {row_e}")
            print(f"[akshare] 龙虎榜获取 {len(result)} 条")
            return result
        except Exception as e:
            print(f"[akshare] 龙虎榜获取失败: {e}")
            return []

    def fetch_fundamentals(self, today: str | None = None) -> list:
        """
        拉取全市场 A 股基本面快照（市值 + 市净率），一次调用覆盖全部品种。
        today: YYYY-MM-DD，默认当天。
        返回 list[dict]，字段：symbol / date / market_cap / float_cap / pb / book_price / pe_ttm
        约耗时 4 分钟（东方财富分页 58 页）。
        """
        try:
            ak = self._import_ak()
            import math
            date_str = today or dt.date.today().isoformat()
            print(f"[akshare] 拉取全市场基本面快照（{date_str}），约需 4 分钟...")
            df = ak.stock_zh_a_spot_em()
            if df is None or df.empty:
                print("[akshare] fundamentals：无数据返回")
                return []

            records = []
            for _, row in df.iterrows():
                symbol = str(row.get("代码", "")).zfill(6)
                if not symbol:
                    continue

                def _f(col):
                    v = row.get(col)
                    try:
                        f = float(v)
                        return None if (math.isnan(f) or math.isinf(f)) else f
                    except (TypeError, ValueError):
                        return None

                market_cap = _f("总市值")
                float_cap  = _f("流通市值")
                pb         = _f("市净率")
                pe_ttm     = _f("市盈率-动态")
                book_price = (1.0 / pb) if (pb and pb > 0) else None

                records.append({
                    "symbol":     symbol,
                    "date":       date_str,
                    "market_cap": market_cap,
                    "float_cap":  float_cap,
                    "pb":         pb,
                    "book_price": book_price,
                    "pe_ttm":     pe_ttm,
                })

            print(f"[akshare] fundamentals 获取 {len(records)} 条")
            return records
        except Exception as e:
            print(f"[akshare] fundamentals 获取失败: {e}")
            return []

    def get_north_flow(self):
        # type: () -> Optional[float]
        """
        拉取当日北向资金净流入（沪股通+深股通合计），
        使用 ak.stock_hsgt_north_net_flow_in_em。
        返回净流入金额（亿元，float），失败时返回 None。
        """
        try:
            ak = self._import_ak()
            # 返回 DataFrame，最新一行为当日数据
            df = ak.stock_hsgt_north_net_flow_in_em(symbol="沪深港通")
            if df is None or df.empty:
                print("[akshare] 北向资金：无数据返回")
                return None
            # 取最新一行
            latest = df.iloc[-1]
            # 字段名容错：可能为 "date"+"value" 或直接是数值列
            cols = list(df.columns)
            # 找数值列（非日期列）
            val_col = None
            for c in cols:
                if c not in ("日期", "date", "Date"):
                    val_col = c
                    break
            if val_col is None:
                val_col = cols[-1]
            raw = _safe_float(latest[val_col])
            # AKShare 北向资金单位通常为亿元，若数值 > 10000 则可能为万元，自动换算
            if abs(raw) > 100000:
                raw = raw / 10000.0
            print(f"[akshare] 北向资金净流入: {raw:.2f} 亿元")
            return raw
        except Exception as e:
            print(f"[akshare] 北向资金获取失败: {e}")
            return None
