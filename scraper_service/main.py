"""
淘股吧远程爬虫服务（VPS 常驻）

启动：
  uvicorn main:app --host 0.0.0.0 --port 8765 --workers 1

环境变量：
  SCRAPER_API_KEY   请求鉴权 key（必须设置，否则服务拒绝所有请求）

接口：
  GET  /health
  POST /blog_since      {user_id, since_date}        → [{url, title}, ...]
  POST /blog            {user_id, max_results}        → [{url, title}, ...]
  POST /articles_batch  {urls: [...]}                 → [{id,title,body,...}, ...]
  POST /article         {url}                         → {id,title,body,...}
  POST /stock           {stock_code, max_results}     → [{url, title}, ...]
  POST /user_replies    {user_id, since_date}         → [{id,article_url,comment_text,...}, ...]
"""
from __future__ import annotations

import os
import re
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BASE = "https://www.tgb.cn"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-default-apps",
]

# ── 浏览器单例（跨请求复用）────────────────────────────────────────────
_pw = None
_browser = None
_lock = asyncio.Lock()


async def _get_browser():
    """获取（或重启）浏览器实例。"""
    global _pw, _browser
    from playwright.async_api import async_playwright
    if _browser and _browser.is_connected():
        return _browser
    async with _lock:
        if _browser and _browser.is_connected():
            return _browser
        if _pw:
            try:
                await _pw.stop()
            except Exception:
                pass
        _pw = await async_playwright().start()
        _browser = await _pw.chromium.launch(headless=True, args=LAUNCH_ARGS)
        log.info("Browser (re)started")
        return _browser


async def _new_page():
    browser = await _get_browser()
    ctx = await browser.new_context(user_agent=UA, locale="zh-CN")
    page = await ctx.new_page()
    page.set_default_navigation_timeout(20_000)
    page.set_default_timeout(10_000)
    return page


async def _close_page(page):
    try:
        await page.context.close()
    except Exception:
        pass


# ── 文章解析 ────────────────────────────────────────────────────────────
async def _parse_article(page, url: str) -> dict:
    container = await page.query_selector(".article-content")
    if not container:
        return {}
    text = await container.inner_text()
    text = text.strip()
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    title = lines[0] if lines else ""
    meta = lines[1] if len(lines) > 1 else ""
    body = "\n".join(lines[2:])
    try:
        img_alts = await page.eval_on_selector_all(
            "img[alt]", "els => els.map(e => e.alt).filter(a => a.length > 2)"
        )
        if img_alts:
            body += " " + " ".join(img_alts[:5])
    except Exception:
        pass
    views    = re.search(r"浏览\s*([\d,]+)", meta)
    comments = re.search(r"评论\s*([\d,]+)", meta)
    date_m   = re.search(r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})", meta)
    author_m = re.match(r"^(\S+)\s+淘股吧", meta)
    aid      = re.search(r"/Article/(\d+)|/a/(\w+)", url)
    article_id = (aid.group(1) or aid.group(2)) if aid else url.split("/")[-1]
    return {
        "id":            article_id,
        "url":           url,
        "title":         title,
        "body":          body,
        "author":        author_m.group(1) if author_m else "",
        "created_at":    date_m.group(1) if date_m else "",
        "view_count":    int(views.group(1).replace(",", "")) if views else 0,
        "comment_count": int(comments.group(1).replace(",", "")) if comments else 0,
    }


# ── 生命周期 ────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    await _get_browser()   # 预热
    yield
    global _pw, _browser
    if _browser:
        try:
            await _browser.close()
        except Exception:
            pass
    if _pw:
        try:
            await _pw.stop()
        except Exception:
            pass


app = FastAPI(lifespan=lifespan)

# ── 鉴权 ────────────────────────────────────────────────────────────────
_API_KEY = os.environ.get("SCRAPER_API_KEY", "")


def _auth(x_api_key: str = Header(...)):
    if not _API_KEY:
        raise HTTPException(503, "SCRAPER_API_KEY not configured on server")
    if x_api_key != _API_KEY:
        raise HTTPException(401, "Invalid API key")


# ── 请求/响应模型 ────────────────────────────────────────────────────────
class BlogSinceReq(BaseModel):
    user_id: str
    since_date: str          # YYYY-MM-DD
    max_scroll: int = 10

class BlogReq(BaseModel):
    user_id: str
    max_results: int = 20

class ArticlesBatchReq(BaseModel):
    urls: list[str]

class ArticleReq(BaseModel):
    url: str

class StockReq(BaseModel):
    stock_code: str
    max_results: int = 10

class UserRepliesReq(BaseModel):
    user_id: str
    since_date: str       # YYYY-MM-DD
    max_scroll: int = 15


# ── 路由 ────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    browser = await _get_browser()
    return {"ok": True, "browser_connected": browser.is_connected()}


@app.post("/blog_since", dependencies=[Depends(_auth)])
async def api_blog_since(req: BlogSinceReq) -> list[dict]:
    url = f"{BASE}/blog/{req.user_id}"
    results = []
    seen: set[str] = set()
    page = await _new_page()
    try:
        await page.goto(url, wait_until="commit", timeout=20_000)
        await page.wait_for_timeout(800)
        for _ in range(req.max_scroll):
            await page.keyboard.press("End")
            await page.wait_for_timeout(800)
            try:
                body_text = await page.inner_text("body")
                dates = re.findall(r"\d{4}-\d{2}-\d{2}", body_text)
                if dates and min(dates) < req.since_date:
                    break
            except Exception:
                pass
            cur = len(await page.query_selector_all("a[href*='/a/'], a[href*='/Article/']"))
            if cur == len(seen) and cur > 0:
                break
        for a in await page.query_selector_all("a"):
            href = await a.get_attribute("href") or ""
            if re.match(r"^/a/\w+$", href) or re.match(r"^/Article/\d+", href):
                title = (await a.inner_text()).strip()
                full = BASE + href
                if full not in seen and title:
                    seen.add(full)
                    results.append({"url": full, "title": title})
    finally:
        await _close_page(page)
    return results


@app.post("/blog", dependencies=[Depends(_auth)])
async def api_blog(req: BlogReq) -> list[dict]:
    url = f"{BASE}/blog/{req.user_id}"
    results = []
    page = await _new_page()
    try:
        await page.goto(url, wait_until="commit", timeout=20_000)
        await page.wait_for_timeout(800)
        prev_count = 0
        for _ in range(6):
            await page.keyboard.press("End")
            await page.wait_for_timeout(800)
            cur = len(await page.query_selector_all("a[href*='/a/'], a[href*='/Article/']"))
            if cur == prev_count and cur > 0:
                break
            prev_count = cur
        for a in await page.query_selector_all("a"):
            href = await a.get_attribute("href") or ""
            if re.match(r"^/a/\w+$", href) or re.match(r"^/Article/\d+", href):
                title = (await a.inner_text()).strip()
                if title:
                    results.append({"url": BASE + href, "title": title})
                    if len(results) >= req.max_results:
                        break
    finally:
        await _close_page(page)
    return results


@app.post("/articles_batch", dependencies=[Depends(_auth)])
async def api_articles_batch(req: ArticlesBatchReq) -> list[dict]:
    MAX_BATCH = 20
    urls = req.urls[:MAX_BATCH]
    results = []
    page = await _new_page()
    try:
        # 预热
        await page.goto("about:blank", timeout=5_000)
        for url in urls:
            try:
                await page.goto(url, wait_until="commit", timeout=15_000)
                await page.wait_for_timeout(2000)
                art = await _parse_article(page, url)
            except Exception:
                art = {}
                try:
                    await page.goto("about:blank", timeout=3_000)
                except Exception:
                    pass
            results.append(art)
    finally:
        await _close_page(page)
    return results


@app.post("/article", dependencies=[Depends(_auth)])
async def api_article(req: ArticleReq) -> dict:
    page = await _new_page()
    try:
        await page.goto(req.url, wait_until="commit", timeout=20_000)
        await page.wait_for_timeout(2000)
        return await _parse_article(page, req.url)
    except Exception:
        return {}
    finally:
        await _close_page(page)


@app.post("/stock", dependencies=[Depends(_auth)])
async def api_stock(req: StockReq) -> list[dict]:
    url = f"{BASE}/stock/{req.stock_code}"
    results = []
    page = await _new_page()
    try:
        await page.goto(url, wait_until="commit", timeout=20_000)
        prev_count = 0
        for _ in range(6):
            await page.keyboard.press("End")
            await page.wait_for_timeout(800)
            cur = len(await page.query_selector_all(
                "a[href*='/post/'], a[href*='/t/'], a[href*='/a/'], a[href*='/Article/']"
            ))
            if cur == prev_count and cur > 0:
                break
            prev_count = cur
        for a in await page.query_selector_all("a"):
            href = await a.get_attribute("href") or ""
            if (re.search(r"/post/\d+", href) or re.search(r"/t/\d+", href)
                    or re.match(r"^/a/\w+$", href) or re.search(r"/Article/\d+", href)):
                title = (await a.inner_text()).strip()
                if title:
                    full = href if href.startswith("http") else BASE + href
                    results.append({"url": full, "title": title})
                    if len(results) >= req.max_results:
                        break
    finally:
        await _close_page(page)
    return results


@app.post("/user_replies", dependencies=[Depends(_auth)])
async def api_user_replies(req: UserRepliesReq) -> list[dict]:
    url = f"{BASE}/blog/{req.user_id}/comment"
    results = []
    seen: set[str] = set()
    page = await _new_page()
    try:
        await page.goto(url, wait_until="commit", timeout=20_000)
        await page.wait_for_timeout(1500)

        prev_h = 0
        for _ in range(req.max_scroll):
            await page.keyboard.press("End")
            await page.wait_for_timeout(800)
            try:
                body_text = await page.inner_text("body")
                dates = re.findall(r"\d{4}-\d{2}-\d{2}", body_text)
                if dates and min(dates) < req.since_date:
                    break
            except Exception:
                pass
            h = await page.evaluate("document.body.scrollHeight")
            if h == prev_h:
                break
            prev_h = h

        items = await page.evaluate("""
            () => {
                const out = [];
                const seen = new Set();
                const links = document.querySelectorAll(
                    'a[href*="/a/"], a[href*="/Article/"]'
                );
                for (const link of links) {
                    const href = link.href;
                    if (!href || seen.has(href)) continue;
                    seen.add(href);
                    let container = link.closest('li')
                        || link.closest('[class*="item"]')
                        || link.closest('[class*="reply"]')
                        || link.closest('[class*="comment"]')
                        || link.parentElement;
                    const fullText = container ? container.innerText.trim() : '';
                    const dateMatch = fullText.match(
                        /\\d{4}-\\d{2}-\\d{2}\\s+\\d{2}:\\d{2}/
                    );
                    out.push({
                        article_url:   href,
                        article_title: link.innerText.trim(),
                        full_text:     fullText,
                        created_at:    dateMatch ? dateMatch[0] : '',
                    });
                }
                return out;
            }
        """)

        for item in items:
            art_url = item.get("article_url", "")
            if not art_url or art_url in seen:
                continue
            created_at = item.get("created_at", "")
            if created_at and created_at[:10] < req.since_date:
                continue
            full_text     = item.get("full_text", "")
            article_title = item.get("article_title", "")
            comment_text  = full_text.replace(article_title, "").replace(created_at, "").strip()

            aid = re.search(r"/Article/(\d+)|/a/(\w+)", art_url)
            art_id = (aid.group(1) or aid.group(2)) if aid else art_url.split("/")[-1]
            reply_id = f"tgbreply_{req.user_id}_{art_id}"

            seen.add(art_url)
            results.append({
                "id":            reply_id,
                "article_url":   art_url,
                "article_title": article_title,
                "comment_text":  comment_text,
                "created_at":    created_at,
            })
    finally:
        await _close_page(page)
    return results
