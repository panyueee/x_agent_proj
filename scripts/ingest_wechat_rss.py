#!/usr/bin/env python3
"""
微信公众号文章入库（经 WeWe RSS 中转的全文 RSS/Atom feed）→ RAG。

消费侧脚本：读 RSS → 解析每篇文章 → HTML 剥成纯文本 → text_quality 质检 →
ingest_text 入库（source_type="wechat"，skip_vectors=True，向量留后统一 embed）。
断点续传：已入库的 source_id 记入 data/wechat_done.json，重复轮询自动跳过。

数据来源：WeWe RSS（自建服务，用微信读书只读 token 订阅公众号并输出全文 RSS）。
每个 feed 对应一个公众号，公众号名取 channel/feed 级 <title>（不是文章级），
作为 author 与 extra_meta["publication"]，逐篇复用。

⚠️ 实测状态：本脚本**尚未对真实 WeWe RSS feed 实测**，因为它依赖用户先自行部署
   WeWe RSS 并用微信读书扫码登录（见 docs/wewe_rss_setup.md）。当前仅通过内置
   代表性 RSS 样例完成**解析验证**（--selftest 通过）。待用户部署 WeWe RSS +
   微信读书登录、拿到真实 RSS URL 后，用 --dry-run 做首轮真实解析核对，再正式入库。

用法：
  .venv/bin/python scripts/ingest_wechat_rss.py --selftest          # 样例解析自测（无需联网）
  .venv/bin/python scripts/ingest_wechat_rss.py --feeds URL1,URL2   # 直接指定 feed
  .venv/bin/python scripts/ingest_wechat_rss.py                     # 从 data/wechat_feeds.txt 读订阅
  .venv/bin/python scripts/ingest_wechat_rss.py --dry-run --limit 3 # 只解析打印，不入库
  .venv/bin/python scripts/ingest_wechat_rss.py --status            # 看断点进度

铁律：走 .venv/bin/python；requests 直连绕过系统代理；中文注释。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

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

FEEDS_FILE = ROOT / "data" / "wechat_feeds.txt"
DONE_FILE = ROOT / "data" / "wechat_done.json"
IMG_ROOT = ROOT / "data" / "wechat_images"   # 每篇一个子目录存配图(图表)


def _log(m: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


# ── HTML → 纯文本 ─────────────────────────────────────────────────────────────
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
_BLOCK_RE = re.compile(r"</?(p|br|div|li|tr|h[1-6]|section|article|blockquote)[^>]*>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_MULTINL_RE = re.compile(r"\n{3,}")


def html_to_text(raw: str) -> str:
    """把文章正文 HTML 剥成纯文本：去 script/style、块级标签转换行、去标签、
    解 HTML 实体、压缩空白。"""
    if not raw:
        return ""
    s = _SCRIPT_STYLE_RE.sub(" ", raw)
    # 块级元素边界转换行，保留段落结构
    s = _BLOCK_RE.sub("\n", s)
    s = _TAG_RE.sub("", s)          # 去掉剩余标签
    s = html.unescape(s)            # &amp; &#8220; 等实体还原
    s = s.replace(" ", " ").replace("​", "")  # nbsp / 零宽空格
    # 逐行 strip，压缩行内空白与多余空行
    lines = [_WS_RE.sub(" ", ln).strip() for ln in s.split("\n")]
    s = "\n".join(ln for ln in lines if ln)
    s = _MULTINL_RE.sub("\n\n", s)
    return s.strip()


# ── 文章配图（图表）提取与保存 ──────────────────────────────────────────────
# 研报文章里的图表(PMI/汇率/GDP走势图)是核心。先把图存下来供 RAG 调出显示 +
# 将来在 M5 上 OCR 取图内数据。装饰图(页眉/二维码)也会一并存，OCR 时用质检过滤。
_IMG_SRC_RE = re.compile(r'<img\b[^>]*?\bsrc=["\']([^"\']+)["\']', re.I)
_MMBIZ_RE = re.compile(r'https?://mmbiz\.qpic\.cn/[^\s"\'<>\\]+')


def _img_key(src: str) -> str:
    """归一化去重键：去查询串 + 去尾部尺寸段（mmbiz 同图 /640 /0 视为一张）。"""
    s = re.split(r"[?#]", src)[0]
    return re.sub(r"/\d+$", "", s)


def extract_img_srcs(html_body: str) -> list[str]:
    """提取配图地址：<img src> + 裸 mmbiz URL；按归一化 key 去重保序（跳过 base64）。"""
    if not html_body:
        return []
    srcs = []
    for m in _IMG_SRC_RE.finditer(html_body):
        srcs.append(html.unescape(m.group(1)).strip())
    for m in _MMBIZ_RE.finditer(html_body):
        srcs.append(m.group(0))
    seen, out = set(), []
    for s in srcs:
        s = re.split(r"\\x|\\u", s)[0].strip()   # 清掉转义尾巴
        if not s.startswith("http"):             # 跳过 base64/相对路径
            continue
        k = _img_key(s)
        if k not in seen:
            seen.add(k); out.append(s)
    return out


def _img_ext(src: str) -> str:
    m = re.search(r"wx_fmt=([a-z]+)", src) or re.search(r"\.([a-z]{3,4})(?:\?|$)", src)
    return (m.group(1) if m else "jpg").lower()


def save_article_images(srcs: list[str], dest_dir: Path,
                        referer: str = "https://mp.weixin.qq.com/") -> int:
    """下载/解码文章配图到 dest_dir。mmbiz URL 带 referer 下载，base64 解码。
    已存在则跳过(断点续传)，<1KB 的图标/占位跳过。返回成功保存数。"""
    if not srcs:
        return 0
    import requests
    dest_dir.mkdir(parents=True, exist_ok=True)
    sess = requests.Session(); sess.trust_env = False
    hdr = {"User-Agent": "Mozilla/5.0", "Referer": referer}
    saved = 0
    for i, src in enumerate(srcs):
        try:
            if src.startswith("data:image/"):
                m = re.match(r"data:image/([a-z]+);base64,(.*)", src, re.S)
                if not m:
                    continue
                ext, data = m.group(1), base64.b64decode(m.group(2))
            else:
                r = sess.get(src, headers=hdr, timeout=30)
                if r.status_code != 200 or not r.content:
                    continue
                data, ext = r.content, _img_ext(src)
            if len(data) < 1024:        # 太小多半是图标/占位，跳过
                continue
            fp = dest_dir / f"{i:03d}.{ext}"
            if fp.exists() and fp.stat().st_size > 0:
                saved += 1
                continue
            fp.write_bytes(data)
            saved += 1
        except Exception:
            continue
    return saved


# ── RSS/Atom 解析 ────────────────────────────────────────────────────────────
# feedparser 未安装，标准库 + 正则解析为**主路径**（--selftest 实际走这里）；
# 若环境装了 feedparser 则优先用它（更鲁棒）。
try:
    import feedparser  # type: ignore
    _HAS_FEEDPARSER = True
except Exception:
    _HAS_FEEDPARSER = False


def _cdata_unwrap(s: str) -> str:
    """去掉 <![CDATA[ ... ]]> 包裹（WeWe RSS 正文几乎总是 CDATA 包裹）。"""
    m = re.search(r"<!\[CDATA\[(.*?)\]\]>", s, re.S)
    return m.group(1) if m else s


def _first_tag(block: str, *names: str) -> str:
    """在一段 XML 里取第一个匹配标签的内层文本（含命名空间，如 content:encoded）。
    返回已 CDATA 解包的原始字符串（可能仍含 HTML）。"""
    for name in names:
        # 命名空间冒号需转义；允许标签带属性
        pat = re.compile(rf"<{re.escape(name)}\b[^>]*>(.*?)</{re.escape(name)}>", re.S | re.I)
        m = pat.search(block)
        if m:
            return _cdata_unwrap(m.group(1)).strip()
    return ""


def _channel_title(xml: str) -> str:
    """取 channel/feed 级 <title>（即公众号名）。剥离 item/entry 段后取第一个 title，
    避免误取文章标题。"""
    head = re.split(r"<(?:item|entry)\b", xml, maxsplit=1, flags=re.I)[0]
    raw = _first_tag(head, "title")
    return html.unescape(_TAG_RE.sub("", raw)).strip()


def _parse_with_stdlib(xml: str) -> tuple[str, list[dict]]:
    """标准库正则解析。返回 (公众号名, 文章列表)。
    同时兼容 RSS 2.0 (<item>/<link>/<pubDate>/<content:encoded>) 与
    Atom (<entry>/<link href>/<published>/<content>)。"""
    publication = _channel_title(xml)
    items: list[dict] = []

    # 逐个抽出 item 或 entry 块
    for m in re.finditer(r"<(item|entry)\b[^>]*>(.*?)</\1>", xml, re.S | re.I):
        is_atom = m.group(1).lower() == "entry"
        block = m.group(2)

        title = html.unescape(_TAG_RE.sub("", _first_tag(block, "title"))).strip()

        # link：RSS 是 <link>文本</link>；Atom 是 <link href="..."/>
        link = _first_tag(block, "link")
        if not link:  # Atom 自闭合 link
            lm = re.search(r'<link\b[^>]*\bhref=["\']([^"\']+)["\']', block, re.I)
            link = html.unescape(lm.group(1)).strip() if lm else ""
        link = link.strip()

        published = (_first_tag(block, "pubDate", "published", "updated", "dc:date")).strip()

        # 正文：全文 feed 放在 content:encoded / content，description/summary 常只是摘要。
        # 规则：取存在且更长者。
        body_full = _first_tag(block, "content:encoded", "content")
        body_brief = _first_tag(block, "description", "summary")
        raw_body = body_full if len(body_full) >= len(body_brief) else body_brief

        items.append({
            "title": title,
            "link": link,
            "published": published,
            "text": html_to_text(raw_body),
            "html": raw_body,   # 原始HTML，供提取图表图片
        })

    return publication, items


def _parse_with_feedparser(xml: str) -> tuple[str, list[dict]]:
    """feedparser 路径（若可用）。"""
    d = feedparser.parse(xml)
    publication = html.unescape(_TAG_RE.sub("", d.feed.get("title", ""))).strip()
    items: list[dict] = []
    for e in d.entries:
        # 正文：content 列表（全文）优先，退回 summary
        raw_body = ""
        if e.get("content"):
            raw_body = max((c.get("value", "") for c in e["content"]), key=len, default="")
        if len(raw_body) < len(e.get("summary", "")):
            raw_body = e.get("summary", "")
        items.append({
            "title": html.unescape(_TAG_RE.sub("", e.get("title", ""))).strip(),
            "link": (e.get("link", "") or "").strip(),
            "published": (e.get("published", "") or e.get("updated", "")).strip(),
            "text": html_to_text(raw_body),
            "html": raw_body,   # 原始HTML，供提取图表图片
        })
    return publication, items


def parse_feed(xml: str) -> tuple[str, list[dict]]:
    """解析 RSS/Atom 文本 → (公众号名, [{title, link, published, text}, ...])。"""
    if _HAS_FEEDPARSER:
        try:
            pub, items = _parse_with_feedparser(xml)
            if items:
                return pub, items
        except Exception as e:
            _log(f"feedparser 解析失败，回退标准库：{e}")
    return _parse_with_stdlib(xml)


# ── 入库主逻辑 ───────────────────────────────────────────────────────────────
def source_id_for(url: str) -> str:
    """稳定 source_id：wechat:<md5(article_url)>。URL 原样，不做归一化。"""
    return "wechat:" + hashlib.md5(url.encode("utf-8")).hexdigest()


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


def _read_feed_urls(cli_feeds: Optional[str]) -> list[str]:
    if cli_feeds:
        return [u.strip() for u in cli_feeds.split(",") if u.strip()]
    if FEEDS_FILE.exists():
        urls = []
        for ln in FEEDS_FILE.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                urls.append(ln)
        return urls
    return []


def _fetch(url: str) -> str:
    """直连拉取 feed 文本（绕过系统代理）。"""
    import requests
    s = requests.Session()
    s.trust_env = False  # 忽略环境里的 http(s)_proxy
    r = s.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0 wewe-rss-ingest"})
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    return r.text


def run(feeds: list[str], limit: int = 0, dry_run: bool = False,
        save_images: bool = False) -> None:
    done = _load_done()
    total_new = 0
    for feed_url in feeds:
        _log(f"拉取 feed：{feed_url}")
        try:
            xml = _fetch(feed_url)
        except Exception as e:
            _log(f"  ✗ 拉取失败：{e}")
            continue
        publication, items = parse_feed(xml)
        _log(f"  公众号：{publication or '(未识别)'}，解析到 {len(items)} 篇")

        count = 0
        for it in items:
            if limit and count >= limit:
                break
            url = it["link"]
            if not url:
                _log(f"  跳过（无链接）：{it['title'][:30]}")
                continue
            sid = source_id_for(url)
            if sid in done:
                continue
            count += 1

            if dry_run:
                preview = it["text"][:120].replace("\n", " ")
                print(f"\n--- {it['title']}\n    link: {url}\n    date: {it['published']}\n"
                      f"    pub : {publication}\n    text[{len(it['text'])}]: {preview}...")
                continue

            # 延迟导入 rag，--selftest/--dry-run 时不触碰 DB / 向量依赖
            from x_agent.rag import ingest_text, text_quality

            ok, reason = text_quality(it["text"])
            if not ok:
                _log(f"  ✗ 质检未过（{reason}）：{it['title'][:30]}")
                continue

            # 文章配图(图表)：默认只存图片URL列表(mmbiz URL 长期有效)→本地零膨胀。
            # 将来 M5 按 URL 下载+OCR 取图内数据；RAG 可按 URL 展示。base64 内联图丢弃。
            img_urls = extract_img_srcs(it.get("html", ""))[:120]
            img_dir = ""
            if save_images and img_urls:  # 显式 --download-images 才落地本地(体积大)
                d = IMG_ROOT / hashlib.md5(sid.encode()).hexdigest()[:16]
                if save_article_images(img_urls, d):
                    img_dir = str(d)

            n = ingest_text(
                it["text"],
                source_id=sid,
                source_type="wechat",
                title=it["title"],
                author=publication,
                extra_meta={
                    "publication": publication,
                    "url": url,
                    "date": it["published"],
                    "source": "wewe-rss",
                    "image_urls": img_urls,      # 图表URL列表, 待M5下载OCR
                    "image_count": len(img_urls),
                    "image_dir": img_dir,        # 仅 --download-images 时非空
                },
                skip_vectors=True,  # 无 VOYAGE key，向量留后统一 embed
            )
            done.add(sid)  # 仅入库成功后记 done
            total_new += 1
            _log(f"  ✓ 入库 {n} 块 + {len(img_urls)} 图URL：{it['title'][:40]}")

        if not dry_run:
            _save_done(done)

    _log(f"完成。本次新增文章 {total_new} 篇，累计 done {len(done)} 篇。")


def show_status() -> None:
    done = _load_done()
    feeds = _read_feed_urls(None)
    print(f"已入库文章数（done）：{len(done)}")
    print(f"订阅 feed 数（{FEEDS_FILE}）：{len(feeds)}")
    for u in feeds:
        print(f"  - {u}")
    if not feeds:
        print("  （data/wechat_feeds.txt 为空或不存在，部署 WeWe RSS 后填入真实 RSS URL）")


# ── 自测：内置代表性样例，验证解析（不联网、不入库）─────────────────────────────
_SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>一瑜中的</title>
    <link>https://weread.example/feeds/yiyu</link>
    <description>华创宏观 张瑜团队</description>
    <item>
      <title>如何看待近期出口数据的超预期</title>
      <link>https://mp.weixin.qq.com/s/AbCdEf12345</link>
      <pubDate>Mon, 30 Jun 2026 08:00:00 GMT</pubDate>
      <description><![CDATA[<p>摘要：出口同比回升……</p>]]></description>
      <content:encoded><![CDATA[
        <section><p>核心观点：6&nbsp;月出口同比增速回升至&#8220;两位数&#8221;，
        主要受<b>基数效应</b>与新兴市场需求拉动。</p>
        <p>风险提示：外需回落超预期。</p>
        <script>trackEvent();</script></section>
      ]]></content:encoded>
    </item>
  </channel>
</rss>"""

_SAMPLE_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>李迅雷金融与投资</title>
  <link href="https://weread.example/feeds/lixunlei"/>
  <entry>
    <title>当前宏观形势下的资产配置思考</title>
    <link href="https://mp.weixin.qq.com/s/XyZ98765"/>
    <published>2026-06-29T10:30:00Z</published>
    <summary>简短摘要</summary>
    <content type="html"><![CDATA[
      <div><p>正文：在利率下行周期中，权益与债券的相对性价比正在变化。</p>
      <p>结论：结构性机会大于总量机会。</p></div>
    ]]></content>
  </entry>
</feed>"""


def selftest() -> bool:
    ok = True

    def check(cond: bool, msg: str):
        nonlocal ok
        print(("  ✓ " if cond else "  ✗ ") + msg)
        if not cond:
            ok = False

    print("[selftest] RSS 2.0 (content:encoded + CDATA)：")
    pub, items = _parse_with_stdlib(_SAMPLE_RSS)
    check(pub == "一瑜中的", f"公众号名(channel-title) = {pub!r}")
    check(len(items) == 1, f"文章数 = {len(items)}")
    a = items[0]
    check(a["title"] == "如何看待近期出口数据的超预期", f"title = {a['title']!r}")
    check(a["link"] == "https://mp.weixin.qq.com/s/AbCdEf12345", f"link = {a['link']!r}")
    check(a["published"].startswith("Mon, 30 Jun 2026"), f"published = {a['published']!r}")
    # 取 content:encoded（全文），非 description（摘要）
    check("核心观点" in a["text"], "正文取自 content:encoded（含‘核心观点’）")
    check("摘要：出口同比回升" not in a["text"], "未误取 description 摘要")
    check("<" not in a["text"] and ">" not in a["text"], "HTML 标签已全部剥除")
    check("trackEvent" not in a["text"], "<script> 内容已去除")
    check("“两位数”" in a["text"], "HTML 实体(&#8220;)已解码")
    check("6 月" in a["text"], "&nbsp; 已转普通空格")

    print("[selftest] source_id 稳定性：")
    sid1 = source_id_for(a["link"])
    sid2 = source_id_for(a["link"])
    check(sid1 == sid2 and sid1.startswith("wechat:") and len(sid1) == 7 + 32,
          f"source_id = {sid1}")

    print("[selftest] Atom (content + CDATA)：")
    pub2, items2 = _parse_with_stdlib(_SAMPLE_ATOM)
    check(pub2 == "李迅雷金融与投资", f"公众号名(feed-title) = {pub2!r}")
    check(len(items2) == 1, f"文章数 = {len(items2)}")
    b = items2[0]
    check(b["title"] == "当前宏观形势下的资产配置思考", f"title = {b['title']!r}")
    check(b["link"] == "https://mp.weixin.qq.com/s/XyZ98765", f"link(atom href) = {b['link']!r}")
    check(b["published"].startswith("2026-06-29"), f"published = {b['published']!r}")
    check("利率下行周期" in b["text"], "正文取自 content（含正文关键词）")
    check("简短摘要" not in b["text"], "未误取 summary 摘要")

    print("[selftest] text_quality 兼容性（离线近似，正文足够长且多样）：")
    # 不导入 rag（避免依赖），仅确认样例正文长度/多样性达标的直觉
    check(len(b["text"]) > 40, f"Atom 正文纯文本长度 = {len(b['text'])}")

    print("\n[selftest] " + ("全部通过 ✅" if ok else "存在失败 ❌"))
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(description="微信公众号(WeWe RSS)文章入库 RAG")
    ap.add_argument("--feeds", help="逗号分隔的 RSS URL；缺省从 data/wechat_feeds.txt 读")
    ap.add_argument("--limit", type=int, default=0, help="每个 feed 最多处理 N 篇（小样）")
    ap.add_argument("--dry-run", action="store_true", help="只解析打印，不入库")
    ap.add_argument("--status", action="store_true", help="查看断点进度与订阅列表")
    ap.add_argument("--selftest", action="store_true", help="内置样例解析自测（不联网/不入库）")
    ap.add_argument("--download-images", action="store_true",
                    help="额外把配图下载到本地(体积大;默认只在元数据存图片URL,下载留M5)")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(0 if selftest() else 1)
    if args.status:
        show_status()
        return

    feeds = _read_feed_urls(args.feeds)
    if not feeds:
        _log("没有可用 feed。请用 --feeds 指定，或在 data/wechat_feeds.txt 填入 RSS URL。")
        _log("（先按 docs/wewe_rss_setup.md 部署 WeWe RSS + 微信读书登录获取 RSS URL）")
        return
    run(feeds, limit=args.limit, dry_run=args.dry_run, save_images=args.download_images)


if __name__ == "__main__":
    main()
