"""
finance_fetcher.py 解析逻辑回归测试。

本模块刚做过性能优化，本套测试用于验证「优化未改变行为」：
  - 复用同一 requests.Session（~7 个接口共享连接池）
  - 模块级 _safe_float_or_none(row, col) 取代逐行闭包 _f(col)
  - 预编译新浪行情正则 _SINA_LINE_RE
  - 顶层 import math（_safe_float 依赖 math.isnan/isinf）

约束：不做任何真实网络请求，全部用 unittest.mock 打桩 Session，喂入仿真响应体。

本文件既可用 pytest 运行：
    python -m pytest tests/test_finance_fetcher.py -v
也可直接当脚本运行（无 pytest 依赖）：
    python tests/test_finance_fetcher.py
"""
from __future__ import annotations

import os
import sys
from unittest import mock

# 让 `import x_agent.finance_fetcher` 在任意 cwd 下都能工作
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from x_agent import finance_fetcher as ff
from x_agent.finance_fetcher import (
    FinanceClient,
    PriceBar,
    _SINA_LINE_RE,
    _safe_float,
    _safe_float_or_none,
)


# ── 测试替身 ──────────────────────────────────────────────────────────────────

class _FakeResp:
    """仿 requests.Response：暴露 text / encoding / json()。"""

    def __init__(self, text="", json_data=None):
        self.text = text
        self._json = json_data
        self.encoding = None  # 代码会对其赋值（resp.encoding = "gbk"）

    def json(self):
        return self._json


class _FakeSession:
    """按调用顺序依次返回预置响应，并记录每次 get 的参数。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []  # [(url, headers, timeout), ...]

    def get(self, url, headers=None, timeout=None):
        self.calls.append((url, headers, timeout))
        if not self._responses:
            raise AssertionError("session.get 调用次数超过预置响应数")
        return self._responses.pop(0)


def _client_with(responses):
    """构造 FinanceClient 并替换其内部 session 为预置响应的假 session。"""
    c = FinanceClient()
    c._session = _FakeSession(responses)
    return c


# ── 1. _safe_float：失败回退 0.0（或自定义 default）───────────────────────────

def test_safe_float_valid_numbers():
    assert _safe_float("1725.50") == 1725.50
    assert _safe_float("0") == 0.0
    assert _safe_float("-3.5") == -3.5
    assert _safe_float(42) == 42.0
    assert _safe_float(3.14) == 3.14


def test_safe_float_invalid_returns_default_zero():
    assert _safe_float("") == 0.0
    assert _safe_float("-") == 0.0
    assert _safe_float(None) == 0.0
    assert _safe_float("None") == 0.0
    assert _safe_float("abc") == 0.0


def test_safe_float_nan_and_inf_return_default():
    assert _safe_float(float("nan")) == 0.0
    assert _safe_float(float("inf")) == 0.0
    assert _safe_float(float("-inf")) == 0.0
    assert _safe_float("nan") == 0.0   # float("nan") 合法但 isnan → default


def test_safe_float_custom_default():
    assert _safe_float("abc", default=-1.0) == -1.0
    assert _safe_float(None, default=99.0) == 99.0
    assert _safe_float(float("nan"), default=-1.0) == -1.0


# ── 2. _safe_float_or_none：失败回退 None ─────────────────────────────────────

def test_safe_float_or_none_valid():
    row = {"col": "123.4", "ival": 7}
    assert _safe_float_or_none(row, "col") == 123.4
    assert _safe_float_or_none(row, "ival") == 7.0


def test_safe_float_or_none_invalid_returns_none():
    row = {"empty": "", "dash": "-", "txt": "abc", "nonestr": "None", "nullv": None}
    assert _safe_float_or_none(row, "empty") is None
    assert _safe_float_or_none(row, "dash") is None
    assert _safe_float_or_none(row, "txt") is None
    assert _safe_float_or_none(row, "nonestr") is None
    assert _safe_float_or_none(row, "nullv") is None


def test_safe_float_or_none_missing_column():
    # 列不存在 → row.get 返回 None → None
    assert _safe_float_or_none({}, "missing") is None


def test_safe_float_or_none_nan_inf():
    row = {"nan": float("nan"), "inf": float("inf"), "ninf": float("-inf")}
    assert _safe_float_or_none(row, "nan") is None
    assert _safe_float_or_none(row, "inf") is None
    assert _safe_float_or_none(row, "ninf") is None


# ── 3. 新浪 A 股行情解析（_SINA_LINE_RE + fetch_a_shares）─────────────────────

def test_sina_regex_matches_sh_and_sz():
    sh = 'var hq_str_sh600519="贵州茅台,1700,1690,1725.5";'
    sz = 'var hq_str_sz000858="五粮液,150,149,151.2";'
    m_sh = _SINA_LINE_RE.search(sh)
    m_sz = _SINA_LINE_RE.search(sz)
    assert m_sh and m_sh.group(1) == "600519"
    assert m_sh.group(2).split(",")[0] == "贵州茅台"
    assert m_sz and m_sz.group(1) == "000858"


def test_sina_regex_skips_non_quote_lines():
    assert _SINA_LINE_RE.search("/* comment */") is None
    assert _SINA_LINE_RE.search('var something_else="x";') is None


def _sina_body():
    """仿真新浪 A 股行情响应（真实字段顺序）。

    fields: name, open(今开), prev_close(昨收), price(当前), high, low,
            竞买, 竞卖, volume(股), amount(元), ...
    """
    return (
        'var hq_str_sh600519="贵州茅台,1700.00,1690.00,1725.50,1730.00,'
        '1680.00,1725.00,1726.00,1234500,2100000000.00,0";\n'
        'var hq_str_sz000858="五粮液,150.00,149.00,151.20,152.00,'
        '148.50,151.10,151.20,987600,15000000.00,0";\n'
    )


def test_fetch_a_shares_parses_fields():
    c = _client_with([_FakeResp(text=_sina_body())])
    bars = c.fetch_a_shares(["600519", "000858"])

    assert len(bars) == 2
    b = bars[0]
    assert isinstance(b, PriceBar)
    assert b.symbol == "600519"
    assert b.market == "a_shares"
    assert b.name == "贵州茅台"
    assert b.open == 1700.00
    assert b.high == 1730.00
    assert b.low == 1680.00
    assert b.close == 1725.50          # fields[3] = 当前价
    assert b.volume == 1234500 * 100   # fields[8] 手→股 ×100
    # change_pct = (close - prev_close) / prev_close * 100
    assert b.change_pct == ((1725.50 - 1690.00) / 1690.00 * 100)

    # 验证请求：拼出 sh/sz 前缀 + 设置 gbk 编码 + Referer 头
    url, headers, timeout = c._session.calls[0]
    assert "list=sh600519,sz000858" in url
    assert headers == ff._SINA_HEADERS


def test_fetch_a_shares_name_override():
    c = _client_with([_FakeResp(text=_sina_body())])
    bars = c.fetch_a_shares(["600519", "000858"], ["茅台", "五粮液"])
    # names 提供时优先使用映射名
    assert bars[0].name == "茅台"


def test_fetch_a_shares_skips_short_field_lines():
    # 退市/停牌返回空字符串 → split 后字段数 < 9 → 跳过该行
    body = 'var hq_str_sh600519="";\nvar hq_str_sz000858="五粮液,1,2,3";\n'
    c = _client_with([_FakeResp(text=body)])
    bars = c.fetch_a_shares(["600519", "000858"])
    assert bars == []


def test_fetch_a_shares_zero_prev_close_no_div_error():
    # 昨收为 0 → change_pct 应安全置 0.0，不抛除零
    body = ('var hq_str_sh600519="测试,10,0,12,13,9,12,12,1000,0,0";\n')
    c = _client_with([_FakeResp(text=body)])
    bars = c.fetch_a_shares(["600519"])
    assert len(bars) == 1
    assert bars[0].change_pct == 0.0


# ── 4. 东方财富美股行情解析（_fetch_us_one）───────────────────────────────────

def _emc_us_json(f43=201500, name="Apple Inc"):
    """东方财富个股快照 JSON：价格×1000、涨跌幅×100。"""
    return {"data": {
        "f43": f43,        # close ×1000
        "f44": 203000,     # high  ×1000
        "f45": 200000,     # low   ×1000
        "f46": 202000,     # open  ×1000
        "f57": "AAPL",
        "f58": name,
        "f170": 125,       # change_pct ×100 → 1.25
        "f47": 50000000,   # volume
    }}


def test_fetch_us_one_parses_and_scales():
    c = _client_with([_FakeResp(json_data=_emc_us_json())])
    bars = c.fetch_us_stocks(["AAPL"])
    assert len(bars) == 1
    b = bars[0]
    assert b.symbol == "AAPL"
    assert b.market == "us_stocks"
    assert b.name == "Apple Inc"
    assert b.close == 201.5    # 201500 / 1000
    assert b.high == 203.0
    assert b.low == 200.0
    assert b.open == 202.0
    assert b.change_pct == 1.25  # 125 / 100
    assert b.volume == 50000000
    # 首个 secid 应为 105.AAPL
    assert "secid=105.AAPL" in c._session.calls[0][0]


def test_fetch_us_one_fallback_to_106():
    # 105 返回无效（f43="-"）→ 应回退尝试 106
    bad = _FakeResp(json_data={"data": {"f43": "-"}})
    good = _FakeResp(json_data=_emc_us_json())
    c = _client_with([bad, good])
    bars = c.fetch_us_stocks(["MSFT"])
    assert len(bars) == 1
    assert bars[0].close == 201.5
    assert "secid=105.MSFT" in c._session.calls[0][0]
    assert "secid=106.MSFT" in c._session.calls[1][0]


def test_fetch_us_one_all_markets_fail_returns_empty():
    bad = _FakeResp(json_data={"data": {}})
    c = _client_with([bad, bad, bad])  # 105/106/107 全失败
    bars = c.fetch_us_stocks(["XXXX"])
    assert bars == []
    assert len(c._session.calls) == 3


# ── 5. gate.io 加密货币行情 + K线 ─────────────────────────────────────────────

def test_fetch_crypto_parses_ticker():
    ticker = [{
        "last": "65000.5",
        "open_24h": "64000",
        "high_24h": "66000",
        "low_24h": "63000",
        "base_volume": "1234.5",
    }]
    c = _client_with([_FakeResp(json_data=ticker)])
    bars = c.fetch_crypto(["BTC/USDT"])
    assert len(bars) == 1
    b = bars[0]
    assert b.symbol == "BTC/USDT"
    assert b.market == "crypto"
    assert b.close == 65000.5
    assert b.open == 64000.0
    assert b.high == 66000.0
    assert b.low == 63000.0
    assert b.volume == 1234.5
    assert b.change_pct == ((65000.5 - 64000.0) / 64000.0 * 100)
    # gate.io 格式：BTC/USDT → BTC_USDT
    assert "currency_pair=BTC_USDT" in c._session.calls[0][0]


def test_fetch_crypto_empty_data_skipped():
    c = _client_with([_FakeResp(json_data=[])])
    assert c.fetch_crypto(["BTC/USDT"]) == []


def test_kline_crypto_parses_candles():
    # gate.io candlesticks: [ts, volume, close, high, low, open]
    raw = [
        ["1700000000", "100.5", "65000", "66000", "64000", "64500"],
        ["1700086400", "200.0", "66000", "67000", "65000", "65000"],
    ]
    c = _client_with([_FakeResp(json_data=raw)])
    bars = c.fetch_kline("BTC/USDT", "crypto", days=2)
    assert len(bars) == 2
    b0 = bars[0]
    assert b0.timestamp == "2023-11-14T22:13:20Z"
    assert b0.volume == 100.5
    assert b0.close == 65000.0
    assert b0.high == 66000.0
    assert b0.low == 64000.0
    assert b0.open == 64500.0
    assert b0.change_pct == 0.0  # 首根无 prev_close
    # 第二根涨跌幅基于前一根 close
    assert bars[1].change_pct == ((66000.0 - 65000.0) / 65000.0 * 100)


# ── 6. 东方财富指数行情（fetch_indices）───────────────────────────────────────

def test_fetch_indices_parses():
    cfg = [{"symbol": "HSI", "name": "恒生指数", "secid": "100.HSI"}]
    json_data = {"data": {
        "f43": 23456000,  # 点位 ×1000 → 23456.0
        "f44": 23500000,
        "f45": 23400000,
        "f46": 23450000,
        "f58": "恒生指数",
        "f170": 150,      # +1.5%
        "f47": 0,
    }}
    c = _client_with([_FakeResp(json_data=json_data)])
    bars = c.fetch_indices(cfg)
    assert len(bars) == 1
    b = bars[0]
    assert b.symbol == "HSI"
    assert b.market == "index"
    assert b.name == "恒生指数"
    assert b.close == 23456.0
    assert b.change_pct == 1.5
    assert "secid=100.HSI" in c._session.calls[0][0]


def test_fetch_indices_skips_missing_secid():
    # 缺 secid → 跳过，且不发请求
    c = _client_with([])  # 无响应；若发请求会 AssertionError
    bars = c.fetch_indices([{"symbol": "X", "name": "无secid"}])
    assert bars == []
    assert c._session.calls == []


def test_fetch_indices_skips_zero_f43():
    # f43 为 0 → 视为无数据跳过
    cfg = [{"symbol": "HSI", "secid": "100.HSI"}]
    c = _client_with([_FakeResp(json_data={"data": {"f43": 0}})])
    assert c.fetch_indices(cfg) == []


# ── 7. Session 复用（优化点本身）─────────────────────────────────────────────

def test_session_created_once_per_client():
    with mock.patch.object(ff.requests, "Session") as MS:
        FinanceClient()
        assert MS.call_count == 1


def test_session_reused_across_multiple_fetches():
    # 同一 client 的多次抓取应复用同一个 session 对象
    c = _client_with([
        _FakeResp(text=_sina_body()),
        _FakeResp(json_data=_emc_us_json()),
    ])
    session_obj = c._session
    c.fetch_a_shares(["600519", "000858"])
    c.fetch_us_stocks(["AAPL"])
    assert c._session is session_obj
    assert len(session_obj.calls) == 2  # 两次抓取共用一个 session


def test_fetch_kline_unknown_market_returns_empty():
    c = _client_with([])
    assert c.fetch_kline("X", "forex") == []


# ── 独立运行入口（无 pytest 时） ──────────────────────────────────────────────

def _run_standalone() -> int:
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {fn.__name__}: {e}")
            failed += 1
        except Exception as e:  # noqa: BLE001
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed (total {passed + failed})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_standalone())
