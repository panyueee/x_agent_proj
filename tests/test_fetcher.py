"""
fetcher.py X 数据抓取层解析逻辑回归测试。

覆盖：
  - Tweet dataclass 默认值
  - OfficialXClient：构造校验、resolve_user 缓存、_tweet_from、
    user_tweets / search 响应解析（mock _get）、_get 的 4xx/429 处理（mock session）
  - ThirdPartyXClient：_parse_created_at 多格式、_tweet_from_raw 字段映射、
    _is_after 时间过滤、user_tweets 跳过转发 + 不活跃标记、search
  - build_client 工厂派发

约束：不做任何真实网络请求，全部用 unittest.mock 打桩。

可用 pytest 运行：
    python -m pytest tests/test_fetcher.py -v
也可直接当脚本运行（无 pytest 依赖）：
    python tests/test_fetcher.py
"""
from __future__ import annotations

import datetime as dt
import os
import sys
from unittest import mock

import pytest

# 让 `import x_agent.fetcher` 在任意 cwd 下都能工作
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from x_agent import fetcher as fx
from x_agent.fetcher import (
    Tweet,
    OfficialXClient,
    ThirdPartyXClient,
    XClientError,
    build_client,
)


# ── 测试替身 ──────────────────────────────────────────────────────────────────

class _FakeResp:
    """仿 requests.Response：status_code / headers / text / json()。"""

    def __init__(self, status_code=200, json_data=None, text="", headers=None):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._json


class _FakeSession:
    """仿 requests.Session：headers dict + 按序返回预置响应。"""

    def __init__(self, responses=None):
        self.headers = {}
        self._responses = list(responses or [])
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        if not self._responses:
            raise AssertionError("session.get 超出预置响应数")
        return self._responses.pop(0)


def _official(token="tok", responses=None):
    c = OfficialXClient(token, min_interval=0)
    c.session = _FakeSession(responses)
    return c


def _thirdparty(responses=None):
    c = ThirdPartyXClient("https://api.example.io/", "key", min_interval=0)
    c.session = _FakeSession(responses)
    return c


# ── 1. Tweet dataclass ────────────────────────────────────────────────────────

def test_tweet_defaults():
    t = Tweet(id="1", author="a", author_id="2", text="hi",
              created_at="2024-01-01T00:00:00Z", url="http://x/1")
    assert t.metrics == {}
    assert t.source_label == ""
    assert t.group_tag == ""
    # 每个实例独立的 dict（default_factory）
    t.metrics["k"] = 1
    t2 = Tweet(id="x", author="", author_id="", text="", created_at="", url="")
    assert t2.metrics == {}


# ── 2. OfficialXClient 构造与鉴权 ─────────────────────────────────────────────

def test_official_missing_token_raises():
    with pytest.raises(XClientError):
        OfficialXClient("")


def test_official_sets_bearer_header():
    # 不替换 session：验证构造函数在真实 requests.Session 上写入鉴权头
    c = OfficialXClient("mytoken", min_interval=0)
    assert c.session.headers["Authorization"] == "Bearer mytoken"


# ── 3. OfficialXClient.resolve_user 缓存 ──────────────────────────────────────

def test_resolve_user_caches_and_strips_at():
    c = _official()
    with mock.patch.object(c, "_get", return_value={"data": {"id": "999"}}) as m:
        uid = c.resolve_user("@alice")
        assert uid == "999"
        # 第二次命中缓存，不再请求
        uid2 = c.resolve_user("alice")
        assert uid2 == "999"
        assert m.call_count == 1


def test_resolve_user_not_found_raises():
    c = _official()
    with mock.patch.object(c, "_get", return_value={"data": {}}):
        with pytest.raises(XClientError):
            c.resolve_user("ghost")


# ── 4. OfficialXClient._tweet_from ────────────────────────────────────────────

def test_official_tweet_from_with_author():
    raw = {
        "id": "123",
        "text": "hello world",
        "created_at": "2024-01-02T03:04:05.000Z",
        "public_metrics": {"like_count": 5},
    }
    t = OfficialXClient._tweet_from(raw, author="bob", author_id="7", label="@bob")
    assert t.id == "123"
    assert t.author == "bob"
    assert t.author_id == "7"
    assert t.text == "hello world"
    assert t.url == "https://x.com/bob/status/123"
    assert t.metrics == {"like_count": 5}
    assert t.source_label == "@bob"


def test_official_tweet_from_without_author_uses_i_status():
    raw = {"id": "456"}
    t = OfficialXClient._tweet_from(raw, author="", author_id="", label="kw")
    assert t.url == "https://x.com/i/status/456"
    assert t.text == ""             # text 缺省
    assert t.metrics == {}


# ── 5. OfficialXClient.user_tweets / search 解析 ──────────────────────────────

def test_official_user_tweets_parses(monkeypatch):
    c = _official()
    payload = {"data": [
        {"id": "1", "text": "t1", "created_at": "2024-01-01T00:00:00.000Z",
         "public_metrics": {"like_count": 2}},
        {"id": "2", "text": "t2", "created_at": "2024-01-01T01:00:00.000Z",
         "public_metrics": {}},
    ]}
    with mock.patch.object(c, "resolve_user", return_value="uid42"), \
         mock.patch.object(c, "_get", return_value=payload) as mget:
        since = dt.datetime(2024, 1, 1)
        out = c.user_tweets("alice", max_results=10, since=since)
    assert [t.id for t in out] == ["1", "2"]
    assert all(t.author == "alice" and t.author_id == "uid42" for t in out)
    assert out[0].url == "https://x.com/alice/status/1"
    # 路径走 /users/{uid}/tweets
    path = mget.call_args[0][0]
    assert path == "/users/uid42/tweets"


def test_official_user_tweets_clamps_max_results():
    c = _official()
    with mock.patch.object(c, "resolve_user", return_value="u"), \
         mock.patch.object(c, "_get", return_value={"data": []}) as mget:
        c.user_tweets("a", max_results=3, since=dt.datetime(2024, 1, 1))
        params = mget.call_args[0][1]
        assert params["max_results"] == 5      # min 下限 5
        c.user_tweets("a", max_results=500, since=dt.datetime(2024, 1, 1))
        assert mget.call_args[0][1]["max_results"] == 100   # max 上限 100


def test_official_search_maps_author_from_includes():
    c = _official()
    payload = {
        "data": [
            {"id": "10", "text": "match", "author_id": "u1",
             "created_at": "2024-01-01T00:00:00.000Z"},
            {"id": "11", "text": "nouser", "author_id": "u_unknown",
             "created_at": "2024-01-01T00:00:00.000Z"},
        ],
        "includes": {"users": [{"id": "u1", "username": "carol"}]},
    }
    with mock.patch.object(c, "_get", return_value=payload):
        out = c.search("q", max_results=10, since=dt.datetime(2024, 1, 1), label="kw")
    assert out[0].author == "carol"
    assert out[0].url == "https://x.com/carol/status/10"
    # 未在 includes 中的作者 → 空字符串 + i/status URL
    assert out[1].author == ""
    assert out[1].url == "https://x.com/i/status/11"
    assert all(t.source_label == "kw" for t in out)


# ── 6. OfficialXClient._get 错误/限流处理（mock session）─────────────────────

def test_official_get_returns_json_on_200():
    c = _official(responses=[_FakeResp(200, json_data={"ok": 1})])
    assert c._get("/x", {}) == {"ok": 1}


def test_official_get_raises_on_4xx():
    c = _official(responses=[_FakeResp(403, text="forbidden")])
    with pytest.raises(XClientError) as ei:
        c._get("/x", {})
    assert "HTTP 403" in str(ei.value)


def test_official_get_retries_on_429_then_succeeds():
    c = _official(responses=[
        _FakeResp(429, headers={"x-rate-limit-reset": "0"}),
        _FakeResp(200, json_data={"done": True}),
    ])
    with mock.patch.object(fx.time, "sleep") as msleep:
        result = c._get("/x", {})
    assert result == {"done": True}
    assert len(c.session.calls) == 2
    assert msleep.called           # 限流时确有等待


# ── 7. ThirdPartyXClient 构造 ─────────────────────────────────────────────────

def test_thirdparty_missing_args_raise():
    with pytest.raises(XClientError):
        ThirdPartyXClient("", "key")
    with pytest.raises(XClientError):
        ThirdPartyXClient("https://x", "")


def test_thirdparty_sets_api_key_header_and_strips_slash():
    c = ThirdPartyXClient("https://api.example.io/", "secret")
    assert c.base_url == "https://api.example.io"
    assert c.session.headers["X-API-Key"] == "secret"


# ── 8. _parse_created_at 多格式归一 ───────────────────────────────────────────

def test_parse_created_at_twitterapi_format():
    out = ThirdPartyXClient._parse_created_at("Mon Jan 01 00:00:00 +0000 2024")
    assert out == "2024-01-01T00:00:00Z"


def test_parse_created_at_iso_with_millis():
    assert ThirdPartyXClient._parse_created_at("2024-03-15T12:30:45.123Z") == "2024-03-15T12:30:45Z"


def test_parse_created_at_iso_plain():
    assert ThirdPartyXClient._parse_created_at("2024-03-15T12:30:45Z") == "2024-03-15T12:30:45Z"


def test_parse_created_at_empty_and_unparseable():
    assert ThirdPartyXClient._parse_created_at("") == ""
    assert ThirdPartyXClient._parse_created_at("garbage time") == "garbage time"


# ── 9. _tweet_from_raw 字段映射 ───────────────────────────────────────────────

def test_tweet_from_raw_twitterapi_fields():
    raw = {
        "id": 12345,
        "text": "原始推文",
        "createdAt": "Mon Jan 01 00:00:00 +0000 2024",
        "author": {"id": "u9", "userName": "dave"},
        "likeCount": 11,
        "retweetCount": 4,
        "replyCount": 2,
        "viewCount": 999,
    }
    t = ThirdPartyXClient._tweet_from_raw(raw, label="@dave")
    assert t.id == "12345"             # int -> str
    assert t.author == "dave"
    assert t.author_id == "u9"
    assert t.text == "原始推文"
    assert t.created_at == "2024-01-01T00:00:00Z"
    assert t.url == "https://x.com/dave/status/12345"
    assert t.metrics == {"like_count": 11, "retweet_count": 4,
                         "reply_count": 2, "view_count": 999}
    assert t.source_label == "@dave"


def test_tweet_from_raw_fallback_fields():
    # 备用字段名（旧版/其它供应商）：username / full_text / *_count / created_at
    raw = {
        "tweet_id": "777",
        "full_text": "fallback text",
        "created_at": "2024-05-05T10:00:00Z",
        "author": {"id": "a1", "username": "erin"},
        "favorite_count": 8,
        "retweet_count": 1,
        "reply_count": 0,
        "views": 50,
    }
    t = ThirdPartyXClient._tweet_from_raw(raw, label="lbl")
    assert t.id == "777"
    assert t.author == "erin"
    assert t.text == "fallback text"
    assert t.metrics["like_count"] == 8
    assert t.metrics["view_count"] == 50


def test_tweet_from_raw_no_author_uses_i_status():
    raw = {"id": "5", "text": "x"}
    t = ThirdPartyXClient._tweet_from_raw(raw, label="l")
    assert t.author == ""
    assert t.url == "https://x.com/i/status/5"


def test_tweet_from_raw_prefers_explicit_url():
    raw = {"id": "5", "url": "https://x.com/somebody/status/5", "author": {"userName": "z"}}
    t = ThirdPartyXClient._tweet_from_raw(raw, label="l")
    assert t.url == "https://x.com/somebody/status/5"


# ── 10. _is_after 时间过滤 ────────────────────────────────────────────────────

def test_is_after_filters_by_since():
    c = _thirdparty()
    since = dt.datetime(2024, 1, 10)
    newer = Tweet(id="1", author="", author_id="", text="",
                  created_at="2024-01-15T00:00:00Z", url="")
    older = Tweet(id="2", author="", author_id="", text="",
                  created_at="2024-01-05T00:00:00Z", url="")
    assert c._is_after(newer, since) is True
    assert c._is_after(older, since) is False


def test_is_after_keeps_when_no_timestamp_or_unparseable():
    c = _thirdparty()
    since = dt.datetime(2024, 1, 10)
    empty = Tweet(id="1", author="", author_id="", text="", created_at="", url="")
    bad = Tweet(id="2", author="", author_id="", text="", created_at="not-a-date", url="")
    assert c._is_after(empty, since) is True
    assert c._is_after(bad, since) is True


# ── 11. ThirdPartyXClient.user_tweets ─────────────────────────────────────────

def test_thirdparty_user_tweets_skips_retweets_and_filters():
    c = _thirdparty()
    since = dt.datetime(2024, 1, 1)
    payload = {"tweets": [
        {"id": "1", "text": "keep", "createdAt": "2024-02-01T00:00:00Z",
         "author": {"userName": "a"}},
        {"id": "2", "text": "rt", "isRetweet": True,
         "createdAt": "2024-02-01T00:00:00Z", "author": {"userName": "a"}},
    ]}
    with mock.patch.object(c, "_get", return_value=payload):
        out = c.user_tweets("a", max_results=10, since=since)
    assert [t.id for t in out] == ["1"]   # 转发被跳过


def test_thirdparty_user_tweets_marks_inactive_when_all_old():
    c = _thirdparty()
    since = dt.datetime(2024, 6, 1)
    payload = {"tweets": [
        {"id": "1", "text": "old", "createdAt": "2024-01-01T00:00:00Z",
         "author": {"userName": "stale"}},
    ]}
    with mock.patch.object(c, "_get", return_value=payload):
        out = c.user_tweets("@stale", max_results=10, since=since)
    assert out == []
    assert "stale" in c.inactive_accounts


def test_thirdparty_user_tweets_nested_tweets_location():
    # 列表也可能在 data.tweets 下
    c = _thirdparty()
    payload = {"data": {"tweets": [
        {"id": "9", "text": "nested", "createdAt": "2024-02-01T00:00:00Z",
         "author": {"userName": "n"}},
    ]}}
    with mock.patch.object(c, "_get", return_value=payload):
        out = c.user_tweets("n", max_results=10, since=dt.datetime(2024, 1, 1))
    assert [t.id for t in out] == ["9"]


# ── 12. ThirdPartyXClient.search ──────────────────────────────────────────────

def test_thirdparty_search_parses_and_sets_params():
    c = _thirdparty()
    payload = {"tweets": [
        {"id": "1", "text": "hit", "createdAt": "2024-02-01T00:00:00Z",
         "author": {"userName": "u"}},
        {"id": "2", "text": "rt", "is_retweet": True,
         "createdAt": "2024-02-01T00:00:00Z", "author": {"userName": "u"}},
    ]}
    with mock.patch.object(c, "_get", return_value=payload) as mget:
        out = c.search("kw", max_results=10, since=dt.datetime(2024, 1, 1), label="L")
    assert [t.id for t in out] == ["1"]
    assert all(t.source_label == "L" for t in out)
    params = mget.call_args[0][1]
    assert params["query"] == "kw"
    assert params["queryType"] == "Latest"


# ── 13. build_client 工厂 ─────────────────────────────────────────────────────

def test_build_client_official():
    cfg = {"x_api": {"provider": "official", "bearer_token": "tok"}}
    c = build_client(cfg)
    assert isinstance(c, OfficialXClient)


def test_build_client_thirdparty():
    cfg = {"x_api": {"provider": "thirdparty",
                     "thirdparty": {"base_url": "https://api.x.io", "api_key": "k"}}}
    c = build_client(cfg)
    assert isinstance(c, ThirdPartyXClient)


def test_build_client_default_provider_is_official():
    cfg = {"x_api": {"bearer_token": "tok"}}
    assert isinstance(build_client(cfg), OfficialXClient)


def test_build_client_unknown_raises():
    with pytest.raises(XClientError):
        build_client({"x_api": {"provider": "nope"}})


# ── 独立运行入口（无 pytest 时） ──────────────────────────────────────────────

def _run_standalone() -> int:
    import inspect
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = failed = skipped = 0
    for fn in fns:
        # 跳过依赖 pytest fixture（monkeypatch 等）的用例
        params = list(inspect.signature(fn).parameters)
        if params:
            print(f"SKIP  {fn.__name__} (需要 fixture: {params})")
            skipped += 1
            continue
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
    print(f"\n{passed} passed, {failed} failed, {skipped} skipped "
          f"(total {passed + failed + skipped})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_standalone())
