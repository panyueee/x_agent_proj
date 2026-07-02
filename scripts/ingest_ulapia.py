#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
乌拉邦（ulapia.com）研报抓取骨架：按分析师拉历史研报清单，（注册后）下载 PDF 入库 RAG。

背景与边界（详见 docs/survey_ulapia.md，2026-07 无账号实测）：
  - 目标作者页（已消歧确认）：
      张瑜(华创宏观)   /authors/zhangyu-2   标称514篇, 40页, 收录 2018-10 ~ 2021-01
      李迅雷(中泰)     /authors/lixunlei    134篇, 4页,  收录至 2023-08
      注意 /authors/zhangyu 是中金"张宇"（同音撞 slug），不是目标人物！
  - 无账号可得：作者页翻页拿全清单（标题/日期/页数/URL/券商/作者），纯服务端 HTML。
  - 全文/PDF 完全锁登录：GET /reports/get_attachment/{slug} 游客返回
      {"err_no":-1,"msg":"该研报仅限注册用户访问..."}；登录后返回 dl.ulapia.com
      签名直链，约 3 分钟过期，须拿到即下。
  - 反爬：登录墙 + 限时直链 + localStorage uuid 设备指纹 + JS 内置爬虫 UA 黑名单。
    务必用浏览器 UA、克制限速（本脚本默认 2s；下载阶段建议 ≥10s + 每日限量）。

用法：
  .venv/bin/python scripts/ingest_ulapia.py --list --authors zhangyu-2,lixunlei
  .venv/bin/python scripts/ingest_ulapia.py --resolve 张瑜      # 姓名反查作者slug
  .venv/bin/python scripts/ingest_ulapia.py --status
  .venv/bin/python scripts/ingest_ulapia.py --ingest ...        # TODO 注册后补 cookie

环境：请用 .venv/bin/python 运行（勿用坏掉的 anaconda3.8）。
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASE = "https://www.ulapia.com"
DATA_DIR = ROOT / "data"
LISTING_FILE = DATA_DIR / "ulapia_listing.json"      # 清单落盘：{author_slug: [record,...]}
DONE_FILE = DATA_DIR / "ulapia_done.json"            # 断点续传：已入库 source_id（下载阶段用）
COOKIE_FILE = DATA_DIR / "ulapia_cookies.json"       # TODO 注册后：浏览器导出的 cookie，勿入 git

# 目标作者（slug -> 备注），--authors 可覆盖
DEFAULT_AUTHORS = {
    "zhangyu-2": "华创证券·张瑜（宏观首席；勿用 zhangyu，那是中金张宇）",
    "lixunlei": "中泰证券·李迅雷",
}

HEADERS = {
    # show.js 有爬虫 UA 黑名单，务必用正常浏览器 UA
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept-Language": "zh-CN,zh;q=0.9",
}
SLEEP = 2.0          # 列表阶段礼貌限速（秒）；下载阶段应更慢（≥10s）
TIMEOUT = 30


# ---------------------------------------------------------------------------
# 会话 / 落盘
# ---------------------------------------------------------------------------
def make_session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    s.headers.update(HEADERS)
    return s


def load_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_json(path: Path, obj: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------------------
# 作者页清单抓取（无账号即可）
# ---------------------------------------------------------------------------
def parse_report_items(html: str) -> list:
    """解析列表页（作者页/券商页/搜索页同构）里的研报条目。"""
    soup = BeautifulSoup(html, "lxml")
    items = []
    for h5 in soup.select("h5 a.stretched-link"):
        url = h5.get("href") or ""
        if "/reports/" not in url:
            continue
        # 条目卡片：h5 往上两层是内容列 div，含 券商/页数/作者/日期
        card = h5.find_parent("div", class_="position-static") or h5.parent
        text = card.get_text(" ", strip=True) if card else ""
        broker_el = card.find("strong") if card else None
        m_date = re.search(r"发布日期[:：]\s*(\d{4}-\d{2}-\d{2})", text)
        m_pages = re.search(r"页数[:：]?\s*(\d+)\s*页", text)
        m_author = re.search(r"作者[:：]?\s*([^\s]{1,20}?)\s*发布日期", text)
        items.append({
            "title": h5.get_text(strip=True),
            "url": url,
            "slug": url.rstrip("/").rsplit("/", 1)[-1],
            "category": url.split("/reports/")[-1].split("/")[0],
            "broker": broker_el.get_text(strip=True) if broker_el else None,
            "author": m_author.group(1) if m_author else None,
            "pages": int(m_pages.group(1)) if m_pages else None,
            "date": m_date.group(1) if m_date else None,
        })
    return items


def has_next_page(html: str) -> bool:
    """Laravel 分页：有 rel="next" 才有下一页。"""
    return 'rel="next"' in html


def iter_author_reports(s: requests.Session, author_slug: str, max_pages: int = 0):
    """作者页逐页翻到底，yield 每条研报记录。max_pages>0 时只翻前 N 页（调试）。"""
    page = 1
    while True:
        url = f"{BASE}/authors/{author_slug}"
        r = s.get(url, params={"page": page}, timeout=TIMEOUT)
        r.raise_for_status()
        items = parse_report_items(r.text)
        if page == 1:
            # 页面标题形如 "华创证券_张瑜_所有已发布研究分析报告_第1页_乌拉邦研报"，用于人肉确认没抓错人
            m = re.search(r"<title>(.*?)</title>", r.text)
            total = re.search(r"已收录(\d+)篇研报", r.text)
            print(f"  [{author_slug}] {m.group(1) if m else '?'}"
                  f"（标称 {total.group(1) if total else '?'} 篇）")
        if not items:
            break
        for it in items:
            yield it
        if not has_next_page(r.text) or (max_pages and page >= max_pages):
            break
        page += 1
        time.sleep(SLEEP)


def cmd_list(s: requests.Session, authors: list, max_pages: int) -> None:
    """拉清单并落盘 data/ulapia_listing.json（可重复跑，整体覆盖对应作者的清单）。"""
    listing = load_json(LISTING_FILE)
    for slug in authors:
        print(f"\n=== 拉取作者清单：{slug}（{DEFAULT_AUTHORS.get(slug, '自定义')}）===")
        recs = list(iter_author_reports(s, slug, max_pages=max_pages))
        dates = sorted(x["date"] for x in recs if x["date"])
        print(f"  抓到 {len(recs)} 条"
              + (f"，区间 {dates[0]} ~ {dates[-1]}" if dates else ""))
        listing[slug] = recs
        save_json(LISTING_FILE, listing)
        time.sleep(SLEEP)
    print(f"\n清单已写入 {LISTING_FILE}")


# ---------------------------------------------------------------------------
# 姓名 → 作者 slug 反查（作者索引不支持搜索，走 搜索→详情页→作者链接）
# ---------------------------------------------------------------------------
def cmd_resolve(s: requests.Session, name: str, probe: int = 5) -> None:
    """站内搜索姓名，抽前几条命中研报的详情页，汇总其中的作者链接（title 带券商可消歧）。"""
    r = s.get(f"{BASE}/reports/search", params={"query": name}, timeout=TIMEOUT)
    r.raise_for_status()
    items = parse_report_items(r.text)
    if not items:
        print(f"搜索 “{name}” 无结果")
        return
    print(f"搜索 “{name}” 命中示例 {len(items)} 条，探测前 {probe} 篇详情页的作者链接：")
    found = {}
    for it in items[:probe]:
        time.sleep(SLEEP)
        try:
            rr = s.get(it["url"], timeout=TIMEOUT)
            rr.raise_for_status()
        except Exception as e:
            print(f"  [warn] 详情页失败 {it['slug'][:30]}: {e}")
            continue
        # 形如 <a href="/authors/zhangyu-2" title="华创证券张瑜">张瑜</a>
        for m in re.finditer(r'href="(/authors/[^"]+)"\s+title="([^"]+)"', rr.text):
            found.setdefault(m.group(1), set()).add(m.group(2))
    if not found:
        print("  未在详情页发现作者链接")
        return
    for path, titles in sorted(found.items()):
        print(f"  {BASE}{path}   <- {' / '.join(sorted(titles))}")


# ---------------------------------------------------------------------------
# 全文下载 / 入库 —— TODO：等用户本人注册账号后补 cookie 登录逻辑
# ---------------------------------------------------------------------------
def load_login_session() -> requests.Session:
    """
    TODO(注册后)：从 data/ulapia_cookies.json 加载浏览器导出的登录 cookie。
      1) 用户浏览器登录 ulapia.com 后，DevTools 导出 cookie（关键是 Laravel session 条目）
      2) 存成 {name: value} JSON 到 COOKIE_FILE（已 gitignore 的 data/ 下，勿入库）
      3) 本函数注入 session 后，先调 is_logged 自检
    """
    if not COOKIE_FILE.exists():
        raise SystemExit(
            f"[TODO] 尚未配置登录 cookie：{COOKIE_FILE} 不存在。\n"
            "需要用户本人注册乌拉邦账号并从浏览器导出 cookie（见 docs/survey_ulapia.md 第六节）。")
    s = make_session()
    for k, v in load_json(COOKIE_FILE).items():
        s.cookies.set(k, v, domain="www.ulapia.com")
    return s


def check_logged_in(s: requests.Session, any_slug: str, uuid: str) -> bool:
    """
    TODO(注册后)：GET /user/is_logged?slug={slug}&uuid={uuid} 自检登录态。
      - uuid：随机 32 位字母数字串，生成一次后固定复用（模拟 localStorage 设备指纹）
      - 游客/失效返回 {"err_no":1}；登录有效返回 err_no==0
    """
    raise NotImplementedError("等注册后实现：is_logged 登录态自检")


def get_attachment_link(s: requests.Session, slug: str) -> str:
    """
    TODO(注册后)：GET /reports/get_attachment/{slug} 换 PDF 直链。
      - err_no==0：返回 json["link"]（dl.ulapia.com 签名 URL，约 3 分钟过期，拿到须立刻下载）
      - err_no==1：收费报告（og:document:cost 非“免费”），记 skip 不重试
      - err_no==-1：未登录/无权限
      - 请求头带 Referer=详情页 URL、X-Requested-With=XMLHttpRequest
    """
    raise NotImplementedError("等注册后实现：get_attachment 换限时直链")


def cmd_ingest(authors: list, limit: int) -> None:
    """
    TODO(注册后)：下载 + 入库主流程。骨架只留接口，不实际执行（rag.db 有其他 agent 在重入库）。

    规划（详见 docs/survey_ulapia.md 第六节）：
      1) listing = load_json(LISTING_FILE)，没有清单先跑 --list
      2) s = load_login_session()；check_logged_in() 自检
      3) 逐篇（新到旧）：done 里有 source_id 就跳过；
         link = get_attachment_link(s, slug)  → 立刻下载到临时文件，校验 %PDF- 魔数
      4) from x_agent.rag import ingest_pdf   # 复用现成入库，勿自写解析
         ingest_pdf(tmp, title=f"{date} {title}", author=f"{broker}·{author}",
                    source_id=f"research:ulapia:{slug}", source_type="research",
                    use_ocr=False, skip_vectors=True)
      5) 每篇成功即写 DONE_FILE 断点续传；
         限速：篇间隔 ≥10s + 每日限量（小站有风控，注册用户免费额度未知，先小批量实测）
    """
    raise SystemExit(
        "[TODO] 下载入库尚未实现：需要用户本人注册账号后补 cookie 登录逻辑。\n"
        "当前无账号只能拉清单（--list）。实现规划见本函数 docstring 与 docs/survey_ulapia.md。")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def cmd_status() -> None:
    listing = load_json(LISTING_FILE)
    done = load_json(DONE_FILE)
    print(f"清单文件 {LISTING_FILE}：{sum(len(v) for v in listing.values())} 条 / {len(listing)} 个作者")
    for slug, recs in listing.items():
        dates = sorted(x["date"] for x in recs if x.get("date"))
        rng = f"{dates[0]} ~ {dates[-1]}" if dates else "-"
        print(f"  {slug}: {len(recs)} 条（{rng}）")
    print(f"已入库 {len(done)} 篇（{DONE_FILE}）")


def main() -> None:
    ap = argparse.ArgumentParser(description="乌拉邦研报：按分析师拉清单/（注册后）下载入库 RAG")
    ap.add_argument("--list", action="store_true", help="拉作者研报清单写 data/ulapia_listing.json（无账号可用）")
    ap.add_argument("--resolve", metavar="姓名", help="按姓名反查作者 slug（搜索→详情页作者链接）")
    ap.add_argument("--ingest", action="store_true", help="下载 PDF 入库（TODO：需注册账号）")
    ap.add_argument("--authors", default=",".join(DEFAULT_AUTHORS),
                    help=f"逗号分隔作者 slug，默认 {','.join(DEFAULT_AUTHORS)}")
    ap.add_argument("--max-pages", type=int, default=0, help="每作者最多翻 N 页（调试用，0=翻到底）")
    ap.add_argument("--limit", type=int, default=0, help="ingest 每人最多 N 篇（0=不限）")
    ap.add_argument("--status", action="store_true", help="打印清单/入库统计后退出")
    args = ap.parse_args()

    if args.status:
        cmd_status()
        return

    authors = [x.strip() for x in args.authors.split(",") if x.strip()]

    if args.resolve:
        cmd_resolve(make_session(), args.resolve)
        return
    if args.list:
        cmd_list(make_session(), authors, args.max_pages)
        return
    if args.ingest:
        cmd_ingest(authors, args.limit)
        return
    print("请指定动作：--list / --resolve 姓名 / --ingest / --status（-h 看帮助）")


if __name__ == "__main__":
    main()
