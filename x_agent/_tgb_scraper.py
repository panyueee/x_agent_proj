"""淘股吧 Playwright 爬取脚本（由子进程运行，输出 JSON）。

调用方式：
    python3 _tgb_scraper.py blog <user_id> <max_results>
    python3 _tgb_scraper.py blog_since <user_id> <since_date>
    python3 _tgb_scraper.py article <url>
    python3 _tgb_scraper.py articles_batch <url1> [url2 ...]
    python3 _tgb_scraper.py stock <stock_code> <max_results>

超时保护：进程启动时设置 SIGALRM，到期后强制 os._exit(1)，
父进程的 killpg 兜底。
"""
import sys, json, re, os, signal, threading

BASE = "https://www.tgb.cn"
UA   = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# ── 全局硬超时（SIGALRM，macOS/Linux 专用）──────────────────────────
def _hard_timeout(signum, frame):
    os._exit(2)   # 强制退出，不触发 atexit / finally

def _set_alarm(seconds: int):
    try:
        signal.signal(signal.SIGALRM, _hard_timeout)
        signal.alarm(seconds)
    except (AttributeError, OSError):
        pass   # Windows 无 SIGALRM，忽略

# ── 关闭浏览器（超时后强制放弃）────────────────────────────────────
def _close_browser(browser, timeout=3):
    """在独立线程里关浏览器，超时后不等待直接返回。"""
    def _do():
        try:
            browser.close()
        except Exception:
            pass
    t = threading.Thread(target=_do, daemon=True)
    t.start()
    t.join(timeout=timeout)

# ── 统一 Playwright 启动参数 ────────────────────────────────────────
_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-default-apps",
]

def _new_browser(p):
    return p.chromium.launch(headless=True, args=_LAUNCH_ARGS)

def _new_page(browser):
    page = browser.new_context(user_agent=UA, locale="zh-CN").new_page()
    page.set_default_navigation_timeout(20_000)   # 单次导航上限 20s
    page.set_default_timeout(10_000)              # 其他操作上限 10s
    return page


# ── 文章解析（无网络，纯本地）───────────────────────────────────────
def _parse_article(page, url):
    container = page.query_selector(".article-content")
    if not container:
        return {}
    text  = container.inner_text().strip()
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    title = lines[0] if lines else ""
    meta  = lines[1] if len(lines) > 1 else ""
    body  = "\n".join(lines[2:])
    try:
        img_alts = page.eval_on_selector_all(
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


# ── 各模式实现 ───────────────────────────────────────────────────────

def scrape_blog(user_id, max_results):
    url = f"{BASE}/blog/{user_id}"
    results = []
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = _new_browser(p)
        try:
            page = _new_page(browser)
            page.goto(url, wait_until="commit", timeout=20_000)
            page.wait_for_timeout(800)
            prev_count = 0
            for _ in range(6):
                page.keyboard.press("End")
                page.wait_for_timeout(800)
                cur = len(page.query_selector_all("a[href*='/a/'], a[href*='/Article/']"))
                if cur == prev_count and cur > 0:
                    break
                prev_count = cur
            for a in page.query_selector_all("a"):
                href = a.get_attribute("href") or ""
                if re.match(r"^/a/\w+$", href) or re.match(r"^/Article/\d+", href):
                    title = a.inner_text().strip()
                    if title:
                        results.append({"url": BASE + href, "title": title})
                        if len(results) >= max_results:
                            break
        finally:
            _close_browser(browser)
    return results


def scrape_blog_since(user_id, since_date_str, max_scroll=10):
    url = f"{BASE}/blog/{user_id}"
    results = []
    seen = set()
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = _new_browser(p)
        try:
            page = _new_page(browser)
            page.goto(url, wait_until="commit", timeout=20_000)
            page.wait_for_timeout(800)
            prev_count = 0
            for _ in range(max_scroll):
                page.keyboard.press("End")
                page.wait_for_timeout(800)
                try:
                    body_text = page.inner_text("body")
                    dates = re.findall(r"\d{4}-\d{2}-\d{2}", body_text)
                    if dates and min(dates) < since_date_str:
                        break
                except Exception:
                    pass
                cur = len(page.query_selector_all("a[href*='/a/'], a[href*='/Article/']"))
                if cur == prev_count and cur > 0:
                    break
                prev_count = cur
            for a in page.query_selector_all("a"):
                href = a.get_attribute("href") or ""
                if re.match(r"^/a/\w+$", href) or re.match(r"^/Article/\d+", href):
                    title = a.inner_text().strip()
                    full  = BASE + href
                    if full not in seen and title:
                        seen.add(full)
                        results.append({"url": full, "title": title})
        finally:
            _close_browser(browser)
    return results


def scrape_article(url):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = _new_browser(p)
        try:
            page = _new_page(browser)
            page.goto(url, wait_until="commit", timeout=20_000)
            page.wait_for_timeout(2000)
            return _parse_article(page, url)
        except Exception:
            return {}
        finally:
            _close_browser(browser)


def scrape_articles_batch(urls):
    """单浏览器依次抓多篇文章，依赖页面级超时（set_default_navigation_timeout）保护。"""
    MAX_BATCH = 20
    urls = urls[:MAX_BATCH]
    results = []
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = _new_browser(p)
        try:
            page = _new_page(browser)
            # 预热：避免首篇冷启动失败
            try:
                page.goto("about:blank", timeout=5_000)
            except Exception:
                pass
            for url in urls:
                try:
                    page.goto(url, wait_until="commit", timeout=15_000)
                    page.wait_for_timeout(2000)
                    art = _parse_article(page, url)
                except Exception:
                    art = {}
                    try:
                        page.goto("about:blank", timeout=3_000)
                    except Exception:
                        pass
                results.append(art)
        finally:
            _close_browser(browser)
    return results


def scrape_stock_posts(stock_code, max_results):
    url = f"{BASE}/stock/{stock_code}"
    results = []
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = _new_browser(p)
        try:
            page = _new_page(browser)
            page.goto(url, wait_until="commit", timeout=20_000)
            prev_count = 0
            for _ in range(6):
                page.keyboard.press("End")
                page.wait_for_timeout(800)
                cur = len(page.query_selector_all(
                    "a[href*='/post/'], a[href*='/t/'], a[href*='/a/'], a[href*='/Article/']"
                ))
                if cur == prev_count and cur > 0:
                    break
                prev_count = cur
            for a in page.query_selector_all("a"):
                href = a.get_attribute("href") or ""
                if (re.search(r"/post/\d+", href) or re.search(r"/t/\d+", href)
                        or re.match(r"^/a/\w+$", href) or re.search(r"/Article/\d+", href)):
                    title = a.inner_text().strip()
                    if title:
                        full = href if href.startswith("http") else BASE + href
                        results.append({"url": full, "title": title})
                        if len(results) >= max_results:
                            break
        finally:
            _close_browser(browser)
    return results


# ── 入口 ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    mode = sys.argv[1]

    # 按模式设置不同的 SIGALRM 硬超时
    _alarms = {
        "blog":           50,
        "blog_since":    100,
        "article":        25,
        "articles_batch": 150,
        "stock":          50,
    }
    _set_alarm(_alarms.get(mode, 120))

    try:
        if mode == "blog":
            user_id, max_r = sys.argv[2], int(sys.argv[3])
            print(json.dumps(scrape_blog(user_id, max_r), ensure_ascii=False))
        elif mode == "blog_since":
            user_id, since_date = sys.argv[2], sys.argv[3]
            print(json.dumps(scrape_blog_since(user_id, since_date), ensure_ascii=False))
        elif mode == "article":
            url = sys.argv[2]
            print(json.dumps(scrape_article(url), ensure_ascii=False))
        elif mode == "articles_batch":
            urls = sys.argv[2:]
            print(json.dumps(scrape_articles_batch(urls), ensure_ascii=False))
        elif mode == "stock":
            stock_code, max_r = sys.argv[2], int(sys.argv[3])
            print(json.dumps(scrape_stock_posts(stock_code, max_r), ensure_ascii=False))
    except Exception as e:
        print(json.dumps([], ensure_ascii=False))
        sys.exit(1)
