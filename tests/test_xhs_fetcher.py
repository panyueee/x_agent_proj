"""
xhs_fetcher.py 解析逻辑回归测试。

测试范围（纯逻辑，不触发 xhs CLI / 真实网络 / Apple Vision OCR）：
  - _parse_time：毫秒时间戳 → ISO8601
  - _parse_count："1.2万" / "3k" / 数字 → int
  - _card_to_tweet：note_card → Tweet 字段映射（标题/描述/标签/时间戳/互动数）
  - _ocr_images：mock session + OCR，验证去重 / MAX_IMGS 截断
  - _read_note：CLI 输出解析与容错
  - XhsClient.search / user_posts：mock _run_xhs，验证过滤与映射

约束：subprocess / Vision 全部 mock，绝不真实调用。

本文件既可用 pytest 运行：
    python -m pytest tests/test_xhs_fetcher.py -v
也可直接当脚本运行：
    python tests/test_xhs_fetcher.py
"""
from __future__ import annotations

import datetime as dt
import os
import sys
from unittest import mock

# 让 `import x_agent.xhs_fetcher` 在任意 cwd 下都能工作
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from x_agent import xhs_fetcher as xf
from x_agent.xhs_fetcher import (
    XhsClient,
    _parse_time,
    _parse_count,
    _card_to_tweet,
    _ocr_images,
    _read_note,
    MAX_IMGS,
)
from x_agent.fetcher import Tweet


# ── 1. _parse_time ─────────────────────────────────────────────────────────────

def test_parse_time_ms_timestamp():
    # 1700000000000 ms = 2023-11-14T22:13:20Z
    assert _parse_time(1700000000000) == "2023-11-14T22:13:20Z"
    assert _parse_time("1700000000000") == "2023-11-14T22:13:20Z"


def test_parse_time_invalid_returns_empty():
    assert _parse_time(None) == ""
    assert _parse_time("abc") == ""


# ── 2. _parse_count ────────────────────────────────────────────────────────────

def test_parse_count_wan():
    assert _parse_count("1.2万") == 12000
    assert _parse_count("3万") == 30000


def test_parse_count_k():
    assert _parse_count("3k") == 3000
    assert _parse_count("2.5K") == 2500


def test_parse_count_plain_numbers():
    assert _parse_count(123) == 123
    assert _parse_count("456") == 456
    assert _parse_count("1,234") == 1234   # 逗号被剥离
    assert _parse_count("78.0") == 78


def test_parse_count_invalid_and_none():
    assert _parse_count(None) == 0
    assert _parse_count("") == 0
    assert _parse_count("abc") == 0


# ── 3. _card_to_tweet ──────────────────────────────────────────────────────────

def _base_card(**over):
    card = {
        "user": {"nickname": "财经达人", "user_id": "u123"},
        "title": "复利的力量",
        "desc": "长期投资是王道",
        "type": "normal",
        "tag_list": [{"name": "投资"}, {"name": "理财"}, {"name": ""}],
        "last_update_time": 1700000000000,
        "interact_info": {
            "liked_count": "1.2万",
            "collected_count": "3k",
            "comment_count": 42,
        },
    }
    card.update(over)
    return card


def test_card_to_tweet_full_mapping():
    # 无 image_list → 不触发 OCR
    tw = _card_to_tweet("note1", _base_card(), "搜索:投资")
    assert isinstance(tw, Tweet)
    assert tw.id == "xhs_note1"
    assert tw.author == "财经达人"
    assert tw.author_id == "u123"
    assert tw.url == "https://www.xiaohongshu.com/explore/note1"
    assert tw.source_label == "搜索:投资"
    assert tw.group_tag == "xiaohongshu"
    # text 由 title + desc + ocr + tag 拼接（OCR 为空）
    assert "复利的力量" in tw.text
    assert "长期投资是王道" in tw.text
    assert "投资" in tw.text and "理财" in tw.text
    assert tw.created_at == "2023-11-14T22:13:20Z"
    assert tw.metrics == {
        "liked_count": 12000,
        "collected_count": 3000,
        "comment_count": 42,
    }


def test_card_to_tweet_video_skips_ocr():
    # 视频类型即使有图片也不 OCR；用 mock 确认 _ocr_images 未被调用
    card = _base_card(type="video", image_list=[{"url_default": "http://x/1.jpg"}])
    with mock.patch.object(xf, "_ocr_images") as moc:
        tw = _card_to_tweet("v1", card, "lbl")
    moc.assert_not_called()
    assert "复利的力量" in tw.text


def test_card_to_tweet_triggers_ocr_for_image_note():
    card = _base_card(image_list=[{"url_default": "http://x/1.jpg"}])
    with mock.patch.object(xf, "_ocr_images", return_value="图片识别文字") as moc:
        tw = _card_to_tweet("p1", card, "lbl")
    moc.assert_called_once()
    assert "图片识别文字" in tw.text


def test_card_to_tweet_seconds_timestamp():
    # 秒级时间戳容错（> 1e9 但 < 1e12）
    card = _base_card(last_update_time=1700000000)
    tw = _card_to_tweet("s1", card, "lbl")
    assert tw.created_at == "2023-11-14T22:13:20Z"


def test_card_to_tweet_no_timestamp_empty():
    card = _base_card(last_update_time=0, time=0)
    tw = _card_to_tweet("z1", card, "lbl")
    assert tw.created_at == ""


def test_card_to_tweet_nick_name_fallback():
    # 用户名优先 nickname，缺失时回退 nick_name
    card = _base_card(user={"nick_name": "备用昵称", "user_id": "u9"})
    tw = _card_to_tweet("n1", card, "lbl")
    assert tw.author == "备用昵称"


# ── 4. _ocr_images ─────────────────────────────────────────────────────────────

class _FakeImgResp:
    content = b"\xff\xd8\xff"  # 假 JPEG 字节


def test_ocr_images_dedup_and_limit(tmp_path):
    # 准备 5 张图，其中两张 URL 重复；MAX_IMGS 应截断为前 3 张去重后
    imgs = [
        {"url_default": "http://x/1.jpg"},
        {"url_default": "http://x/1.jpg"},   # 重复
        {"url_default": "http://x/2.jpg"},
        {"url_pre": "http://x/3.jpg"},
        {"url_default": "http://x/4.jpg"},
    ]
    fake_session = mock.Mock()
    fake_session.get.return_value = _FakeImgResp()
    with mock.patch.object(xf, "_session", fake_session), \
         mock.patch.object(xf, "_apple_vision_ocr", return_value="X") as mocr, \
         mock.patch.object(xf, "TMP_IMG", str(tmp_path / "ocr.jpg")):
        text = _ocr_images(imgs)
    # 只处理前 MAX_IMGS=3 张：url1 / url1(dup,跳过) / url2
    # → 实际下载 url1, url2（dup 不算新 URL 但占用切片位）
    assert mocr.call_count >= 1
    assert "X" in text
    # 验证确实切片到 MAX_IMGS 张以内
    assert fake_session.get.call_count <= MAX_IMGS


def test_ocr_images_skips_empty_urls(tmp_path):
    imgs = [{"url_default": ""}, {}, {"url_pre": "http://x/a.jpg"}]
    fake_session = mock.Mock()
    fake_session.get.return_value = _FakeImgResp()
    with mock.patch.object(xf, "_session", fake_session), \
         mock.patch.object(xf, "_apple_vision_ocr", return_value="A"), \
         mock.patch.object(xf, "TMP_IMG", str(tmp_path / "ocr.jpg")):
        text = _ocr_images(imgs)
    # 只有一张有效 URL 被下载
    assert fake_session.get.call_count == 1
    assert text == "A"


def test_ocr_images_network_error_caught(tmp_path):
    imgs = [{"url_default": "http://x/1.jpg"}]
    fake_session = mock.Mock()
    fake_session.get.side_effect = ConnectionError("boom")
    with mock.patch.object(xf, "_session", fake_session), \
         mock.patch.object(xf, "_apple_vision_ocr", return_value="X"), \
         mock.patch.object(xf, "TMP_IMG", str(tmp_path / "ocr.jpg")):
        # 异常应被吞掉，返回空字符串
        assert _ocr_images(imgs) == ""


# ── 5. _read_note ──────────────────────────────────────────────────────────────

def test_read_note_parses_card():
    payload = {"data": {"items": [{"note_card": {"title": "标题"}}]}}
    with mock.patch.object(xf, "_run_xhs", return_value=payload):
        card = _read_note("nid")
    assert card == {"title": "标题"}


def test_read_note_empty_items_returns_empty():
    with mock.patch.object(xf, "_run_xhs", return_value={"data": {"items": []}}):
        assert _read_note("nid") == {}


def test_read_note_malformed_returns_empty():
    with mock.patch.object(xf, "_run_xhs", return_value={}):
        assert _read_note("nid") == {}


# ── 6. XhsClient.search ────────────────────────────────────────────────────────

def _search_payload():
    return {"data": {"items": [
        {"id": "n1", "note_card": {
            "user": {"nickname": "A", "user_id": "u1"},
            "display_title": "搜索标题",   # search 用 display_title
            "desc": "正文",
            "type": "normal",
            "last_update_time": 1700000000000,
        }},
        {"id": "ad#123", "note_card": {"title": "广告"}},  # 含 # → 跳过
        {"id": "", "note_card": {"title": "无id"}},        # 空 id → 跳过
        {"id": "n2", "note_card": {}},                     # 空 card → 跳过
    ]}}


def test_search_maps_display_title_and_filters():
    since = dt.datetime(2000, 1, 1)
    with mock.patch.object(xf, "_run_xhs", return_value=_search_payload()):
        results = XhsClient().search("投资", max_results=10, since=since, label="搜索:投资")
    assert len(results) == 1
    tw = results[0]
    assert tw.id == "xhs_n1"
    assert "搜索标题" in tw.text   # display_title 被映射为 title
    assert "正文" in tw.text


def test_search_respects_since_filter():
    # since 设在未来 → 旧笔记被过滤掉
    since = dt.datetime(2099, 1, 1)
    with mock.patch.object(xf, "_run_xhs", return_value=_search_payload()):
        results = XhsClient().search("x", max_results=10, since=since, label="lbl")
    assert results == []


def test_search_respects_max_results():
    items = [{"id": f"n{i}", "note_card": {"title": f"t{i}", "type": "normal"}}
             for i in range(5)]
    payload = {"data": {"items": items}}
    since = dt.datetime(2000, 1, 1)
    with mock.patch.object(xf, "_run_xhs", return_value=payload):
        results = XhsClient().search("x", max_results=2, since=since, label="lbl")
    assert len(results) == 2


def test_search_empty_payload():
    since = dt.datetime(2000, 1, 1)
    with mock.patch.object(xf, "_run_xhs", return_value={}):
        assert XhsClient().search("x", max_results=10, since=since, label="lbl") == []


# ── 7. XhsClient.user_posts ────────────────────────────────────────────────────

def test_user_posts_reads_each_note():
    list_payload = {"data": {"items": [
        {"id": "n1"}, {"id": "bad#1"}, {"id": "n2"},
    ]}}
    note_cards = {
        "n1": {"title": "笔记1", "type": "normal", "last_update_time": 1700000000000},
        "n2": {"title": "笔记2", "type": "normal", "last_update_time": 1700000000000},
    }
    since = dt.datetime(2000, 1, 1)
    with mock.patch.object(xf, "_run_xhs", return_value=list_payload), \
         mock.patch.object(xf, "_read_note", side_effect=lambda nid: note_cards.get(nid, {})):
        results = XhsClient().user_posts("someuser", max_results=10, since=since)
    assert len(results) == 2
    assert {r.id for r in results} == {"xhs_n1", "xhs_n2"}
    assert all(r.source_label == "@someuser" for r in results)


def test_user_posts_skips_empty_card():
    list_payload = {"data": {"items": [{"id": "n1"}]}}
    since = dt.datetime(2000, 1, 1)
    with mock.patch.object(xf, "_run_xhs", return_value=list_payload), \
         mock.patch.object(xf, "_read_note", return_value={}):
        assert XhsClient().user_posts("u", max_results=10, since=since) == []


# ── 独立运行入口（无 pytest 时） ──────────────────────────────────────────────

def _run_standalone() -> int:
    import tempfile

    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = failed = skipped = 0
    for fn in fns:
        # 需要 tmp_path fixture 的测试在 standalone 下用临时目录代替
        kwargs = {}
        try:
            varnames = fn.__code__.co_varnames[:fn.__code__.co_argcount]
            if "tmp_path" in varnames:
                import pathlib
                kwargs["tmp_path"] = pathlib.Path(tempfile.mkdtemp())
            fn(**kwargs)
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
