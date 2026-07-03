"""
scripts/ingest_chief_web.py 回归测试（全 mock 网络，不落真实 DB）。

覆盖：
  - nxny：列表解析 / 详情解析（正文在 display:none div、日期/券商/作者字段）/
          作者 id 自动解析（含"团队报告里首席只是共同作者"的宽容匹配）
  - 格隆汇：列表解析 / 与已入库公众号文章的标题归一化去重
  - 新浪zl：列表解析（去"查看全文"重复锚）/ 正文提取（artibody→正文end、去营销行）
  - 断点续传：done-list 里已有的 source_id 不再抓详情
  - 质检：text_quality 不过的内容不入库

运行：
    .venv/bin/python -m pytest tests/test_chief_web.py -v
"""
from __future__ import annotations

import importlib.util
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# scripts/ 不是包，用 importlib 按路径加载
_spec = importlib.util.spec_from_file_location(
    "ingest_chief_web", os.path.join(_ROOT, "scripts", "ingest_chief_web.py"))
icw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(icw)


# ── 代表性 HTML 样例（按实测页面结构裁剪）──────────────────────────────────────
NXNY_LIST_HTML = """
<a href="/report/view_6366715.html" title="华创证券-【宏观快评】6月PMI数据点评：出口强劲增长-260630" target="_blank">x</a>
<a href="/report/view_6366270.html" title="华创证券-【宏观专题】全球货币转向跟踪第14期：全球加息潮启动？-260630">y</a>
<a href="/report/view_6366715.html" title="华创证券-【宏观快评】6月PMI数据点评：出口强劲增长-260630">重复条目</a>
<a href="/report/view_6330868.html" title="申万宏源-铟行业深度报告之二-260617">侧栏热门(其他券商)</a>
"""

NXNY_DETAIL_HTML = """
<html><head><title>
  华创证券-【宏观快评】6月PMI数据点评：出口强劲增长-260630
</title></head><body>
<td>上传日期：</td><td>&nbsp;&nbsp;2026/7/1</td>
<td>来源：</td><td>&nbsp;&nbsp;<a href="/stype_61/" target='_blank'>华创证券</a></td>
<td>评级：</td><td>&nbsp;&nbsp;<b>--</b></td>
<td>作者：</td>
<td>&nbsp;&nbsp;<b><a href="/author/11240.html" target="_blank">张瑜</a>,<a href="/author/11241.html" target="_blank">陆银波</a></b></td>
<td>下载权限：</td><td><span>此报告为加密报告</span></td>
<div style="display:none">PMI数据：制造业PMI有所回升<br>　　6月制造业PMI为50.3%，前值为50.0%。
具体分项来看：生产指数为51.4%，新订单指数为51.2%，出口订单指数为50.1%，从业人员指数48.5%。
建筑业商务活动指数为49.0%，服务业商务活动指数为50.4%，综合PMI产出指数为50.6%。
出口强劲的原因：全球制造业景气持续上行，美伊冲突缓和后中东航线恢复。风险提示：内需偏弱。</div>
<strong>相关研报</strong>
</body></html>
"""

NXNY_DETAIL_EMPTY = """
<html><head><title>华创证券-空正文报告-260629</title></head><body>
<td>下载权限：</td><td><span>此报告为加密报告</span></td>
<div style="display:none"></div>
</body></html>
"""

# my-dynamics API 响应（timestamp 游标翻页；type=2 为文章，type=1 为短动态）
GLH_API_JSON = """{"statusCode":200,"totalCount":15,"result":[
  {"type":2,"id":577538,"title":"1月非农为何超预期？","content":"1月新增非农就业人口大增。",
   "createTimestamp":1675641600,"route":"https://m.gelonghui.com/p/577538"},
  {"type":1,"id":999999,"title":"","content":"一条无标题短动态",
   "createTimestamp":1675600000,"route":"https://m.gelonghui.com/moment/999999"},
  {"type":2,"id":577474,"title":"中国版QE：谁在“非常规”扩表？","content":"",
   "createTimestamp":1675555200,"route":"https://m.gelonghui.com/p/577474"}
]}"""

GLH_ARTICLE_HTML = """
<html><body><article><p>事项</p><p>美国1月新增非农就业人数51.7万，好于彭博预期18.8万。
失业率3.4%，劳动参与率提升至62.4%。行业结构上新增就业主要来源是服务业，
休闲酒店业新增12.8万。时薪增速继续下降，环比0.3%。</p></article></body></html>
"""

SINA_LIST_HTML = """
<a class="link-212121" target="_blank" href="http://finance.sina.com.cn/zl/china/2026-06-09/zl-iniavfst1780547.shtml">李迅雷：从M2视角看中国经济驱动力的变化</a>
<a class="blog-viewAll" target="_blank" href="http://finance.sina.com.cn/zl/china/2026-06-09/zl-iniavfst1780547.shtml">&gt;&gt; 查看全文</a>
<a class="link-212121" target="_blank" href="http://finance.sina.com.cn/zl/china/2026-04-16/zl-inhuryek2952350.shtml">李迅雷：拉长60年，多维度分析中国经济现象</a>
<a class="blog-viewAll" target="_blank" href="http://finance.sina.com.cn/zl/china/2026-04-16/zl-inhuryek2952350.shtml">&gt;&gt; 查看全文</a>
"""

SINA_ARTICLE_HTML = """
<html><body><div id="artibody">
<!-- publish_helper name='原始正文' -->
<p>　　意见领袖 | 李迅雷</p>
<p>　　从M2的视角看，过去驱动经济增长的主要是投资，货币扩张对应的是地产和基建。</p>
<p>　　如今M2增速与名义GDP增速的剪刀差收窄，反映资金效率的变化。</p>
<p>海量资讯、精准解读，尽在新浪财经APP</p>
</div>
<script>x()</script>
<!-- 正文内容 end -->
<p class="article-editor">责任编辑：张三</p>
</body></html>
"""


# ── nxny ─────────────────────────────────────────────────────────────────────
def test_nxny_list_parse():
    items = icw.parse_nxny_list(NXNY_LIST_HTML)
    assert ("6366715", "华创证券-【宏观快评】6月PMI数据点评：出口强劲增长-260630") == items[0]
    assert len(items) == 3                       # 重复 id 去掉
    assert items[2][1].startswith("申万宏源")      # 侧栏条目由调用方按前缀过滤


def test_nxny_detail_parse():
    d = icw.parse_nxny_detail(NXNY_DETAIL_HTML)
    assert d["title"].startswith("华创证券-【宏观快评】6月PMI数据点评")
    assert d["date"] == "2026-07-01"             # 2026/7/1 归一化
    assert d["broker"] == "华创证券"
    assert d["authors"] == {"张瑜": "11240", "陆银波": "11241"}
    assert "制造业PMI有所回升" in d["text"]
    assert "<" not in d["text"]                  # HTML 已剥净
    assert "相关研报" not in d["text"]            # 没把侧栏吸进来


def test_nxny_detail_date_fallback_from_title():
    html = NXNY_DETAIL_HTML.replace("上传日期：", "别的字段：")
    d = icw.parse_nxny_detail(html)
    assert d["date"] == "2026-06-30"             # 标题尾 -260630 兜底


def test_nxny_resolve_author_id(monkeypatch):
    """作者 id 解析：分类页 → 命中关键词的详情页 → 作者链接精确匹配姓名。
    张瑜是团队报告的共同作者之一（宽容：不要求唯一作者/标题含名字）。"""
    def fake_fetch(url, **kw):
        if "/stype_61" in url:
            return NXNY_LIST_HTML
        if "/report/view_" in url:
            return NXNY_DETAIL_HTML
        raise AssertionError(f"意外请求 {url}")
    monkeypatch.setattr(icw, "fetch", fake_fetch)
    tgt = {"name": "张瑜", "broker": "华创证券", "prefix": "华创证券",
           "stype": 61, "author_id": None, "resolve_kw": ["宏观"]}
    state = {"done": [], "author_ids": {}}
    icw.nxny_resolve_author_ids([tgt], state, max_pages=1)
    assert tgt["author_id"] == "11240"
    assert state["author_ids"]["张瑜"] == "11240"


def test_nxny_resolve_not_found(monkeypatch):
    """券商分类里翻不到该首席 → author_id 保持 None，不抛异常。"""
    monkeypatch.setattr(icw, "fetch", lambda url, **kw:
                        NXNY_LIST_HTML if "/stype_" in url else NXNY_DETAIL_HTML)
    tgt = {"name": "不存在的人", "broker": "华创证券", "prefix": "华创证券",
           "stype": 61, "author_id": None, "resolve_kw": ["宏观"]}
    icw.nxny_resolve_author_ids([tgt], {"done": [], "author_ids": {}}, max_pages=1)
    assert tgt["author_id"] is None


def test_nxny_resolve_via_probe_ids(monkeypatch):
    """分类页为空壳的券商（平安/建投/民生）走 probe_ids 直探详情页解析作者 id。"""
    def fake_fetch(url, **kw):
        assert "/report/view_5867275.html" in url   # 不该去翻分类页
        return NXNY_DETAIL_HTML.replace("张瑜", "钟正生").replace("11240", "20001")
    monkeypatch.setattr(icw, "fetch", fake_fetch)
    tgt = {"name": "钟正生", "broker": "平安证券", "prefix": "平安证券",
           "stype": 63, "author_id": None, "resolve_kw": ["宏观"],
           "probe_ids": ["5867275"]}
    state = {"done": [], "author_ids": {}}
    icw.nxny_resolve_author_ids([tgt], state, max_pages=1)
    assert tgt["author_id"] == "20001"


def test_nxny_ratelimit_page_not_marked_done(monkeypatch):
    """限流/错误页（无标题无字段）不记 done，续传时可重试；且不入库。"""
    def fake_fetch(url, **kw):
        if "/author/" in url:
            return NXNY_LIST_HTML
        return "<html><head><title></title></head><body>err</body></html>"
    monkeypatch.setattr(icw, "fetch", fake_fetch)
    monkeypatch.setattr(icw, "save_state", lambda *a, **k: None)
    ingested, failed, done = [], [], set()
    monkeypatch.setattr(icw, "do_ingest",
                        lambda text, **kw: ingested.append(kw["source_id"]) or 1)
    monkeypatch.setattr(icw, "NXNY_TARGETS", [
        {"name": "张瑜", "broker": "华创证券", "prefix": "华创证券", "stype": 61,
         "author_id": "11240", "resolve_kw": ["宏观"]}])
    icw.run_nxny({"done": [], "author_ids": {}}, done, failed, set(), max_pages=1)
    assert ingested == [] and done == set()      # 全部留待重试
    assert all("疑似限流" in f for f in failed)


def test_nxny_author_page_pagination_stops_on_repeat(monkeypatch):
    """越界作者页返回重复内容时停止翻页。"""
    monkeypatch.setattr(icw, "fetch", lambda url, **kw: NXNY_LIST_HTML)
    items = icw.crawl_nxny_author_reports("11240")   # 每页内容相同 → 第2页无新id
    assert len(items) == 3


# ── 格隆汇 ────────────────────────────────────────────────────────────────────
def test_glh_api_parse():
    items = icw.parse_glh_api(GLH_API_JSON)
    assert len(items) == 2                       # 无标题短动态被过滤
    assert items[0]["id"] == "577538"
    assert items[0]["title"] == "1月非农为何超预期？"
    assert items[0]["date"] == "2023-02-06"      # createTimestamp → 日期
    assert items[0]["summary"] == "1月新增非农就业人口大增。"
    assert items[1]["title"] == "中国版QE：谁在“非常规”扩表？"


def test_glh_cursor_pagination_stops(monkeypatch):
    """游标翻页：第二页返回同样内容（无新 id）时停止，不死循环。"""
    calls = []

    def fake_fetch(url, **kw):
        calls.append(url)
        return GLH_API_JSON
    monkeypatch.setattr(icw, "fetch", fake_fetch)
    items = icw.crawl_glh_articles(293015)
    assert len(items) == 2
    assert len(calls) == 2                       # 第2次请求带游标、无新条目即停
    assert "timestamp=1675555200" in calls[1]    # 游标 = 上页最后一条的 ts


def test_glh_article_parse():
    text = icw.parse_glh_article(GLH_ARTICLE_HTML)
    assert "非农就业人数51.7万" in text
    assert "<" not in text


def test_norm_title_dedup():
    """标题归一化去重：公众号常带【前缀】和不同标点/空格写法。"""
    wechat = {icw.norm_title("【华创宏观】1月非农为何超预期？——美国就业数据点评")}
    assert icw.is_dup_title("1月非农为何超预期？— 美国就业数据点评", wechat)
    assert icw.is_dup_title("1月非农为何超预期?——美国就业数据点评", wechat)   # 全半角标点
    assert not icw.is_dup_title("2月非农为何低于预期？", wechat)
    # 短标题不做包含比对，避免误伤
    assert not icw.is_dup_title("周报", {icw.norm_title("每周经济观察周报第76期")})


def test_glh_dedup_and_done_skip(monkeypatch):
    """已入库公众号同名文章 → 跳过且记 done；done 里已有的 → 不再抓详情。"""
    fetched = []

    def fake_fetch(url, **kw):
        fetched.append(url)
        if "my-dynamics" in url:
            return GLH_API_JSON if "timestamp=" not in url else '{"result":[]}'
        return GLH_ARTICLE_HTML
    monkeypatch.setattr(icw, "fetch", fake_fetch)
    monkeypatch.setattr(icw, "load_wechat_title_norms",
                        lambda: {icw.norm_title("1月非农为何超预期？")})
    monkeypatch.setattr(icw, "check_quality", lambda t: (True, "ok"))
    ingested = []
    monkeypatch.setattr(icw, "do_ingest",
                        lambda text, **kw: ingested.append(kw["source_id"]) or 1)
    monkeypatch.setattr(icw, "save_state", lambda *a, **k: None)

    done = {"glh:577474"}                       # 断点：第二篇已入库
    icw.run_gelonghui({"done": [], "author_ids": {}}, done, [], max_pages=1)
    assert ingested == []                        # 一篇公众号重复、一篇断点跳过
    assert "glh:577538" in done                  # 重复的也记 done，下次不再比对
    assert not any("/p/" in u for u in fetched)  # 两篇都没抓详情页


# ── 新浪 zl ───────────────────────────────────────────────────────────────────
def test_sina_list_parse():
    items = icw.parse_sina_list(SINA_LIST_HTML)
    assert len(items) == 2                       # "查看全文"重复锚被丢弃
    assert items[0][1] == "李迅雷：从M2视角看中国经济驱动力的变化"
    assert items[0][0].endswith("zl-iniavfst1780547.shtml")


def test_sina_article_parse():
    text = icw.parse_sina_article(SINA_ARTICLE_HTML)
    assert "过去驱动经济增长的主要是投资" in text
    assert "海量资讯" not in text                 # 营销行剔除
    assert "责任编辑" not in text                 # 正文end 之后不收
    assert "artibody" not in text                # div 开标签残渣不带入


def test_sina_date_from_url():
    assert icw._sina_date(
        "http://finance.sina.com.cn/zl/china/2026-06-09/zl-x.shtml") == "2026-06-09"


def test_sina_quality_gate(monkeypatch):
    """质检不过的文章不入库，但记 done（避免反复重抓）并进失败清单。"""
    def fake_fetch(url, **kw):
        if "author_article" in url:
            return SINA_LIST_HTML if "page=1" in url else ""
        return "<div id=\"artibody\"><p>短</p></div><!-- 正文内容 end -->"
    monkeypatch.setattr(icw, "fetch", fake_fetch)
    monkeypatch.setattr(icw, "save_state", lambda *a, **k: None)
    ingested, failed, done = [], [], set()
    monkeypatch.setattr(icw, "do_ingest",
                        lambda text, **kw: ingested.append(kw["source_id"]) or 1)
    icw.run_sina({"done": [], "author_ids": {}}, done, failed,
                 {"李迅雷"}, max_pages=1)
    assert ingested == []
    assert len(done) == 2 and len(failed) == 2   # 两篇都"质检未过"


def test_sina_ingest_meta(monkeypatch):
    """正常入库路径：extra_meta 带 author/broker/date/url。"""
    def fake_fetch(url, **kw):
        if "author_article" in url:
            return SINA_LIST_HTML if "page=1" in url else ""
        return SINA_ARTICLE_HTML
    monkeypatch.setattr(icw, "fetch", fake_fetch)
    monkeypatch.setattr(icw, "check_quality", lambda t: (True, "ok"))
    monkeypatch.setattr(icw, "save_state", lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(icw, "do_ingest", lambda text, **kw: calls.append(kw) or 1)
    icw.run_sina({"done": [], "author_ids": {}}, set(), [], {"李迅雷"}, max_pages=1)
    assert len(calls) == 2
    m = calls[0]["extra_meta"]
    assert m["author"] == "李迅雷" and m["broker"] == "中泰证券"
    assert m["date"] == "2026-06-09" and m["url"].endswith(".shtml")
    assert calls[0]["source_type"] == "column"
    assert calls[0]["source_id"].startswith("sinazl:")


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
