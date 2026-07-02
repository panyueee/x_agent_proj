"""tv_fetcher.py 单元测试：schema 转换 / 符号归一化 / 失败兜底。

约束：不做任何真实网络请求，底层 TvDatafeed.get_hist 全部打桩。

    .venv/bin/python -m pytest tests/test_tv_fetcher.py -v
"""
from __future__ import annotations

import os
import sys
from unittest import mock

import pandas as pd

# 让 `import x_agent.tv_fetcher` 在任意 cwd 下都能工作
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from x_agent import tv_fetcher
from x_agent.tv_fetcher import (
    KLINE_COLUMNS,
    TvClient,
    _interval_of,
    _normalize_symbol,
)


def _fake_raw(symbol: str, n: int = 3) -> pd.DataFrame:
    """仿真 tvDatafeed.get_hist 的返回结构：datetime 索引 + 6 列。"""
    idx = pd.date_range("2026-06-29 09:30:00", periods=n, freq="D", name="datetime")
    return pd.DataFrame({
        "symbol": [symbol] * n,
        "open": [10.0 + i for i in range(n)],
        "high": [11.0 + i for i in range(n)],
        "low": [9.0 + i for i in range(n)],
        "close": [10.5 + i for i in range(n)],
        "volume": [1000.7 + i for i in range(n)],
    }, index=idx)


def _client_with(raw):
    """构造一个底层 get_hist 被打桩的 TvClient。"""
    client = TvClient()
    fake_tv = mock.Mock()
    if isinstance(raw, Exception):
        fake_tv.get_hist.side_effect = raw
    else:
        fake_tv.get_hist.return_value = raw
    client._tv = fake_tv  # 跳过真实连接
    return client, fake_tv


# ---- schema 转换 ----

def test_schema_matches_stock_history_parquet():
    client, _ = _client_with(_fake_raw("SSE:600519"))
    df = client.get_klines("600519", "SSE")
    assert list(df.columns) == KLINE_COLUMNS
    assert list(df.columns) == ["date", "open", "high", "low", "close", "volume",
                                "amount", "adj_close", "symbol", "market"]
    # 日线 date 是纯日期字符串
    assert df["date"].iloc[0] == "2026-06-29"
    # volume 取整成 int64，amount/adj_close 置空
    assert str(df["volume"].dtype) == "int64"
    assert df["volume"].iloc[0] == 1001
    assert df["amount"].isna().all()
    assert df["adj_close"].isna().all()


def test_intraday_keeps_full_timestamp():
    client, fake_tv = _client_with(_fake_raw("BINANCE:BTCUSDT"))
    df = client.get_klines("BTCUSDT", "BINANCE", interval="1h")
    assert df["date"].iloc[0] == "2026-06-29 09:30:00"
    # interval 正确映射传给底层
    from tvDatafeed import Interval
    assert fake_tv.get_hist.call_args.kwargs["interval"] == Interval.in_1_hour


def test_combined_exchange_symbol_form():
    client, fake_tv = _client_with(_fake_raw("NASDAQ:AAPL"))
    df = client.get_klines("NASDAQ:AAPL")
    assert fake_tv.get_hist.call_args.kwargs["symbol"] == "AAPL"
    assert fake_tv.get_hist.call_args.kwargs["exchange"] == "NASDAQ"
    assert df["symbol"].iloc[0] == "AAPL"
    assert df["market"].iloc[0] == "us"


# ---- 符号归一化 / market 推断 ----

def test_symbol_normalization_conventions():
    assert _normalize_symbol("600519", "SSE", "a") == "sh.600519"
    assert _normalize_symbol("000858", "SZSE", "a") == "sz.000858"
    assert _normalize_symbol("700", "HKEX", "hk") == "0700.HK"
    assert _normalize_symbol("BTCUSDT", "BINANCE", "crypto") == "BTC-USD"
    assert _normalize_symbol("ETHUSD", "COINBASE", "crypto") == "ETH-USD"
    assert _normalize_symbol("AAPL", "NASDAQ", "us") == "AAPL"


def test_market_inference_per_exchange():
    for exchange, market in [("SSE", "a"), ("SZSE", "a"), ("HKEX", "hk"),
                             ("NYSE", "us"), ("BINANCE", "crypto")]:
        client, _ = _client_with(_fake_raw(f"{exchange}:X1"))
        df = client.get_klines("600519" if market == "a" else "X1", exchange)
        assert df["market"].iloc[0] == market, exchange
    # 未知交易所退化为小写交易所名
    client, _ = _client_with(_fake_raw("MOEX:SBER"))
    df = client.get_klines("SBER", "MOEX")
    assert df["market"].iloc[0] == "moex"


# ---- interval 映射 ----

def test_interval_mapping_and_case():
    from tvDatafeed import Interval
    assert _interval_of("1D") == Interval.in_daily
    assert _interval_of("1d") == Interval.in_daily
    assert _interval_of("5m") == Interval.in_5_minute
    assert _interval_of("1M") == Interval.in_monthly   # 大写 M 是月线
    assert _interval_of("1m") == Interval.in_1_minute  # 小写 m 是分钟
    try:
        _interval_of("7x")
        assert False, "应当抛 ValueError"
    except ValueError:
        pass


# ---- 失败兜底：绝不抛异常 ----

def test_failure_returns_empty_with_schema():
    client, _ = _client_with(RuntimeError("connection lost"))
    df = client.get_klines("600519", "SSE")
    assert len(df) == 0
    assert list(df.columns) == KLINE_COLUMNS


def test_none_result_returns_empty():
    client, _ = _client_with(None)
    df = client.get_klines("NOSUCH", "NASDAQ")
    assert len(df) == 0
    assert list(df.columns) == KLINE_COLUMNS


def test_missing_exchange_returns_empty_without_network():
    client, fake_tv = _client_with(_fake_raw("X"))
    df = client.get_klines("600519")  # 没给交易所且非合写
    assert len(df) == 0
    fake_tv.get_hist.assert_not_called()


def test_bad_interval_returns_empty():
    client, _ = _client_with(_fake_raw("X"))
    df = client.get_klines("AAPL", "NASDAQ", interval="7x")
    assert len(df) == 0
    assert list(df.columns) == KLINE_COLUMNS


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok - {fn.__name__}")
    print(f"\n{len(fns)} tests passed")
