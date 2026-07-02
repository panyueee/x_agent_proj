# -*- coding: utf-8 -*-
"""scripts/ingest_global_gurus.py 单测：mock 网络，覆盖 HTML 剥离 / 断点续传 / 元数据格式。"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import ingest_global_gurus as gg


# ── HTML 剥离 ─────────────────────────────────────────────────────────────────
class TestHtmlToText:
    def test_strip_tags_and_entities(self):
        raw = ('<div><p>Bonds yield <b>4.5%</b> &amp; rising.</p>'
               '<script>track();</script><style>p{color:red}</style>'
               '<p>Second&nbsp;paragraph &#8220;quoted&#8221;</p></div>')
        text = gg.html_to_text(raw)
        assert "Bonds yield 4.5% & rising." in text
        assert "track" not in text          # script 去除
        assert "color:red" not in text      # style 去除
        assert "<" not in text and ">" not in text
        assert "Second paragraph" in text   # nbsp → 空格
        assert "“quoted”" in text  # 实体解码

    def test_block_tags_become_newlines(self):
        text = gg.html_to_text("<p>one</p><p>two</p><li>three</li>")
        assert text.splitlines() == ["one", "two", "three"]

    def test_empty(self):
        assert gg.html_to_text("") == ""

    def test_maybe_unescape_escaped_html(self):
        # Blogger description 里实体转义的 HTML：先解一层实体再剥标签
        escaped = "&lt;p&gt;Valuation is &lt;b&gt;hard&lt;/b&gt;&lt;/p&gt;" * 2
        text = gg.html_to_text(gg._maybe_unescape(escaped))
        assert "Valuation is hard" in text
        assert "<" not in text

    def test_maybe_unescape_keeps_plain_html(self):
        raw = "<p>a &lt; b holds</p><p>x</p><p>y</p>"
        assert gg._maybe_unescape(raw) == raw   # 真 HTML 不再解实体


# ── done-list 断点续传 ────────────────────────────────────────────────────────
@pytest.fixture
def tmp_done(tmp_path, monkeypatch):
    f = tmp_path / "gurus_done.json"
    monkeypatch.setattr(gg, "DONE_FILE", f)
    return f


class TestDoneList:
    def test_roundtrip(self, tmp_done):
        assert gg._load_done() == set()
        gg._save_done({"guru:a:1", "guru:b:2"})
        assert gg._load_done() == {"guru:a:1", "guru:b:2"}
        assert json.loads(tmp_done.read_text()) == ["guru:a:1", "guru:b:2"]  # 排序稳定

    def test_corrupt_file_returns_empty(self, tmp_done):
        tmp_done.write_text("{oops")
        assert gg._load_done() == set()

    def test_source_id_stable(self):
        sid = gg.source_id_for("lyn_alden", "https://x.com/a")
        assert sid == gg.source_id_for("lyn_alden", "https://x.com/a")
        assert sid.startswith("guru:lyn_alden:") and len(sid.split(":")[-1]) == 32


# ── process_articles：断点跳过 + 元数据格式 ──────────────────────────────────
GOOD_TEXT = ("Inflation dynamics and monetary policy interact through real rates. " * 5)


def _item(url, title="T", date="2026-01-02", text=GOOD_TEXT):
    return {"title": title, "url": url, "date": date, "site": "test.com",
            "get_text": (lambda: text)}


@pytest.fixture
def fake_rag(monkeypatch):
    """替换 RAG 绑定，记录 ingest 调用。"""
    calls = []

    def fake_ingest(text, **kw):
        calls.append({"text": text, **kw})
        return 3

    monkeypatch.setattr(gg, "_rag_ingest", fake_ingest)
    monkeypatch.setattr(gg, "_rag_quality", lambda t: (len(t) > 50, "too_short"))
    return calls


class TestProcessArticles:
    def test_metadata_format(self, tmp_done, fake_rag):
        done = set()
        res = gg.process_articles("alden", "lyn_alden",
                                  iter([_item("https://www.lynalden.com/post-1/",
                                              title="ETF Mechanics")]),
                                  done, dry_run=False)
        assert res["new"] == 1 and not res["failed"]
        call = fake_rag[0]
        assert call["source_type"] == "guru"
        assert call["author"] == "lyn_alden"
        assert call["title"] == "ETF Mechanics"
        assert call["skip_vectors"] is True
        meta = call["extra_meta"]
        assert meta["url"] == "https://www.lynalden.com/post-1/"
        assert meta["date"] == "2026-01-02"
        assert call["source_id"] == gg.source_id_for("lyn_alden", meta["url"])
        # 入库成功后写 done 文件
        assert call["source_id"] in gg._load_done()

    def test_resume_skips_done_without_fetch(self, tmp_done, fake_rag):
        url = "https://www.lynalden.com/old/"
        done = {gg.source_id_for("lyn_alden", url)}

        def boom():
            raise AssertionError("done 命中不应再取正文")

        it = {"title": "Old", "url": url, "date": "", "get_text": boom}
        res = gg.process_articles("alden", "lyn_alden", iter([it]), done)
        assert res["new"] == 0 and not fake_rag

    def test_quality_reject_not_marked_done(self, tmp_done, fake_rag):
        done = set()
        res = gg.process_articles("x", "a", iter([_item("https://t.co/1", text="short")]), done)
        assert res["new"] == 0 and res["skipped_quality"] == 1
        assert not done            # 质检未过不记 done，可重试
        assert res["failed"][0][1].startswith("quality:")

    def test_fetch_error_recorded_and_continues(self, tmp_done, fake_rag):
        def boom():
            raise RuntimeError("net down")

        items = [{"title": "A", "url": "https://t.co/a", "date": "", "get_text": boom},
                 _item("https://t.co/b")]
        res = gg.process_articles("x", "a", iter(items), set())
        assert res["new"] == 1 and len(res["failed"]) == 1
        assert "net down" in res["failed"][0][1]

    def test_limit(self, tmp_done, fake_rag):
        items = [_item(f"https://t.co/{i}") for i in range(5)]
        res = gg.process_articles("x", "a", iter(items), set(), limit=2)
        assert res["new"] == 2 and len(fake_rag) == 2

    def test_dry_run_touches_nothing(self, tmp_done, fake_rag, capsys):
        res = gg.process_articles("x", "a", iter([_item("https://t.co/1")]), set(),
                                  dry_run=True)
        assert res["new"] == 1 and not fake_rag
        assert not tmp_done.exists()
        assert "https://t.co/1" in capsys.readouterr().out


# ── 各源迭代器（mock http）────────────────────────────────────────────────────
class TestBuffettIndex:
    def test_parse_links(self):
        idx = ('<a href="1977.html">1977</a> <a href="2004ltr.pdf">2004</a>'
               '<a href="1977.html">dup</a> <a href="other.html">x</a>'
               "<a href='2024ltr.pdf'>2024</a>")
        links = gg.iter_buffett_links(idx)
        assert links == [("1977", "1977.html"), ("2004", "2004ltr.pdf"),
                         ("2024", "2024ltr.pdf")]


class TestWpIterator:
    def test_pagination_and_fields(self, monkeypatch):
        pages = {
            1: [{"link": "https://s.com/a/", "date": "2026-05-01T10:00:00",
                 "title": {"rendered": "Post &amp; A"},
                 "content": {"rendered": "<p>body A</p>" * 20}}] * 50,
            2: [{"link": "https://s.com/b/", "date": "2026-04-01T09:00:00",
                 "title": {"rendered": "B"},
                 "content": {"rendered": "<p>body B</p>"}}],
        }

        def fake_json(url, timeout=60):
            import re as _re
            page = int(_re.search(r"[?&]page=(\d+)", url).group(1))
            return pages.get(page, [])

        monkeypatch.setattr(gg, "http_json", fake_json)
        items = list(gg.iter_wp_posts("https://s.com", "s.com"))
        assert len(items) == 51
        first = items[0]
        assert first["title"] == "Post & A"          # 实体已解码
        assert first["date"] == "2026-05-01"          # 只留日期
        assert "body A" in first["get_text"]()
        assert items[-1]["url"] == "https://s.com/b/"

    def test_stops_on_400(self, monkeypatch):
        import requests

        def fake_json(url, timeout=60):
            if "page=1" in url:
                return [{"link": "https://s.com/a/", "date": "2026-01-01T00:00:00",
                         "title": {"rendered": "A"}, "content": {"rendered": "x"}}] * 50
            resp = requests.Response()
            resp.status_code = 400
            raise requests.HTTPError(response=resp)

        monkeypatch.setattr(gg, "http_json", fake_json)
        assert len(list(gg.iter_wp_posts("https://s.com", "s.com"))) == 50


class TestBloggerRss:
    XML = """<?xml version='1.0'?><rss><channel>
      <item><title>Valuing &amp; Pricing</title>
        <link>https://aswathdamodaran.blogspot.com/2026/06/post.html</link>
        <pubDate>Thu, 18 Jun 2026 07:00:00 +0000</pubDate>
        <description>&lt;p&gt;Full &lt;b&gt;post&lt;/b&gt; body here&lt;/p&gt;&lt;p&gt;more&lt;/p&gt;</description>
      </item>
      <item><title><![CDATA[CDATA Title]]></title>
        <link>https://aswathdamodaran.blogspot.com/2026/05/p2.html</link>
        <pubDate>bad-date</pubDate>
        <description><![CDATA[<p>raw html body</p>]]></description>
      </item></channel></rss>"""

    def test_parse(self):
        items = gg.parse_blogger_rss(self.XML)
        assert len(items) == 2
        a, b = items
        assert a["title"] == "Valuing & Pricing"
        assert a["date"] == "2026-06-18"                    # RFC822 → ISO
        assert "Full post body here" in a["get_text"]()     # 转义 HTML 剥净
        assert "<" not in a["get_text"]()
        assert b["title"] == "CDATA Title"
        assert b["date"] == "bad-date"                      # 解析失败保留原串
        assert b["get_text"]() == "raw html body"           # CDATA 原生 HTML


class TestHayesIterator:
    def test_archive_pagination_and_post_body(self, monkeypatch):
        archive = {
            0: [{"slug": "post-a", "title": "Post A", "audience": "everyone",
                 "post_date": "2026-06-08T23:01:28.122Z",
                 "canonical_url": "https://cryptohayes.substack.com/p/post-a"},
                {"slug": "paid", "title": "Paid", "audience": "only_paid",
                 "post_date": "2026-06-01T00:00:00Z", "canonical_url": "u"}],
            2: [],
        }

        def fake_json(url, timeout=60):
            import re as _re
            if "/api/v1/archive" in url:
                off = int(_re.search(r"offset=(\d+)", url).group(1))
                return archive.get(off, [])
            if url.endswith("/api/v1/posts/post-a"):
                return {"body_html": "<p>alpha beta</p>"}
            raise AssertionError(f"unexpected url {url}")

        monkeypatch.setattr(gg, "http_json", fake_json)
        items = list(gg.iter_hayes(page_size=2))
        assert len(items) == 1                        # 付费 audience 被过滤
        it = items[0]
        assert it["url"] == "https://cryptohayes.substack.com/p/post-a"
        assert it["date"] == "2026-06-08"
        assert it["get_text"]() == "alpha beta"


# ── 代理显式表 ────────────────────────────────────────────────────────────────
class TestProxyTable:
    def test_domain_modes(self):
        assert gg.DOMAIN_MODE["www.lynalden.com"] == "direct"   # 代理掐 TLS
        assert gg.DOMAIN_MODE["blog.bitmex.com"] == "proxy"     # 直连不稳
        assert gg.DEFAULT_MODE == "proxy"

    def test_session_proxy_config(self):
        gg._sessions.clear()
        s_direct = gg._session("direct")
        s_proxy = gg._session("proxy")
        assert s_direct.trust_env is False and not s_direct.proxies
        assert s_proxy.proxies["https"] == gg.PROXY_URL
        gg._sessions.clear()
