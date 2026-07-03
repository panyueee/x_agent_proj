#!/usr/bin/env python3
"""
中国券商首席分析师历史文章 下载+入库 → RAG（output/rag.db）。

三个渠道（调研见 docs/survey_chief_sources.md）：
  1. nxny      股票报告网 nxny.com   —— 纯静态研报站。核心发现：每个作者有
     /author/{id}.html 作者页（20篇/页，_pN 翻页），直接按作者页抓取即可精确过滤，
     无需在整个券商分类里大海捞针。作者 id 通过券商分类页→详情页的作者链接自动解析。
     详情页 /report/view_{id}.html 的正文藏在 <div style="display:none"> 里（SSR 直出）。
     入库 source_type="research_web"。
  2. gelonghui 格隆汇 —— 张瑜专栏。列表用 my-dynamics JSON API 按 timestamp
     游标翻页（?page=N 是摆设，SSR 恒返回第 1 页），单篇 /p/{id} 全文免费。
     与已入库公众号(一瑜中的)文章按"标题归一化"去重。入库 source_type="column"。
  3. sina      新浪意见领袖 —— 列表在 iframe：
     finance.sina.com.cn/zl/author_article.d.html?id={uid}&page=N（静态 HTML，无需破 JS）。
     正文在 id="artibody" ... <!-- 正文内容 end --> 之间。入库 source_type="column"。

已实测确认的分类/用户 id（2026-07-02）：
  nxny：华创=61 中泰=1 广发=22 国盛=558 中信建投=66 平安=63 粤开=566
        中信证券=21 民生证券=14（本次翻导航补查到，survey 的遗留缺口）
  格隆汇：张瑜 user=293015
  新浪：李迅雷 uid=1803814487，张瑜 uid=1705952971

用法：
  .venv/bin/python scripts/ingest_chief_web.py --sources nxny,gelonghui,sina --dry-run --limit 3
  .venv/bin/python scripts/ingest_chief_web.py --sources nxny --authors 张瑜,郭磊
  .venv/bin/python scripts/ingest_chief_web.py --status     # 断点进度
  .venv/bin/python scripts/ingest_chief_web.py --stats      # 各渠道各作者入库统计

铁律：走 .venv/bin/python；requests 直连绕过系统代理；同域间隔 ≥2s；
      断点续传 data/chief_web_done_{渠道}.json（分文件，支持三渠道并行跑）；
      入库前 text_quality 质检；中文注释。
"""
from __future__ import annotations

import argparse
import hashlib
import html as _html
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

# ── 网络/证书铁律：直连、绕过系统 clash 代理，用 certifi 证书 ────────────────────
import os
for _k in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = "*"
try:
    import certifi
    os.environ["SSL_CERT_FILE"] = certifi.where()
    os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
except Exception:
    pass

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# done-list 按渠道分文件：三个渠道常并行跑（不同域名互不占用限速额度），
# 分文件避免并发覆盖丢 done 记录
def _done_file(source: str) -> Path:
    return ROOT / "data" / f"chief_web_done_{source}.json"
MIN_INTERVAL = 2.0          # 同域最小请求间隔（秒）
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def _log(m: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


# ── 抓取目标配置 ─────────────────────────────────────────────────────────────
# nxny：name=首席姓名；broker=券商全名；prefix=列表页标题前缀（用于过滤侧栏杂项）；
#       stype=券商分类 id；author_id=已知作者 id（未知则自动解析）；
#       resolve_kw=解析作者 id 时用来挑候选详情页的标题关键词（该首席常写的报告类型）
NXNY_TARGETS = [
    {"name": "张瑜",   "broker": "华创证券", "prefix": "华创证券", "stype": 61,
     "author_id": "11240", "resolve_kw": ["宏观"]},
    {"name": "李迅雷", "broker": "中泰证券", "prefix": "中泰证券", "stype": 1,
     "author_id": None, "resolve_kw": ["李迅雷", "宏观", "总量", "策略"],
     "probe_ids": ["4918095"]},   # 中泰-当前经济与政策思考-220406
    {"name": "郭磊",   "broker": "广发证券", "prefix": "广发证券", "stype": 22,
     "author_id": "10106", "resolve_kw": ["宏观"]},
    {"name": "戴康",   "broker": "广发证券", "prefix": "广发证券", "stype": 22,
     "author_id": None, "resolve_kw": ["策略", "资产"],
     "probe_ids": ["5234333"]},   # 广发-港股"战略机遇"系列十三-230321（戴康团队）
    # 注意：stype_21 近期只收 中信证券(香港)，大陆中信研报只到 2023 年左右的深页
    {"name": "明明",   "broker": "中信证券", "prefix": "中信证券", "stype": 21,
     "author_id": None, "resolve_kw": ["债", "固收", "利率", "宏观"],
     "probe_ids": ["5386240"]},   # 中信-债市聚焦系列-230731（明明团队）
    {"name": "牟一凌", "broker": "民生证券", "prefix": "民生证券", "stype": 14,
     "author_id": "10832", "resolve_kw": ["策略"]},   # id 经搜索引擎索引确认
    {"name": "罗志恒", "broker": "粤开证券", "prefix": "粤开证券", "stype": 566,
     "author_id": None, "resolve_kw": ["宏观", "财政"]},
    # 注意：平安/中信建投/民生 的分类页(63/66/14)是空壳（只剩侧栏热榜），
    # 报告本身仍在站内、作者页也正常，故这些人靠 probe_ids 或已知 author_id 解析
    {"name": "钟正生", "broker": "平安证券", "prefix": "平安证券", "stype": 63,
     "author_id": None, "resolve_kw": ["宏观"],
     "probe_ids": ["5867275"]},   # 平安-2025重振消费之路(一)-250127（钟正生团队）
    {"name": "熊园",   "broker": "国盛证券", "prefix": "国盛证券", "stype": 558,
     "author_id": None, "resolve_kw": ["宏观"]},
    {"name": "陈果",   "broker": "中信建投", "prefix": "中信建投", "stype": 66,
     "author_id": None, "resolve_kw": ["策略"],
     "probe_ids": ["5445054", "5387187", "5431783"]},  # 建投-一周策略回顾与展望(2023)
]
RESOLVE_MAX_PAGES = 15      # 解析作者 id 时最多翻多少页分类列表
RESOLVE_MAX_PROBES = 40     # 解析作者 id 时最多探测多少个详情页

GLH_TARGETS = [
    {"name": "张瑜",   "broker": "华创证券", "user_id": 293015},
    {"name": "李迅雷", "broker": "中泰证券", "user_id": 107592},  # "李迅雷金融与投资"
]

SINA_TARGETS = [
    {"name": "李迅雷", "broker": "中泰证券", "uid": "1803814487"},
    {"name": "张瑜",   "broker": "华创证券", "uid": "1705952971"},
]


# ── HTML → 纯文本（与 scripts/ingest_wechat_rss.py 同款）────────────────────────
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
_BLOCK_RE = re.compile(r"</?(p|br|div|li|tr|h[1-6]|section|article|blockquote|table)[^>]*>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_MULTINL_RE = re.compile(r"\n{3,}")


def html_to_text(raw: str) -> str:
    """正文 HTML 剥成纯文本：去 script/style、块级标签转换行、去标签、解实体、压空白。"""
    if not raw:
        return ""
    s = _SCRIPT_STYLE_RE.sub(" ", raw)
    s = _BLOCK_RE.sub("\n", s)
    s = _TAG_RE.sub("", s)
    s = _html.unescape(s)
    s = s.replace(" ", " ").replace("​", "")   # nbsp / 零宽空格
    lines = [_WS_RE.sub(" ", ln).strip() for ln in s.split("\n")]
    s = "\n".join(ln for ln in lines if ln)
    return _MULTINL_RE.sub("\n\n", s).strip()


# ── 标题归一化（格隆汇 vs 已入库公众号 去重用）─────────────────────────────────
# \W 在 unicode 模式下不含汉字/字母/数字 → 正好剥掉全部空格和中英文标点
_NORM_RE = re.compile(r"[\W_]+", re.UNICODE)


def norm_title(t: str) -> str:
    """去空格/标点/下划线并小写。'【华创宏观】出口强劲增长——6月PMI点评' 与
    '出口强劲增长—6月PMI点评' 归一化后可做包含比对。"""
    return _NORM_RE.sub("", t or "").lower()


def is_dup_title(title: str, existing_norms: set[str]) -> bool:
    """与已入库标题集合比对：归一化后完全相同，或一方是另一方的包含子串
    （公众号常加"【华创宏观】"之类前缀），均视为重复。"""
    n = norm_title(title)
    if not n:
        return False
    if n in existing_norms:
        return True
    for e in existing_norms:
        # 只对足够长的标题做包含比对，避免"周报"这类短词误伤
        if len(e) >= 10 and len(n) >= 10 and (n in e or e in n):
            return True
    return False


def load_wechat_title_norms() -> set[str]:
    """从 rag.db 读已入库的 公众号 + 新浪zl 文章标题（归一化），供格隆汇去重。
    同一首席的随笔常在 公众号/新浪/格隆汇 三处同发，标题只差前缀（"张瑜："等）。"""
    import sqlite3
    db_path = os.getenv("RAG_DB_PATH", str(ROOT / "output" / "rag.db"))
    if not Path(db_path).exists():
        return set()
    try:
        conn = sqlite3.connect(db_path, timeout=30)
        rows = conn.execute(
            "SELECT DISTINCT title FROM chunks WHERE source_type='wechat' "
            "UNION SELECT DISTINCT title FROM chunks "
            "WHERE source_type='column' AND json_extract(extra_meta,'$.source')='sina_zl'"
        ).fetchall()
        conn.close()
        return {norm_title(r[0]) for r in rows if r[0]}
    except Exception as e:
        _log(f"读已入库标题失败（跳过去重）：{e}")
        return set()


# ── 限速抓取 ─────────────────────────────────────────────────────────────────
_last_req: dict[str, float] = {}
_session = None


def fetch(url: str, timeout: int = 30, retries: int = 3) -> str:
    """限速 GET：同域间隔 ≥ MIN_INTERVAL，失败退避重试。测试里 monkeypatch 本函数。"""
    global _session
    import requests
    if _session is None:
        _session = requests.Session()
        _session.trust_env = False          # 忽略系统代理
        _session.headers["User-Agent"] = UA
    dom = urlparse(url).netloc
    for attempt in range(retries):
        wait = _last_req.get(dom, 0.0) + MIN_INTERVAL - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_req[dom] = time.time()
        try:
            r = _session.get(url, timeout=timeout)
            r.raise_for_status()
            r.encoding = "utf-8"            # 三个站点均为 utf-8
            return r.text
        except Exception as e:
            # 长跑会话的 keep-alive 连接可能被服务端拉黑/RST，重建会话再试
            try:
                _session.close()
            except Exception:
                pass
            _session = None
            import requests as _rq
            _session = _rq.Session()
            _session.trust_env = False
            _session.headers["User-Agent"] = UA
            if attempt == retries - 1:
                raise
            time.sleep(3 * (attempt + 1))   # 退避后重试
    return ""


# ── 断点续传（done-list，按渠道分文件）───────────────────────────────────────
def load_state(source: str) -> dict:
    """state = {"source": 渠道名, "done": [source_id...], "author_ids": {"张瑜": "11240"}}"""
    fp = _done_file(source)
    if fp.exists():
        try:
            st = json.load(open(fp, encoding="utf-8"))
            st.setdefault("done", [])
            st.setdefault("author_ids", {})
            st["source"] = source
            return st
        except Exception:
            pass
    return {"source": source, "done": [], "author_ids": {}}


def save_state(state: dict, done: set[str]) -> None:
    state["done"] = sorted(done)
    fp = _done_file(state.get("source", "misc"))
    fp.parent.mkdir(parents=True, exist_ok=True)
    tmp = fp.with_suffix(".tmp")
    json.dump(state, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    tmp.replace(fp)


def do_ingest(text: str, source_id: str, source_type: str, title: str,
              author: str, extra_meta: dict) -> int:
    """真正入库（延迟导入 x_agent.rag；测试里 monkeypatch 本函数）。"""
    from x_agent.rag import ingest_text
    return ingest_text(text, source_id=source_id, source_type=source_type,
                       title=title, author=author, extra_meta=extra_meta,
                       skip_vectors=True)   # 向量留给 embed-all 统一跑


def check_quality(text: str) -> tuple[bool, str]:
    """text_quality 质检（延迟导入，dry-run 也能用）。"""
    from x_agent.rag import text_quality
    return text_quality(text)


# ══════════════════════════════════════════════════════════════════════════════
# 渠道 1：股票报告网 nxny.com
# ══════════════════════════════════════════════════════════════════════════════
NXNY = "https://www.nxny.com"
_NXNY_ITEM_RE = re.compile(
    r'href="/report/view_(\d+)\.html"\s+title="([^"]+)"')
_NXNY_AUTHOR_RE = re.compile(
    r'href="/author/(\d+)\.html"[^>]*>\s*([^<]+?)\s*</a>')


def parse_nxny_list(html: str) -> list[tuple[str, str]]:
    """解析列表页（分类页/作者页通用）→ [(report_id, title), ...]，按出现序去重。"""
    seen, out = set(), []
    for rid, title in _NXNY_ITEM_RE.findall(html):
        if rid not in seen:
            seen.add(rid)
            out.append((rid, _html.unescape(title).strip()))
    return out


def _nxny_date(raw: str) -> str:
    """'2026/7/1' → '2026-07-01'。"""
    m = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})", raw or "")
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else ""


def parse_nxny_detail(html: str) -> dict:
    """解析研报详情页 → {title, date, broker, rating, authors:{name:id}, text}。
    正文由 SSR 直出在 <div style="display:none"> 内（页面上要登录才"显示"，但
    HTML 里就有），取"下载权限"之后的第一个该 div。"""
    t = re.search(r"<title>\s*(.*?)\s*</title>", html, re.S)
    title = _html.unescape(t.group(1)).strip() if t else ""

    d = re.search(r"上传日期：.{0,200}?(\d{4}/\d{1,2}/\d{1,2})", html, re.S)
    date = _nxny_date(d.group(1)) if d else ""
    if not date:                      # 兜底：标题尾部 -YYMMDD
        m = re.search(r"-(\d{6})\s*$", title)
        if m:
            y, mo, dy = m.group(1)[:2], m.group(1)[2:4], m.group(1)[4:6]
            date = f"20{y}-{mo}-{dy}"

    b = re.search(r'href="/stype_\d+/"[^>]*>([^<]+)</a>', html)
    broker = b.group(1).strip() if b else ""

    r = re.search(r"评级：.{0,200}?<b>([^<]*)</b>", html, re.S)
    rating = _html.unescape(r.group(1)).strip() if r else ""

    authors = {name.strip(): aid for aid, name in _NXNY_AUTHOR_RE.findall(html)}

    text = ""
    i = html.find("下载权限")
    m = re.search(r'<div style="display:none">(.*?)</div>', html[max(i, 0):], re.S)
    if m:
        text = html_to_text(m.group(1))
    return {"title": title, "date": date, "broker": broker,
            "rating": rating, "authors": authors, "text": text}


def nxny_resolve_author_ids(targets: list[dict], state: dict,
                            max_pages: int = RESOLVE_MAX_PAGES) -> None:
    """自动解析目标首席在 nxny 的作者 id：翻其券商分类页，挑标题含关键词的候选
    详情页，看作者链接里有没有该首席的名字。结果缓存进 state["author_ids"]。"""
    cache = state["author_ids"]
    # 先套用缓存/内置已知 id
    for t in targets:
        if t["author_id"] is None and t["name"] in cache:
            t["author_id"] = cache[t["name"]]
        elif t["author_id"]:
            cache.setdefault(t["name"], t["author_id"])

    # 第一步：先探已知的 probe_ids（搜索引擎索引到的该首席团队报告详情页），
    # 对分类页为空壳的券商（平安/建投/民生）这是唯一可行路径
    for t in targets:
        if t["author_id"] is not None:
            continue
        for rid in t.get("probe_ids") or []:
            try:
                det = parse_nxny_detail(fetch(f"{NXNY}/report/view_{rid}.html"))
            except Exception:
                continue
            if t["name"] in det["authors"]:
                t["author_id"] = det["authors"][t["name"]]
                cache[t["name"]] = t["author_id"]
                _log(f"  ✓ 解析到作者id（probe）：{t['name']} = {t['author_id']}"
                     f"（{det['title'][:40]}）")
                break

    # 第二步：按 stype 分组翻分类页（郭磊/戴康同在广发 22，一趟解决）
    by_stype: dict[int, list[dict]] = {}
    for t in targets:
        if t["author_id"] is None:
            by_stype.setdefault(t["stype"], []).append(t)

    for stype, pend in by_stype.items():
        probes = 0
        for page in range(1, max_pages + 1):
            if not pend:
                break
            url = f"{NXNY}/stype_{stype}_p{page}/" if page > 1 else f"{NXNY}/stype_{stype}/"
            try:
                items = parse_nxny_list(fetch(url))
            except Exception as e:
                _log(f"  解析作者id：列表页失败 {url}: {e}")
                break
            for rid, title in items:
                if not pend or probes >= RESOLVE_MAX_PROBES:
                    break
                # 只看该券商自己的报告（列表页混有侧栏热门），且标题命中候选关键词
                if not title.startswith(pend[0]["prefix"]):
                    continue
                if not any(kw in title for t in pend for kw in t["resolve_kw"]):
                    continue
                probes += 1
                try:
                    det = parse_nxny_detail(fetch(f"{NXNY}/report/view_{rid}.html"))
                except Exception:
                    continue
                for t in list(pend):
                    if t["name"] in det["authors"]:
                        t["author_id"] = det["authors"][t["name"]]
                        cache[t["name"]] = t["author_id"]
                        _log(f"  ✓ 解析到作者id：{t['name']} = {t['author_id']}"
                             f"（{det['title'][:40]}）")
                        pend.remove(t)
            if probes >= RESOLVE_MAX_PROBES:
                break
        for t in pend:
            _log(f"  ✗ 未解析到 {t['name']}({t['broker']}) 的作者id"
                 f"（翻了 {max_pages} 页 / 探测 {probes} 个详情页）")


def crawl_nxny_author_reports(author_id: str, max_pages: int = 0) -> list[tuple[str, str]]:
    """翻作者页收集全部 (report_id, title)。p1=/author/{id}.html，pN=/author/{id}_pN.html。
    到底的判定：无条目，或整页 id 都已见过（越界页会回退重复内容）。"""
    all_items, seen = [], set()
    page = 1
    while True:
        if max_pages and page > max_pages:
            break
        url = (f"{NXNY}/author/{author_id}.html" if page == 1
               else f"{NXNY}/author/{author_id}_p{page}.html")
        try:
            items = parse_nxny_list(fetch(url))
        except Exception as e:
            _log(f"  作者页抓取失败 {url}: {e}")
            break
        new = [(rid, t) for rid, t in items if rid not in seen]
        if not new:
            break
        for rid, t in new:
            seen.add(rid)
            all_items.append((rid, t))
        page += 1
    return all_items


def run_nxny(state: dict, done: set[str], failed: list[str], authors_filter: set[str],
             limit: int = 0, dry_run: bool = False, max_pages: int = 0) -> dict:
    """nxny 主流程：解析作者id → 作者页翻页 → 详情页 → 质检 → 入库。返回各作者新增数。"""
    targets = [t for t in NXNY_TARGETS
               if not authors_filter or t["name"] in authors_filter]
    _log(f"[nxny] 目标作者：{'、'.join(t['name'] for t in targets)}")
    nxny_resolve_author_ids(targets, state)
    if not dry_run:
        save_state(state, done)     # 作者 id 解析结果先落盘

    stats: dict[str, int] = {}
    for t in targets:
        name = t["name"]
        stats[name] = 0
        if not t["author_id"]:
            failed.append(f"nxny:{name}: 作者id未解析到，整体跳过")
            continue
        items = crawl_nxny_author_reports(t["author_id"], max_pages=max_pages)
        todo = [(rid, ti) for rid, ti in items if f"nxny:{rid}" not in done]
        _log(f"[nxny] {name}(author/{t['author_id']})：作者页共 {len(items)} 篇，"
             f"待抓 {len(todo)} 篇")
        n_this = 0
        for rid, list_title in todo:
            if limit and n_this >= limit:
                break
            sid = f"nxny:{rid}"
            url = f"{NXNY}/report/view_{rid}.html"
            try:
                det = parse_nxny_detail(fetch(url))
            except Exception as e:
                failed.append(f"{sid}: 抓取失败 {e}")
                continue
            # 被限流/跳错误页时解析不出报告字段 → 不记 done，留待下次续传重试。
            # （真"无正文加密报告"仍有 作者/来源 字段，走下面质检失败并记 done）
            if not det["title"] or (not det["text"] and not det["authors"]
                                    and not det["broker"]):
                failed.append(f"{sid}: 疑似限流/错误页，未记done")
                _log(f"  ✗ [{name}] 疑似限流/错误页：view_{rid}")
                continue
            if dry_run:
                n_this += 1
                print(f"\n--- [nxny/{name}] {det['title']}\n    date: {det['date']}  "
                      f"broker: {det['broker']}  authors: {list(det['authors'])}\n"
                      f"    text[{len(det['text'])}]: "
                      f"{det['text'][:100].replace(chr(10), ' ')}...")
                continue
            ok, reason = check_quality(det["text"])
            if not ok:
                failed.append(f"{sid}: 质检未过({reason}) {det['title'][:40]}")
                done.add(sid)       # 正文本身就短/空的加密报告，重试也没用，记 done
                continue
            n = do_ingest(
                det["text"], source_id=sid, source_type="research_web",
                title=det["title"], author=name,
                extra_meta={
                    "author": name, "authors": list(det["authors"]),
                    "broker": det["broker"] or t["broker"],
                    "date": det["date"], "url": url,
                    "rating": det["rating"], "source": "nxny",
                })
            done.add(sid)
            n_this += 1
            stats[name] += 1
            _log(f"  ✓ [{name}] {det['date']} 入库{n}块：{det['title'][:50]}")
            if n_this % 10 == 0:
                save_state(state, done)
        stats[name] = n_this if dry_run else stats[name]
        if not dry_run:
            save_state(state, done)
    return stats


# ══════════════════════════════════════════════════════════════════════════════
# 渠道 2：格隆汇 gelonghui.com
# ══════════════════════════════════════════════════════════════════════════════
GLH = "https://www.gelonghui.com"
# 注意：/user/{id}/article?page=N 的 page 参数是摆设——SSR 恒返回第 1 页 15 篇
# （survey 里"翻到 page40 仍满页返回"其实是同一页内容）。真正的翻页走 JSON API：
#   /api/community/dynamic/my-dynamics/v2?article=true&userId={id}&count=15&timestamp={cursor}
# cursor = 上一页最后一条的 createTimestamp（秒级），逆时间序往前翻。
GLH_API = (GLH + "/api/community/dynamic/my-dynamics/v2"
           "?article=true&userId={uid}&count=15{cursor}")


def parse_glh_api(raw_json: str) -> list[dict]:
    """解析动态列表 API 响应 → [{id,title,date,ts,summary}]（只保留文章类条目）。"""
    data = json.loads(raw_json)
    items = []
    for it in data.get("result") or []:
        aid, title = it.get("id"), (it.get("title") or "").strip()
        ts = it.get("createTimestamp") or 0
        # 文章类条目 type=2 / route 含 /p/；短动态没有标题，跳过
        route = it.get("route") or ""
        if not aid or not title or (it.get("type") != 2 and "/p/" not in route):
            continue
        date = f"{datetime.fromtimestamp(ts):%Y-%m-%d}" if ts else ""
        items.append({
            "id": str(aid),
            "title": title,
            "date": date,
            "ts": ts,
            "summary": (it.get("content") or "").strip(),
        })
    return items


def parse_glh_article(html: str) -> str:
    """单篇 /p/{id} → 正文纯文本（<article> 容器）。"""
    m = re.search(r"<article[^>]*>(.*?)</article>", html, re.S)
    return html_to_text(m.group(1)) if m else ""


def crawl_glh_articles(user_id: int, max_pages: int = 0) -> list[dict]:
    """timestamp 游标翻页收集全部文章元信息。空页/无新 id/游标不再前进 即到底。"""
    all_items, seen = [], set()
    cursor, page = None, 1
    while True:
        if max_pages and page > max_pages:
            break
        url = GLH_API.format(uid=user_id,
                             cursor=f"&timestamp={cursor}" if cursor else "")
        try:
            items = parse_glh_api(fetch(url))
        except Exception as e:
            _log(f"  格隆汇列表API失败 {url}: {e}")
            break
        new = [it for it in items if it["id"] not in seen]
        if not new:
            break
        for it in new:
            seen.add(it["id"])
            all_items.append(it)
        next_cursor = new[-1]["ts"]
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
        page += 1
    return all_items


def run_gelonghui(state: dict, done: set[str], failed: list[str],
                  limit: int = 0, dry_run: bool = False, max_pages: int = 0) -> dict:
    wechat_norms = load_wechat_title_norms()
    _log(f"[gelonghui] 已入库公众号标题 {len(wechat_norms)} 条（用于去重）")
    stats: dict[str, int] = {}
    for t in GLH_TARGETS:
        name = t["name"]
        stats[name] = 0
        items = crawl_glh_articles(t["user_id"], max_pages=max_pages)
        _log(f"[gelonghui] {name}(user/{t['user_id']})：列表共 {len(items)} 篇")
        n_this, n_dup = 0, 0
        for it in items:
            if limit and n_this >= limit:
                break
            sid = f"glh:{it['id']}"
            if sid in done:
                continue
            if is_dup_title(it["title"], wechat_norms):
                n_dup += 1
                done.add(sid)       # 与公众号重复的永久跳过
                continue
            url = f"{GLH}/p/{it['id']}"
            try:
                text = parse_glh_article(fetch(url))
            except Exception as e:
                failed.append(f"{sid}: 抓取失败 {e}")
                continue
            if dry_run:
                n_this += 1
                print(f"\n--- [glh/{name}] {it['title']}\n    date: {it['date']}\n"
                      f"    text[{len(text)}]: {text[:100].replace(chr(10), ' ')}...")
                continue
            ok, reason = check_quality(text)
            if not ok:
                failed.append(f"{sid}: 质检未过({reason}) {it['title'][:40]}")
                done.add(sid)
                continue
            n = do_ingest(
                text, source_id=sid, source_type="column",
                title=it["title"], author=name,
                extra_meta={
                    "author": name, "broker": t["broker"], "date": it["date"],
                    "url": url, "summary": it["summary"][:200],
                    "source": "gelonghui",
                })
            done.add(sid)
            n_this += 1
            stats[name] += 1
            _log(f"  ✓ [{name}] {it['date']} 入库{n}块：{it['title'][:50]}")
            if n_this % 10 == 0:
                save_state(state, done)
        _log(f"[gelonghui] {name}：新增 {n_this}，与公众号重复跳过 {n_dup}")
        stats[name] = n_this if dry_run else stats[name]
        if not dry_run:
            save_state(state, done)
    return stats


# ══════════════════════════════════════════════════════════════════════════════
# 渠道 3：新浪财经 意见领袖
# ══════════════════════════════════════════════════════════════════════════════
SINA_LIST = "https://finance.sina.com.cn/zl/author_article.d.html?id={uid}&page={page}"
_SINA_LINK_RE = re.compile(
    r'<a[^>]+href="(https?://finance\.sina\.com\.cn/zl/[^"]+?\.shtml)"[^>]*>(.*?)</a>',
    re.S)


def parse_sina_list(html: str) -> list[tuple[str, str]]:
    """解析作者文章 iframe 列表页 → [(url, title)]。同一篇会出现"标题+查看全文"
    两个链接，按 URL 去重、丢弃"查看全文"类锚文本。"""
    seen, out = set(), []
    for url, anchor in _SINA_LINK_RE.findall(html):
        title = html_to_text(anchor)
        if not title or "查看全文" in title:
            continue
        url = url.strip()
        if url in seen:
            continue
        seen.add(url)
        out.append((url, title))
    return out


def parse_sina_article(html: str) -> str:
    """正文 = id="artibody" 到 <!-- 正文内容 end --> 之间；剥 HTML 后去掉
    页尾"海量资讯…新浪财经APP"之类营销行。"""
    i = html.find('id="artibody"')
    if i < 0:
        return ""
    i = html.find(">", i) + 1           # 跳过 artibody 的 div 开标签本身
    j = html.find("<!-- 正文内容 end -->", i)
    seg = html[i:j] if j > 0 else html[i:i + 200_000]
    text = html_to_text(seg)
    drop = ("海量资讯", "新浪财经APP", "点击进入专题", "责任编辑")
    lines = [ln for ln in text.split("\n") if not any(k in ln for k in drop)]
    return "\n".join(lines).strip()


def _sina_date(url: str) -> str:
    m = re.search(r"/(\d{4}-\d{2}-\d{2})/", url)
    return m.group(1) if m else ""


def crawl_sina_articles(uid: str, max_pages: int = 0) -> list[tuple[str, str]]:
    """翻页收集 (url, title)。整页无新 URL 即到底。"""
    all_items, seen = [], set()
    page = 1
    while True:
        if max_pages and page > max_pages:
            break
        try:
            items = parse_sina_list(fetch(SINA_LIST.format(uid=uid, page=page)))
        except Exception as e:
            _log(f"  新浪列表页失败 uid={uid} page={page}: {e}")
            break
        new = [(u, t) for u, t in items if u not in seen]
        if not new:
            break
        for u, t in new:
            seen.add(u)
            all_items.append((u, t))
        page += 1
    return all_items


def run_sina(state: dict, done: set[str], failed: list[str], authors_filter: set[str],
             limit: int = 0, dry_run: bool = False, max_pages: int = 0) -> dict:
    stats: dict[str, int] = {}
    targets = [t for t in SINA_TARGETS
               if not authors_filter or t["name"] in authors_filter]
    for t in targets:
        name = t["name"]
        stats[name] = 0
        items = crawl_sina_articles(t["uid"], max_pages=max_pages)
        _log(f"[sina] {name}(uid={t['uid']})：列表共 {len(items)} 篇")
        n_this = 0
        for url, title in items:
            if limit and n_this >= limit:
                break
            sid = "sinazl:" + hashlib.md5(url.encode()).hexdigest()[:16]
            if sid in done:
                continue
            try:
                text = parse_sina_article(fetch(url))
            except Exception as e:
                failed.append(f"{sid}: 抓取失败 {url} {e}")
                continue
            date = _sina_date(url)
            if dry_run:
                n_this += 1
                print(f"\n--- [sina/{name}] {title}\n    date: {date}  url: {url}\n"
                      f"    text[{len(text)}]: {text[:100].replace(chr(10), ' ')}...")
                continue
            ok, reason = check_quality(text)
            if not ok:
                failed.append(f"{sid}: 质检未过({reason}) {title[:40]}")
                done.add(sid)
                continue
            n = do_ingest(
                text, source_id=sid, source_type="column",
                title=title, author=name,
                extra_meta={
                    "author": name, "broker": t["broker"], "date": date,
                    "url": url, "source": "sina_zl",
                })
            done.add(sid)
            n_this += 1
            stats[name] += 1
            _log(f"  ✓ [{name}] {date} 入库{n}块：{title[:50]}")
            if n_this % 10 == 0:
                save_state(state, done)
        stats[name] = n_this if dry_run else stats[name]
        if not dry_run:
            save_state(state, done)
    return stats


# ── 统计 / 状态 ──────────────────────────────────────────────────────────────
def show_stats() -> None:
    """rag.db 里本脚本三渠道的入库统计：各渠道各作者的文章数/分块数。"""
    import sqlite3
    db_path = os.getenv("RAG_DB_PATH", str(ROOT / "output" / "rag.db"))
    conn = sqlite3.connect(db_path, timeout=30)
    rows = conn.execute("""
        SELECT json_extract(extra_meta,'$.source') AS src,
               author,
               COUNT(DISTINCT source_id) AS docs,
               COUNT(*) AS chunks,
               MIN(json_extract(extra_meta,'$.date')),
               MAX(json_extract(extra_meta,'$.date'))
        FROM chunks
        WHERE source_type IN ('research_web','column')
          AND json_extract(extra_meta,'$.source') IN ('nxny','gelonghui','sina_zl')
        GROUP BY src, author ORDER BY src, docs DESC""").fetchall()
    conn.close()
    print(f"{'渠道':<10}{'作者':<8}{'文章':>6}{'分块':>8}  日期范围")
    for src, author, docs, chunks, dmin, dmax in rows:
        print(f"{src:<10}{author:<8}{docs:>6}{chunks:>8}  {dmin or '?'} ~ {dmax or '?'}")


def show_status() -> None:
    for src in ("nxny", "gelonghui", "sina"):
        state = load_state(src)
        print(f"[{src}] done-list：{len(state['done'])} 条"
              + (f"，作者id缓存：{state['author_ids']}" if state["author_ids"] else ""))


# ── 主入口 ───────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description="券商首席历史文章 下载+入库（nxny/格隆汇/新浪zl）")
    ap.add_argument("--sources", default="nxny,gelonghui,sina",
                    help="逗号分隔：nxny,gelonghui,sina")
    ap.add_argument("--authors", default="", help="逗号分隔的作者名过滤（缺省全部）")
    ap.add_argument("--limit", type=int, default=0, help="每个作者最多新抓 N 篇（小样验证）")
    ap.add_argument("--max-pages", type=int, default=0, help="每个列表最多翻 N 页（0=翻到底）")
    ap.add_argument("--dry-run", action="store_true", help="只抓取解析打印，不入库不记断点")
    ap.add_argument("--status", action="store_true", help="查看断点进度")
    ap.add_argument("--stats", action="store_true", help="查看 rag.db 各渠道各作者入库统计")
    args = ap.parse_args()

    if args.status:
        show_status()
        return
    if args.stats:
        show_stats()
        return

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    authors_filter = {a.strip() for a in args.authors.split(",") if a.strip()}
    failed: list[str] = []
    all_stats: dict[str, dict] = {}
    runners = {
        "nxny": lambda st, dn: run_nxny(st, dn, failed, authors_filter,
                                        limit=args.limit, dry_run=args.dry_run,
                                        max_pages=args.max_pages),
        "gelonghui": lambda st, dn: run_gelonghui(st, dn, failed,
                                                  limit=args.limit, dry_run=args.dry_run,
                                                  max_pages=args.max_pages),
        "sina": lambda st, dn: run_sina(st, dn, failed, authors_filter,
                                        limit=args.limit, dry_run=args.dry_run,
                                        max_pages=args.max_pages),
    }
    for src in sources:
        if src not in runners:
            _log(f"未知渠道 {src}，跳过")
            continue
        state = load_state(src)
        done: set[str] = set(state["done"])
        try:
            all_stats[src] = runners[src](state, done)
        except KeyboardInterrupt:
            _log("中断，保存断点后退出")
            if not args.dry_run:
                save_state(state, done)
            break
        if not args.dry_run:
            save_state(state, done)

    _log("=" * 60)
    for src, st in all_stats.items():
        _log(f"[{src}] 本次新增：" + "，".join(f"{k} {v}篇" for k, v in st.items()))
    if failed:
        _log(f"失败 {len(failed)} 条：")
        for f in failed[:50]:
            _log(f"  ✗ {f}")
        fp = ROOT / "data" / "chief_web_failed.json"
        json.dump(failed, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
        _log(f"完整失败清单已写 {fp}")


if __name__ == "__main__":
    main()
