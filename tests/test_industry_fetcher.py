"""industry_fetcher.py 解析逻辑回归测试。

覆盖：
  - 东方财富板块成分股解析（change_pct = f3 / 100）+ 异常返回 []
  - 新浪财经行业新闻解析 → ChainEvent
  - 巨潮资讯公告解析（session.post）+ URL 拼接 + max_results 截断

约束：不做任何真实网络请求，全部用 unittest.mock 打桩 session，喂入仿真响应体。

本文件既可用 pytest 运行：
    .venv/bin/python -m pytest tests/test_industry_fetcher.py -v
也可直接当脚本运行（无 pytest 依赖）：
    .venv/bin/python tests/test_industry_fetcher.py
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from x_agent import industry_fetcher as inf
from x_agent.industry_fetcher import (
    IndustryClient,
    ChainEvent,
    IndustryNode,
)


# ── 测试替身 ──────────────────────────────────────────────────────────────────

class _FakeResp:
    def __init__(self, json_data=None, json_exc=None):
        self._json = json_data
        self._json_exc = json_exc

    def json(self):
        if self._json_exc is not None:
            raise self._json_exc
        return self._json


class _FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []   # [("get"/"post", url, kwargs), ...]
        self.headers = {}

    def _next(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self._responses:
            raise AssertionError("session 调用次数超过预置响应数")
        return self._responses.pop(0)

    def get(self, url, **kwargs):
        return self._next("get", url, **kwargs)

    def post(self, url, **kwargs):
        return self._next("post", url, **kwargs)


def _client_with(responses):
    c = IndustryClient()
    c.session = _FakeSession(responses)
    return c


# ── 1. 板块成分股 ─────────────────────────────────────────────────────────────

def test_fetch_sector_stocks_parses_and_scales_change_pct():
    payload = {"data": {"diff": [
        {"f12": "300750", "f14": "宁德时代", "f3": 325},   # 3.25%
        {"f12": "002594", "f14": "比亚迪", "f3": -150},     # -1.50%
        {"f12": "600519", "f14": "贵州茅台", "f3": 0},      # 0
    ]}}
    c = _client_with([_FakeResp(json_data=payload)])
    rows = c.fetch_sector_stocks("BK0471")
    assert len(rows) == 3
    assert rows[0] == {"code": "300750", "name": "宁德时代", "change_pct": 3.25}
    assert rows[1]["change_pct"] == -1.5
    assert rows[2]["change_pct"] == 0.0
    # fs 参数含板块代码
    assert "b:BK0471" in c.session.calls[0][2]["params"]["fs"]


def test_fetch_sector_stocks_missing_f3_defaults_zero():
    payload = {"data": {"diff": [{"f12": "1", "f14": "X"}]}}   # 无 f3
    c = _client_with([_FakeResp(json_data=payload)])
    rows = c.fetch_sector_stocks("BK0001")
    assert rows[0]["change_pct"] == 0.0


def test_fetch_sector_stocks_null_data_returns_empty():
    c = _client_with([_FakeResp(json_data={"data": None})])
    assert c.fetch_sector_stocks("BK0001") == []


def test_fetch_sector_stocks_error_returns_empty():
    c = _client_with([_FakeResp(json_exc=ValueError("bad"))])
    assert c.fetch_sector_stocks("BK0001") == []


# ── 2. 新浪行业新闻 ───────────────────────────────────────────────────────────

def test_fetch_company_news_parses():
    payload = {"result": {"data": [{
        "title": "新能源汽车销量大增",
        "intro": "同比增长 50%",
        "url": "http://news/ev",
        "ctime": "1700000001",
    }]}}
    c = _client_with([_FakeResp(json_data=payload)])
    events = c.fetch_company_news("新能源汽车")
    assert len(events) == 1
    e = events[0]
    assert isinstance(e, ChainEvent)
    assert e.chain == "新能源汽车"
    assert e.title == "新能源汽车销量大增"
    assert e.content == "同比增长 50%"
    assert e.source == "新浪财经"
    assert e.url == "http://news/ev"
    assert e.published_at == "1700000001"
    assert e.relevance_score == 0.0
    assert c.session.calls[0][2]["params"]["k"] == "新能源汽车"


def test_fetch_company_news_empty():
    c = _client_with([_FakeResp(json_data={"result": {}})])
    assert c.fetch_company_news("X") == []


def test_fetch_company_news_error_returns_empty():
    c = _client_with([_FakeResp(json_exc=ValueError("boom"))])
    assert c.fetch_company_news("X") == []


# ── 3. 巨潮公告（POST）────────────────────────────────────────────────────────

def test_fetch_cninfo_announcements_parses_and_builds_url():
    payload = {"announcements": [
        {"announcementTitle": "年度报告", "adjunctUrl": "finalpage/2026/a.PDF",
         "announcementTime": 1700000002},
        {"announcementTitle": "股东大会决议", "adjunctUrl": "finalpage/2026/b.PDF",
         "announcementTime": 1700000003},
    ]}
    c = _client_with([_FakeResp(json_data=payload)])
    events = c.fetch_cninfo_announcements("000001")
    assert len(events) == 2
    e = events[0]
    assert e.chain == "000001"
    assert e.title == "年度报告"
    assert e.source == "巨潮资讯"
    assert e.url == "http://static.cninfo.com.cn/finalpage/2026/a.PDF"
    assert e.published_at == "1700000002"
    # 走的是 POST
    assert c.session.calls[0][0] == "post"


def test_fetch_cninfo_announcements_respects_max_results():
    payload = {"announcements": [
        {"announcementTitle": f"公告{i}", "adjunctUrl": "", "announcementTime": i}
        for i in range(10)
    ]}
    c = _client_with([_FakeResp(json_data=payload)])
    events = c.fetch_cninfo_announcements("000001", max_results=3)
    assert len(events) == 3   # items[:max_results]


def test_fetch_cninfo_announcements_null_returns_empty():
    c = _client_with([_FakeResp(json_data={"announcements": None})])
    assert c.fetch_cninfo_announcements("000001") == []


def test_fetch_cninfo_announcements_error_returns_empty():
    c = _client_with([_FakeResp(json_exc=ValueError("bad"))])
    assert c.fetch_cninfo_announcements("000001") == []


# ── 4. dataclass 字段（基本健全性） ──────────────────────────────────────────

def test_industry_node_defaults():
    n = IndustryNode(code="600519", name="贵州茅台", role="core", chain="白酒")
    assert n.notes == ""
    assert n.updated_at == ""


def test_chain_event_default_score():
    e = ChainEvent(chain="c", title="t", content="x", source="s",
                   url="u", published_at="p")
    assert e.relevance_score == 0.0


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
