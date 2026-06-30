"""
weread_fetcher.py 解析逻辑回归测试。

测试范围（纯逻辑，不做任何真实网络请求）：
  - 预编译正则 _TAG_RE / _BLANKLINE_RE
  - WeReadClient.__init__：Cookie 来源优先级 + 缺失时报错
  - get_shelf / get_chapters / get_chapter_content：响应 JSON → 解析结构
  - _get：errcode 非 0 抛错 / HTTP 重试逻辑（time.sleep 打桩）

约束：用 unittest.mock 打桩 self._get 或 self._session，喂入仿真响应体。

本文件既可用 pytest 运行：
    python -m pytest tests/test_weread_fetcher.py -v
也可直接当脚本运行：
    python tests/test_weread_fetcher.py
"""
from __future__ import annotations

import os
import sys
from unittest import mock

# 让 `import x_agent.weread_fetcher` 在任意 cwd 下都能工作
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from x_agent import weread_fetcher as wr
from x_agent.weread_fetcher import WeReadClient, _TAG_RE, _BLANKLINE_RE


# ── 测试替身 ──────────────────────────────────────────────────────────────────

class _FakeResp:
    """仿 requests.Response：暴露 json() / raise_for_status()。"""

    def __init__(self, json_data=None, status_ok=True):
        self._json = json_data if json_data is not None else {}
        self._status_ok = status_ok

    def json(self):
        return self._json

    def raise_for_status(self):
        if not self._status_ok:
            raise RuntimeError("HTTP error")


class _FakeSession:
    """按调用顺序返回预置响应，并记录每次 get 的参数。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        if not self._responses:
            raise AssertionError("session.get 调用次数超过预置响应数")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _client(cookie="wr_skey=abc"):
    """构造 WeReadClient（不触发网络），用于挂载假 session / 假 _get。"""
    return WeReadClient(cookie=cookie)


# ── 1. 预编译正则 ──────────────────────────────────────────────────────────────

def test_tag_re_strips_html_tags():
    assert _TAG_RE.sub("", "<p>你好<b>世界</b></p>") == "你好世界"
    assert _TAG_RE.sub("", "<br/>line") == "line"


def test_blankline_re_collapses():
    assert _BLANKLINE_RE.sub("\n\n", "a\n\n\n\nb") == "a\n\nb"
    # 两个换行不受影响
    assert _BLANKLINE_RE.sub("\n\n", "a\n\nb") == "a\n\nb"


# ── 2. __init__ Cookie 来源 ────────────────────────────────────────────────────

def test_init_cookie_argument():
    c = WeReadClient(cookie="wr_skey=xxx")
    assert c._session.headers["Cookie"] == "wr_skey=xxx"
    assert "User-Agent" in c._session.headers


def test_init_cookie_from_cfg():
    c = WeReadClient(cfg={"weread": {"cookie": "wr_skey=fromcfg"}})
    assert c._session.headers["Cookie"] == "wr_skey=fromcfg"


def test_init_cookie_from_env():
    with mock.patch.dict(os.environ, {"WEREAD_COOKIE": "wr_skey=fromenv"}):
        c = WeReadClient()
        assert c._session.headers["Cookie"] == "wr_skey=fromenv"


def test_init_no_cookie_raises():
    import pytest
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("WEREAD_COOKIE", None)
        with pytest.raises(ValueError):
            WeReadClient()


def test_init_argument_priority_over_env():
    with mock.patch.dict(os.environ, {"WEREAD_COOKIE": "env"}):
        c = WeReadClient(cookie="arg")
        assert c._session.headers["Cookie"] == "arg"


# ── 3. get_shelf ───────────────────────────────────────────────────────────────

def test_get_shelf_parses_bookinfo():
    payload = {"books": [
        {"bookInfo": {
            "bookId": "b1", "title": "穷查理宝典", "author": "芒格",
            "cover": "http://c", "categoryEnglishName": "Investment",
            "wordCount": 123456,
        }},
    ]}
    c = _client()
    with mock.patch.object(c, "_get", return_value=payload) as mg:
        shelf = c.get_shelf()
    assert len(shelf) == 1
    b = shelf[0]
    assert b == {
        "book_id": "b1", "title": "穷查理宝典", "author": "芒格",
        "cover": "http://c", "category": "Investment", "word_count": 123456,
    }
    # 验证调用了正确端点
    mg.assert_called_once()
    assert mg.call_args[0][0] == "/shelf/sync"


def test_get_shelf_book_without_bookinfo_wrapper():
    # 部分书目直接平铺字段（无 bookInfo 包裹）→ 用 b 自身
    payload = {"books": [{"bookId": "b2", "title": "原则", "author": "达利欧"}]}
    c = _client()
    with mock.patch.object(c, "_get", return_value=payload):
        shelf = c.get_shelf()
    assert shelf[0]["book_id"] == "b2"
    assert shelf[0]["title"] == "原则"
    assert shelf[0]["word_count"] == 0  # 缺失字段回退默认


def test_get_shelf_empty():
    c = _client()
    with mock.patch.object(c, "_get", return_value={}):
        assert c.get_shelf() == []


# ── 4. get_chapters ────────────────────────────────────────────────────────────

def test_get_chapters_parses():
    payload = {"data": [{"updated": [
        {"chapterUid": 1, "title": "第一章", "level": 1},
        {"chapterUid": 2, "title": "第二节", "level": 2},
    ]}]}
    c = _client()
    with mock.patch.object(c, "_get", return_value=payload) as mg:
        chapters = c.get_chapters("b1")
    assert chapters == [
        {"chapter_uid": 1, "title": "第一章", "level": 1},
        {"chapter_uid": 2, "title": "第二节", "level": 2},
    ]
    assert mg.call_args[0][0] == "/book/chapterInfos"
    # bookIds 参数为 JSON 数组字符串
    params = mg.call_args[0][1]
    assert params["bookIds"] == '["b1"]'


def test_get_chapters_missing_level_defaults_1():
    payload = {"data": [{"updated": [{"chapterUid": 9, "title": "无level"}]}]}
    c = _client()
    with mock.patch.object(c, "_get", return_value=payload):
        chapters = c.get_chapters("b1")
    assert chapters[0]["level"] == 1


def test_get_chapters_empty_data():
    c = _client()
    with mock.patch.object(c, "_get", return_value={"data": []}):
        assert c.get_chapters("b1") == []


# ── 5. get_chapter_content ─────────────────────────────────────────────────────

def test_get_chapter_content_strips_html_and_entities():
    html = "<p>价值&nbsp;投资&amp;成长</p>\n\n\n\n<p>5&lt;10&gt;3</p>"
    c = _client()
    with mock.patch.object(c, "_get", return_value={"chapterContentHtml": html}):
        text = c.get_chapter_content("b1", 1)
    assert "<p>" not in text
    assert "价值 投资&成长" in text
    assert "5<10>3" in text
    assert "\n\n\n" not in text  # 连续空行被压缩


def test_get_chapter_content_fallback_str_field():
    # 无 chapterContentHtml 时使用 chapterContentStr
    c = _client()
    with mock.patch.object(c, "_get", return_value={"chapterContentStr": "<b>纯文本</b>"}):
        assert c.get_chapter_content("b1", 1) == "纯文本"


def test_get_chapter_content_empty_returns_empty():
    c = _client()
    with mock.patch.object(c, "_get", return_value={}):
        assert c.get_chapter_content("b1", 1) == ""


# ── 6. _get：errcode / 重试 ────────────────────────────────────────────────────

def test_get_success():
    c = _client()
    c._session = _FakeSession([_FakeResp({"errcode": 0, "books": []})])
    data = c._get("/shelf/sync", {"x": 1})
    assert data == {"errcode": 0, "books": []}
    assert c._session.calls[0][0].endswith("/shelf/sync")
    assert c._session.calls[0][1] == {"x": 1}


def test_get_errcode_nonzero_raises_after_retries():
    import pytest
    c = _client()
    # 三次（retries=2 → 共 3 次尝试）都返回 errcode!=0
    c._session = _FakeSession([
        _FakeResp({"errcode": -2012, "errmsg": "登录超时"}) for _ in range(3)
    ])
    with mock.patch.object(wr.time, "sleep"):
        with pytest.raises(RuntimeError):
            c._get("/shelf/sync")
    assert len(c._session.calls) == 3


def test_get_retries_then_succeeds():
    c = _client()
    # 第一次抛网络错，第二次成功 → 重试后拿到数据
    c._session = _FakeSession([
        ConnectionError("boom"),
        _FakeResp({"errcode": 0, "ok": True}),
    ])
    with mock.patch.object(wr.time, "sleep"):
        data = c._get("/book/read")
    assert data == {"errcode": 0, "ok": True}
    assert len(c._session.calls) == 2


def test_get_errcode_none_is_ok():
    # errcode 缺失（None）视为成功
    c = _client()
    c._session = _FakeSession([_FakeResp({"data": [1, 2, 3]})])
    data = c._get("/book/chapterInfos")
    assert data == {"data": [1, 2, 3]}


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
