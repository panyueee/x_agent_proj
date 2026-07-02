#!/usr/bin/env python3
"""
海外投资/宏观大牛文章下载 + 入库 RAG（依据 docs/survey_global_gurus.md 实测结论）。

覆盖源（--sources 逗号选跑，默认全跑）：
  marks     Howard Marks / Oaktree — the-complete-collection.pdf 一个文件全拿（1990–今）
  buffett   Buffett 股东信 — 1977–2003 HTML + 2004+ PDF（Sucuri 强制 brotli，requests+Brotli 已装）
  hayes     Arthur Hayes — Substack /api/v1/archive 分页 + /api/v1/posts/<slug> 全文
            （BitMEX 2015–2020 旧文已迁 Next.js 客户端渲染，feed 仅 20 条且与 Substack 重复，见 TODO）
  damodaran Aswath Damodaran — Blogger alt=rss feed 分页回填 2008–今（全文在 <description>）
  alden     Lyn Alden — wp-json/wp/v2/posts 全文（必须直连，代理会掐 TLS）
  hussman   John Hussman — wp-json/wp/v2/posts 全文（2017-10 至今；更早 wmc 档案见 TODO）

代理规则（调研文档实测，显式表 DOMAIN_MODE）：
  - lynalden.com / fedguy.com 走本机代理(127.0.0.1:1089)时 TLS 握手被掐 → 必须直连
  - blog.bitmex.com 直连不稳 → 必须走代理
  - blogspot / substack / berkshire / oaktree 等默认走代理

入库：每篇 → html 剥纯文本 → text_quality 质检 → ingest_text(source_type="guru",
skip_vectors=True，向量留 embed-all 统一生成)。元数据带 author(slug)/title/date/url。
断点续传：已入库 source_id 记 data/gurus_done.json；同域请求间隔 ≥2s。

用法：
  .venv/bin/python scripts/ingest_global_gurus.py --sources alden --limit 3 --dry-run
  .venv/bin/python scripts/ingest_global_gurus.py --sources marks,buffett
  .venv/bin/python scripts/ingest_global_gurus.py            # 全量
  .venv/bin/python scripts/ingest_global_gurus.py --status   # 断点进度
  .venv/bin/python scripts/ingest_global_gurus.py --stats    # rag.db 各 author 篇数/分块数

铁律：走 .venv/bin/python；密钥不进代码；中文注释。
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator, Optional
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

DONE_FILE = ROOT / "data" / "gurus_done.json"
PDF_DIR = ROOT / "data" / "gurus"          # 下载的 PDF 落地目录（gitignore 的 data/ 下）

# ── 代理显式表（docs/survey_global_gurus.md 实测）─────────────────────────────
PROXY_URL = os.getenv("GURUS_PROXY", "http://127.0.0.1:1089")
DOMAIN_MODE = {
    "www.lynalden.com":  "direct",   # 代理会掐 TLS 握手
    "lynalden.com":      "direct",
    "fedguy.com":        "direct",   # 同上（暂未接，规则先放着）
    "blog.bitmex.com":   "proxy",    # 直连不稳
    "www.bitmex.com":    "proxy",
}
DEFAULT_MODE = "proxy"               # blogspot/substack 等直连不可达，默认走代理

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
MIN_INTERVAL = 2.0                   # 同域请求最小间隔（秒）

_sessions: dict[str, object] = {}
_last_req: dict[str, float] = {}


def _log(m: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


def _session(mode: str):
    """按代理模式取 requests.Session（trust_env=False，代理走显式表不吃 shell 环境）。"""
    if mode not in _sessions:
        import requests
        s = requests.Session()
        s.trust_env = False
        if mode == "proxy":
            s.proxies = {"http": PROXY_URL, "https": PROXY_URL}
        _sessions[mode] = s
    return _sessions[mode]


def _throttle(domain: str) -> None:
    """同域限速：距上次请求不足 MIN_INTERVAL 则 sleep 补足。"""
    now = time.monotonic()
    last = _last_req.get(domain)
    if last is not None and now - last < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - (now - last))
    _last_req[domain] = time.monotonic()


def fetch(url: str, timeout: int = 60, stream: bool = False):
    """限速 + 按域名选代理的 GET。抛出 HTTPError 由调用方处理。"""
    domain = urlparse(url).netloc
    _throttle(domain)
    mode = DOMAIN_MODE.get(domain, DEFAULT_MODE)
    r = _session(mode).get(url, headers={"User-Agent": UA},
                           timeout=timeout, stream=stream)
    r.raise_for_status()
    return r


def http_text(url: str, timeout: int = 60) -> str:
    r = fetch(url, timeout=timeout)
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding or "utf-8"
    return r.text


def http_json(url: str, timeout: int = 60):
    return fetch(url, timeout=timeout).json()


def download_file(url: str, dest: Path) -> Path:
    """流式下载大文件（已存在且非空则跳过 = 断点续传）。"""
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    r = fetch(url, timeout=300, stream=True)
    with open(tmp, "wb") as f:
        for chunk in r.iter_content(1 << 20):
            f.write(chunk)
    tmp.rename(dest)
    return dest


# ── HTML → 纯文本（与 scripts/ingest_wechat_rss.py 同一套做法）────────────────
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
_BLOCK_RE = re.compile(r"</?(p|br|div|li|tr|h[1-6]|section|article|blockquote)[^>]*>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_MULTINL_RE = re.compile(r"\n{3,}")


def html_to_text(raw: str) -> str:
    """正文 HTML 剥纯文本：去 script/style、块级标签转换行、去标签、解实体、压空白。"""
    if not raw:
        return ""
    s = _SCRIPT_STYLE_RE.sub(" ", raw)
    s = _BLOCK_RE.sub("\n", s)
    s = _TAG_RE.sub("", s)
    s = html.unescape(s)
    s = s.replace("\xa0", " ").replace("​", "")
    lines = [_WS_RE.sub(" ", ln).strip() for ln in s.split("\n")]
    s = "\n".join(ln for ln in lines if ln)
    return _MULTINL_RE.sub("\n\n", s).strip()


def _cdata_unwrap(s: str) -> str:
    m = re.search(r"<!\[CDATA\[(.*?)\]\]>", s, re.S)
    return m.group(1) if m else s


def _first_tag(block: str, *names: str) -> str:
    """XML 片段里取第一个匹配标签的内层文本（CDATA 解包）。"""
    for name in names:
        pat = re.compile(rf"<{re.escape(name)}\b[^>]*>(.*?)</{re.escape(name)}>", re.S | re.I)
        m = pat.search(block)
        if m:
            return _cdata_unwrap(m.group(1)).strip()
    return ""


def _maybe_unescape(s: str) -> str:
    """RSS description 里若是实体转义的 HTML（&lt;p&gt;…），先解一层实体再剥标签。"""
    if s.count("&lt;") > 3 and s.count("<") < 3:
        return html.unescape(s)
    return s


# ── 断点续传 done-list（风格同 data/wechat_done.json）────────────────────────
def _load_done() -> set[str]:
    if DONE_FILE.exists():
        try:
            return set(json.load(open(DONE_FILE, encoding="utf-8")))
        except Exception:
            return set()
    return set()


def _save_done(done: set[str]) -> None:
    DONE_FILE.parent.mkdir(parents=True, exist_ok=True)
    json.dump(sorted(done), open(DONE_FILE, "w", encoding="utf-8"),
              ensure_ascii=False, indent=0)


def source_id_for(author: str, url: str) -> str:
    """稳定 source_id：guru:<author>:<md5(url)>。"""
    return f"guru:{author}:{hashlib.md5(url.encode('utf-8')).hexdigest()}"


# ── RAG 绑定（延迟导入；测试时可直接替换这两个模块级变量）─────────────────────
_rag_ingest: Optional[Callable] = None
_rag_quality: Optional[Callable] = None


def _bind_rag() -> None:
    global _rag_ingest, _rag_quality
    if _rag_ingest is None:
        from x_agent.rag import ingest_text, text_quality
        _rag_ingest, _rag_quality = ingest_text, text_quality


def _patch_meta(sid_prefix: str, meta: dict) -> None:
    """给已入库 chunks 的 extra_meta 合并补充字段（ingest_pdf 不支持自定义 meta，事后补）。"""
    from x_agent.rag import _db
    db = _db()
    db.execute(
        "UPDATE chunks SET extra_meta = json_patch(extra_meta, ?) WHERE source_id LIKE ?",
        (json.dumps(meta, ensure_ascii=False), sid_prefix + "%"),
    )
    db.commit()


# ── 通用文章处理循环 ──────────────────────────────────────────────────────────
def process_articles(
    name: str,
    author: str,
    items: Iterator[dict],
    done: set[str],
    limit: int = 0,
    dry_run: bool = False,
) -> dict:
    """
    items 逐条产出 {"title","url","date","get_text": callable}（get_text 延迟取正文，
    done 命中的直接跳过、不发正文请求）。返回 {"new","skipped_quality","failed"}。
    """
    new, skipped_q, failed = 0, 0, []
    try:
        for it in items:
            if limit and new >= limit:
                break
            url = (it.get("url") or "").strip()
            if not url:
                continue
            sid = source_id_for(author, url)
            if sid in done:
                continue
            try:
                text = it["get_text"]()
            except Exception as e:
                _log(f"  ✗ [{name}] 取正文失败 {url} : {type(e).__name__} {str(e)[:80]}")
                failed.append((url, f"{type(e).__name__}: {str(e)[:80]}"))
                continue

            if dry_run:
                preview = text[:100].replace("\n", " ")
                print(f"\n--- [{name}] {it['title']}\n    url : {url}\n    date: {it.get('date','')}\n"
                      f"    text[{len(text)}]: {preview}...")
                new += 1
                continue

            _bind_rag()
            ok, reason = _rag_quality(text)
            if not ok:
                _log(f"  ✗ [{name}] 质检未过({reason})：{it['title'][:50]}")
                skipped_q += 1
                failed.append((url, f"quality:{reason}"))
                continue

            n = _rag_ingest(
                text,
                source_id=sid,
                source_type="guru",
                title=it["title"],
                author=author,
                extra_meta={
                    "url": url,
                    "date": it.get("date", ""),
                    "source_site": it.get("site", name),
                },
                skip_vectors=True,   # 向量留 embed-all 统一生成
            )
            done.add(sid)
            new += 1
            _log(f"  ✓ [{name}] 入库 {n} 块：{it['title'][:60]}")
            if new % 10 == 0:
                _save_done(done)
    finally:
        if not dry_run:
            _save_done(done)
    return {"new": new, "skipped_quality": skipped_q, "failed": failed}


# ── 1. Howard Marks / Oaktree：全集 PDF ──────────────────────────────────────
MARKS_PDF_URL = ("https://www.oaktreecapital.com/docs/default-source/memos/"
                 "the-complete-collection.pdf")
MARKS_SID = "guru:howard_marks:complete-collection"


def run_marks(done: set[str], limit: int = 0, dry_run: bool = False) -> dict:
    if MARKS_SID in done:
        _log("[marks] 已入库，跳过")
        return {"new": 0, "skipped_quality": 0, "failed": []}
    if dry_run:
        print(f"--- [marks] 计划下载并入库全集 PDF（~26MB）：{MARKS_PDF_URL}")
        return {"new": 1, "skipped_quality": 0, "failed": []}
    dest = PDF_DIR / "oaktree_memos_the_complete_collection.pdf"
    try:
        _log(f"[marks] 下载全集 PDF → {dest.name}")
        download_file(MARKS_PDF_URL, dest)
        from x_agent.rag import ingest_pdf
        n = ingest_pdf(
            str(dest),
            title="Oaktree Memos — The Complete Collection (1990–present)",
            author="howard_marks",
            source_id=MARKS_SID,
            source_type="guru",
            skip_vectors=True,
        )
        _patch_meta(MARKS_SID, {"url": MARKS_PDF_URL, "date": "1990–present",
                                "source_site": "oaktreecapital.com"})
        done.add(MARKS_SID)
        _save_done(done)
        _log(f"[marks] ✓ 入库 {n} 块")
        return {"new": 1, "skipped_quality": 0, "failed": []}
    except Exception as e:
        _log(f"[marks] ✗ 失败：{type(e).__name__} {str(e)[:120]}")
        return {"new": 0, "skipped_quality": 0,
                "failed": [(MARKS_PDF_URL, f"{type(e).__name__}: {str(e)[:80]}")]}


# ── 2. Buffett 股东信：1977–2003 HTML + 2004+ PDF ────────────────────────────
BUFFETT_INDEX = "https://www.berkshirehathaway.com/letters/letters.html"


def iter_buffett_links(index_html: str) -> list[tuple[str, str]]:
    """从索引页取 (year, href)。1977.html / 2004ltr.pdf 两种形态，去重保序。"""
    out, seen = [], set()
    for href in re.findall(r"href=['\"]([^'\"]+)['\"]", index_html, re.I):
        m = re.match(r"((?:19|20)\d\d)(?:ltr)?\.(html?|pdf)$", href.strip())
        if m and href not in seen:
            seen.add(href)
            out.append((m.group(1), href.strip()))
    return out


def _buffett_letter_text(url: str) -> str:
    """HTML 信全文；若页面只是壳（太短且指向 PDF），转抓 PDF 文本。"""
    page = http_text(url)
    text = html_to_text(page)
    if len(text) > 3000:
        return text
    # 壳页：找页内 PDF 链接
    m = re.search(r"href=['\"]([^'\"]+\.pdf)['\"]", page, re.I)
    if m:
        pdf_url = urljoin(url, m.group(1))
        dest = PDF_DIR / "buffett" / Path(urlparse(pdf_url).path).name
        download_file(pdf_url, dest)
        return _pdf_to_text(dest)
    return text


def _pdf_to_text(path: Path) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    return "\n\n".join((p.extract_text() or "") for p in reader.pages)


def run_buffett(done: set[str], limit: int = 0, dry_run: bool = False) -> dict:
    links = iter_buffett_links(http_text(BUFFETT_INDEX))
    _log(f"[buffett] 索引解析到 {len(links)} 封信")

    def gen():
        for year, href in links:
            url = urljoin(BUFFETT_INDEX, href)
            if href.endswith(".pdf"):
                def get_pdf(u=url):
                    dest = PDF_DIR / "buffett" / Path(urlparse(u).path).name
                    download_file(u, dest)
                    return _pdf_to_text(dest)
                get_text = get_pdf
            else:
                get_text = (lambda u=url: _buffett_letter_text(u))
            yield {
                "title": f"Berkshire Hathaway Shareholder Letter {year}",
                "url": url, "date": year, "site": "berkshirehathaway.com",
                "get_text": get_text,
            }

    return process_articles("buffett", "warren_buffett", gen(), done, limit, dry_run)


# ── 3. Arthur Hayes：Substack archive API 分页 + 逐篇取全文 ───────────────────
HAYES_BASE = "https://cryptohayes.substack.com"


def iter_hayes(page_size: int = 20) -> Iterator[dict]:
    offset = 0
    while True:
        batch = http_json(f"{HAYES_BASE}/api/v1/archive?sort=new&offset={offset}&limit={page_size}")
        if not batch:
            break
        for it in batch:
            if it.get("audience") not in (None, "everyone"):
                continue  # 理论上他全免费，防御一下
            slug = it.get("slug", "")
            url = it.get("canonical_url") or f"{HAYES_BASE}/p/{slug}"
            yield {
                "title": (it.get("title") or slug).strip(),
                "url": url,
                "date": (it.get("post_date") or "")[:10],
                "site": "cryptohayes.substack.com",
                "get_text": (lambda s=slug: html_to_text(
                    http_json(f"{HAYES_BASE}/api/v1/posts/{s}").get("body_html") or "")),
            }
        offset += len(batch)
        if len(batch) < page_size:
            break


def run_hayes(done: set[str], limit: int = 0, dry_run: bool = False) -> dict:
    # TODO: BitMEX 2015–2020 Crypto Trader Digest 旧文——blog.bitmex.com 已迁
    # Next.js/Contentful 且列表/正文均客户端渲染，category feed 仅 20 条且全部与
    # Substack 重复（实测 2026-07）。需要真实浏览器或逆向其内部 API 才能回填。
    return process_articles("hayes", "arthur_hayes", iter_hayes(), done, limit, dry_run)


# ── 4. Damodaran：Blogger alt=rss feed 分页（全文在 description）──────────────
DAMODARAN_FEED = ("https://aswathdamodaran.blogspot.com/feeds/posts/default"
                  "?alt=rss&max-results={n}&start-index={i}")


def parse_blogger_rss(xml: str) -> list[dict]:
    """Blogger alt=rss：<item> 里 title/link/pubDate/description(全文 HTML)。"""
    out = []
    for m in re.finditer(r"<item>(.*?)</item>", xml, re.S):
        block = m.group(1)
        title = html.unescape(_TAG_RE.sub("", _first_tag(block, "title"))).strip()
        link = _first_tag(block, "link").strip()
        pub = _first_tag(block, "pubDate").strip()
        desc = _first_tag(block, "description")
        out.append({
            "title": title, "url": link, "date": _rfc822_to_iso(pub),
            "site": "aswathdamodaran.blogspot.com",
            "get_text": (lambda d=desc: html_to_text(_maybe_unescape(d))),
        })
    return out


def _rfc822_to_iso(s: str) -> str:
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(s).date().isoformat()
    except Exception:
        return s


def iter_damodaran(page_size: int = 150) -> Iterator[dict]:
    # 注意：Blogger alt=rss 单次实际最多返回 50 条（max-results 再大也没用），
    # 所以不能用 len(items) < page_size 判尾页，必须翻到空页为止。
    start = 1
    while True:
        xml = http_text(DAMODARAN_FEED.format(n=page_size, i=start))
        items = parse_blogger_rss(xml)
        if not items:
            break
        yield from items
        start += len(items)


def run_damodaran(done: set[str], limit: int = 0, dry_run: bool = False) -> dict:
    return process_articles("damodaran", "aswath_damodaran", iter_damodaran(),
                            done, limit, dry_run)


# ── 5/6. WordPress 同构：Lyn Alden + Hussman（wp-json 全文）───────────────────
def iter_wp_posts(base: str, site: str, per_page: int = 50) -> Iterator[dict]:
    """wp-json/wp/v2/posts 分页；content.rendered 即全文 HTML。翻过尾页返回 400 视为结束。"""
    import requests
    page = 1
    while True:
        url = f"{base}/wp-json/wp/v2/posts?per_page={per_page}&page={page}&orderby=date&order=desc"
        try:
            posts = http_json(url)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 400:
                break  # rest_post_invalid_page_number
            raise
        if not posts:
            break
        for p in posts:
            yield {
                "title": html_to_text(p.get("title", {}).get("rendered", "")),
                "url": p.get("link", ""),
                "date": (p.get("date") or "")[:10],
                "site": site,
                "get_text": (lambda h=p.get("content", {}).get("rendered", ""): html_to_text(h)),
            }
        if len(posts) < per_page:
            break
        page += 1


def run_alden(done: set[str], limit: int = 0, dry_run: bool = False) -> dict:
    return process_articles(
        "alden", "lyn_alden",
        iter_wp_posts("https://www.lynalden.com", "lynalden.com"),
        done, limit, dry_run)


def run_hussman(done: set[str], limit: int = 0, dry_run: bool = False) -> dict:
    # TODO: 2003–2017 的旧 wmc 档案（hussmanfunds.com/market-comment-archive/ 老式
    # 静态页 ~700 篇）不在 wp-json 里，需另写老页面爬虫回填。
    return process_articles(
        "hussman", "john_hussman",
        iter_wp_posts("https://www.hussmanfunds.com", "hussmanfunds.com"),
        done, limit, dry_run)


SOURCES: dict[str, Callable] = {
    "marks": run_marks,
    "buffett": run_buffett,
    "hayes": run_hayes,
    "damodaran": run_damodaran,
    "alden": run_alden,
    "hussman": run_hussman,
}


# ── 状态 / 统计 ───────────────────────────────────────────────────────────────
def show_status() -> None:
    done = _load_done()
    by_author: dict[str, int] = {}
    for sid in done:
        parts = sid.split(":")
        if len(parts) >= 3:
            by_author[parts[1]] = by_author.get(parts[1], 0) + 1
    print(f"done-list（{DONE_FILE}）：共 {len(done)} 篇")
    for a, n in sorted(by_author.items()):
        print(f"  {a:20s} {n}")


def show_stats() -> None:
    """rag.db 里 source_type='guru' 的各 author 文章数（去分页后缀）与分块数。"""
    from x_agent.rag import _db
    db = _db()
    rows = db.execute(
        "SELECT author, source_id, COUNT(*) FROM chunks "
        "WHERE source_type='guru' GROUP BY author, source_id"
    ).fetchall()
    stats: dict[str, dict] = {}
    for author, sid, n in rows:
        base = re.sub(r":p\d+-\d+$", "", sid)   # PDF 分批 source_id 归并回一篇
        s = stats.setdefault(author, {"articles": set(), "chunks": 0})
        s["articles"].add(base)
        s["chunks"] += n
    print(f"{'author':22s} {'articles':>8s} {'chunks':>8s}")
    tot_a = tot_c = 0
    for a in sorted(stats):
        na, nc = len(stats[a]["articles"]), stats[a]["chunks"]
        tot_a += na
        tot_c += nc
        print(f"{a:22s} {na:8d} {nc:8d}")
    print(f"{'TOTAL':22s} {tot_a:8d} {tot_c:8d}")


# ── main ─────────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description="海外大牛文章下载入库 RAG")
    ap.add_argument("--sources", default=",".join(SOURCES),
                    help=f"逗号分隔：{','.join(SOURCES)}（默认全跑）")
    ap.add_argument("--limit", type=int, default=0, help="每源最多入库 N 篇（小样验证）")
    ap.add_argument("--dry-run", action="store_true", help="只抓取解析打印，不入库")
    ap.add_argument("--status", action="store_true", help="看 done-list 断点进度")
    ap.add_argument("--stats", action="store_true", help="看 rag.db 各 author 篇数/分块数")
    args = ap.parse_args()

    if args.status:
        show_status()
        return
    if args.stats:
        show_stats()
        return

    names = [s.strip() for s in args.sources.split(",") if s.strip()]
    bad = [n for n in names if n not in SOURCES]
    if bad:
        _log(f"未知源：{bad}，可选：{list(SOURCES)}")
        sys.exit(1)

    done = _load_done()
    summary = {}
    for name in names:
        _log(f"===== {name} =====")
        try:
            summary[name] = SOURCES[name](done, limit=args.limit, dry_run=args.dry_run)
        except KeyboardInterrupt:
            _log("中断，已保存断点")
            _save_done(done)
            break
        except Exception as e:
            _log(f"[{name}] ✗ 整源失败：{type(e).__name__} {str(e)[:150]}")
            summary[name] = {"new": 0, "skipped_quality": 0,
                             "failed": [("(source)", f"{type(e).__name__}: {str(e)[:100]}")]}

    print("\n===== 本次汇总 =====")
    for name, s in summary.items():
        print(f"{name:10s} 新增 {s['new']:4d} 篇，质检拦截 {s['skipped_quality']}，"
              f"失败 {len(s['failed'])}")
        for url, err in s["failed"][:10]:
            print(f"    ✗ {url} — {err}")
    if not args.dry_run:
        print()
        show_stats()


if __name__ == "__main__":
    main()
