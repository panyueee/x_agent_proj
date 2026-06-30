"""research_fetcher.py 解析逻辑回归测试。

覆盖：
  - 东方财富研报 JSONP（datatable(...)）解析 + 目标价 float 转换 + 异常吞掉返回 []
  - 同花顺研报 JSON 解析
  - 新浪财经供应商关联新闻解析
  - 雪球骨架方法返回 []

约束：不做任何真实网络请求，全部用 unittest.mock 打桩 session，喂入仿真响应体。

本文件既可用 pytest 运行：
    .venv/bin/python -m pytest tests/test_research_fetcher.py -v
也可直接当脚本运行（无 pytest 依赖）：
    .venv/bin/python tests/test_research_fetcher.py
"""
from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from x_agent import research_fetcher as rf
from x_agent.research_fetcher import (
    ResearchClient,
    ResearchReport,
    SupplierUpdate,
)


# ── 测试替身 ──────────────────────────────────────────────────────────────────

class _FakeResp:
    """仿 requests.Response：暴露 text / json()。"""

    def __init__(self, text="", json_data=None, json_exc=None):
        self.text = text
        self._json = json_data
        self._json_exc = json_exc

    def json(self):
        if self._json_exc is not None:
            raise self._json_exc
        return self._json


class _FakeSession:
    """按调用顺序返回预置响应，记录 get 参数。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []   # [(url, kwargs), ...]
        self.headers = {}

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self._responses:
            raise AssertionError("session.get 调用次数超过预置响应数")
        return self._responses.pop(0)


def _client_with(responses):
    c = ResearchClient()
    c.session = _FakeSession(responses)
    return c


# ── 1. 东方财富研报 JSONP 解析 ────────────────────────────────────────────────

def _em_jsonp(items):
    return "datatable(" + json.dumps({"data": items}) + ")"


def test_fetch_reports_eastmoney_parses_jsonp():
    items = [{
        "id": 100,
        "stockName": "贵州茅台",
        "title": "买入评级报告",
        "orgSName": "某券商",
        "researcher": "分析师A",
        "rating": "买入",
        "priceNow": "1800.5",
        "publishDate": "2026-06-01",
        "infoCode": "AP202606",
    }]
    c = _client_with([_FakeResp(text=_em_jsonp(items))])
    reports = c.fetch_reports_eastmoney("600519")
    assert len(reports) == 1
    r = reports[0]
    assert isinstance(r, ResearchReport)
    assert r.report_id == "100"
    assert r.stock_code == "600519"
    assert r.stock_name == "贵州茅台"
    assert r.org_name == "某券商"
    assert r.analyst == "分析师A"
    assert r.rating == "买入"
    assert r.target_price == 1800.5
    assert r.published_at == "2026-06-01"
    assert r.url == "AP202606"


def test_fetch_reports_eastmoney_target_price_invalid_to_none():
    items = [
        {"id": 1, "priceNow": ""},      # 空 → None
        {"id": 2, "priceNow": "abc"},   # 非数字 → None
        {"id": 3, "priceNow": None},    # None → None
        {"id": 4, "priceNow": "12.5"},  # 正常
    ]
    c = _client_with([_FakeResp(text=_em_jsonp(items))])
    reports = c.fetch_reports_eastmoney("000001")
    assert [r.target_price for r in reports] == [None, None, None, 12.5]


def test_fetch_reports_eastmoney_code_prefix():
    # 0/3 开头 → SZ，其余 → SH
    c = _client_with([_FakeResp(text=_em_jsonp([]))])
    c.fetch_reports_eastmoney("000001")
    assert c.session.calls[0][1]["params"]["code"] == "SZ000001"

    c2 = _client_with([_FakeResp(text=_em_jsonp([]))])
    c2.fetch_reports_eastmoney("300750")
    assert c2.session.calls[0][1]["params"]["code"] == "SZ300750"

    c3 = _client_with([_FakeResp(text=_em_jsonp([]))])
    c3.fetch_reports_eastmoney("600519")
    assert c3.session.calls[0][1]["params"]["code"] == "SH600519"


def test_fetch_reports_eastmoney_plain_json_without_jsonp_wrapper():
    # 没有 datatable( 包裹时也应能直接 json.loads
    c = _client_with([_FakeResp(text=json.dumps({"data": [{"id": 9, "title": "T"}]}))])
    reports = c.fetch_reports_eastmoney("600000")
    assert len(reports) == 1
    assert reports[0].report_id == "9"


def test_fetch_reports_eastmoney_bad_text_returns_empty():
    # 无法解析的文本 → 异常被吞 → 返回 []
    c = _client_with([_FakeResp(text="<html>error</html>")])
    assert c.fetch_reports_eastmoney("600519") == []


def test_fetch_reports_eastmoney_null_data_returns_empty():
    c = _client_with([_FakeResp(text=_em_jsonp(None))])
    assert c.fetch_reports_eastmoney("600519") == []


# ── 2. 同花顺研报 JSON 解析 ───────────────────────────────────────────────────

def test_fetch_reports_ths_parses():
    payload = {"data": {"list": [{
        "id": 55,
        "stockname": "宁德时代",
        "title": "产能扩张点评",
        "orgname": "甲证券",
        "author": "研究员B",
        "invest": "增持",
        "time": "2026-05-20",
        "pdfurl": "http://x/report.pdf",
    }]}}
    c = _client_with([_FakeResp(json_data=payload)])
    reports = c.fetch_reports_ths("300750")
    assert len(reports) == 1
    r = reports[0]
    assert r.report_id == "55"
    assert r.stock_name == "宁德时代"
    assert r.org_name == "甲证券"
    assert r.analyst == "研究员B"
    assert r.rating == "增持"
    assert r.target_price is None
    assert r.url == "http://x/report.pdf"
    # 同花顺需要覆盖 Referer
    assert c.session.calls[0][1]["headers"]["Referer"] == "https://www.10jqka.com.cn"


def test_fetch_reports_ths_empty_list():
    c = _client_with([_FakeResp(json_data={"data": {}})])
    assert c.fetch_reports_ths("300750") == []


def test_fetch_reports_ths_error_returns_empty():
    c = _client_with([_FakeResp(json_exc=ValueError("bad json"))])
    assert c.fetch_reports_ths("300750") == []


# ── 3. 雪球骨架 ───────────────────────────────────────────────────────────────

def test_fetch_xueqiu_updates_returns_empty():
    c = _client_with([])
    assert c.fetch_xueqiu_updates("SH600519") == []


# ── 4. 新浪供应商新闻 ─────────────────────────────────────────────────────────

def test_fetch_supplier_news_parses():
    payload = {"result": {"data": [{
        "title": "甲与乙签订大单",
        "intro": "供货协议达成",
        "ctime": "1700000000",
        "url": "http://news/1",
    }]}}
    c = _client_with([_FakeResp(json_data=payload)])
    updates = c.fetch_supplier_news("供应商甲", "客户乙")
    assert len(updates) == 1
    u = updates[0]
    assert isinstance(u, SupplierUpdate)
    assert u.supplier_name == "供应商甲"
    assert u.customer_name == "客户乙"
    assert u.event_type == "news"
    assert u.title == "甲与乙签订大单"
    assert u.content == "供货协议达成"
    assert u.source == "新浪财经"
    assert u.published_at == "1700000000"
    assert u.url == "http://news/1"
    # 关键词拼接为 "供应商甲 客户乙"
    assert c.session.calls[0][1]["params"]["k"] == "供应商甲 客户乙"


def test_fetch_supplier_news_empty_data():
    c = _client_with([_FakeResp(json_data={"result": {}})])
    assert c.fetch_supplier_news("甲", "乙") == []


def test_fetch_supplier_news_error_returns_empty():
    c = _client_with([_FakeResp(json_exc=ValueError("boom"))])
    assert c.fetch_supplier_news("甲", "乙") == []


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
