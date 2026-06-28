"""淘股吧 Playwright 爬取脚本（由系统 python3 运行，输出 JSON）。

调用方式：
    python3 _tgb_scraper.py blog <user_id> <max_results>
    python3 _tgb_scraper.py blog_since <user_id> <since_date>   # since_date: YYYY-MM-DD
    python3 _tgb_scraper.py article <url>
    python3 _tgb_scraper.py stock <stock_code> <max_results>
"""
import sys, json, re
from playwright.sync_api import sync_playwright

BASE = "https://www.tgb.cn"
UA   = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def scrape_blog(user_id, max_results):
    """抓博客列表，返回文章 URL 列表。"""
    url = f"{BASE}/blog/{user_id}"
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(user_agent=UA, locale="zh-CN").new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        # 滚动加载：检测链接数量稳定后才结束，最多滚动 8 次
        prev_count = 0
        for _ in range(8):
            page.keyboard.press("End")
            page.wait_for_timeout(1000)
            cur = len(page.query_selector_all("a[href*='/a/']"))
            if cur == prev_count and cur > 0:
                break
            prev_count = cur
        page.wait_for_timeout(1500)
        for a in page.query_selector_all("a"):
            href = a.get_attribute("href") or ""
            if re.match(r"^/a/\w+$", href) or re.match(r"^/Article/\d+", href):
                title = a.inner_text().strip()
                if title:
                    full = BASE + href
                    results.append({"url": full, "title": title})
                    if len(results) >= max_results:
                        break
        browser.close()
    return results


def scrape_blog_since(user_id, since_date_str, max_scroll=15):
    """
    滚动博客列表直到内容稳定或超过 max_scroll 次，
    返回所有文章链接（时间过滤交给调用方）。
    since_date_str 格式：YYYY-MM-DD，用于检测到更早内容时提前退出。
    """
    url = f"{BASE}/blog/{user_id}"
    results = []
    seen = set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(user_agent=UA, locale="zh-CN").new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)

        hit_old = False
        prev_count = 0
        for _ in range(max_scroll):
            page.keyboard.press("End")
            page.wait_for_timeout(1200)
            dates = re.findall(r"\d{4}-\d{2}-\d{2}", page.inner_text("body"))
            if dates and min(dates) < since_date_str:
                hit_old = True
            cur = len(page.query_selector_all("a[href*='/a/'], a[href*='/Article/']"))
            if cur == prev_count and cur > 0:
                break
            prev_count = cur
            if hit_old:
                page.keyboard.press("End")
                page.wait_for_timeout(1200)
                break

        page.wait_for_timeout(1500)
        for a in page.query_selector_all("a"):
            href = a.get_attribute("href") or ""
            if re.match(r"^/a/\w+$", href) or re.match(r"^/Article/\d+", href):
                title = a.inner_text().strip()
                full = BASE + href
                if full not in seen and title:
                    seen.add(full)
                    results.append({"url": full, "title": title})
        browser.close()
    return results


def scrape_article(url):
    """抓单篇文章，返回结构化数据。"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(user_agent=UA, locale="zh-CN").new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(1000)
        container = page.query_selector(".article-content")
        if not container:
            return {}
        text = container.inner_text().strip()
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        title  = lines[0] if lines else ""
        meta   = lines[1] if len(lines) > 1 else ""
        body   = "\n".join(lines[2:])
        # 收集文章内图片的 alt 文字（可能含有用信息）
        img_alts = page.eval_on_selector_all("img[alt]", "els => els.map(e => e.alt).filter(a => a.length > 2)")
        if img_alts:
            body += " " + " ".join(img_alts[:5])
        views    = re.search(r"浏览\s*([\d,]+)", meta)
        comments = re.search(r"评论\s*([\d,]+)", meta)
        date_m   = re.search(r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})", meta)
        author_m = re.match(r"^(\S+)\s+淘股吧", meta)
        # 文章 ID 从 URL 提取
        aid = re.search(r"/Article/(\d+)|/a/(\w+)", url)
        article_id = (aid.group(1) or aid.group(2)) if aid else url.split("/")[-1]
        browser.close()
        return {
            "id":           article_id,
            "url":          url,
            "title":        title,
            "body":         body,
            "author":       author_m.group(1) if author_m else "",
            "created_at":   date_m.group(1) if date_m else "",
            "view_count":   int(views.group(1).replace(",", "")) if views else 0,
            "comment_count": int(comments.group(1).replace(",", "")) if comments else 0,
        }


def scrape_stock_posts(stock_code, max_results):
    """抓个股讨论帖列表，返回帖子 URL 与标题列表。"""
    url = f"{BASE}/stock/{stock_code}"
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(user_agent=UA, locale="zh-CN").new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        # 滚动加载，最多 8 次
        prev_count = 0
        for _ in range(8):
            page.keyboard.press("End")
            page.wait_for_timeout(1000)
            cur = len(page.query_selector_all("a[href*='/post/'], a[href*='/t/'], a[href*='/a/'], a[href*='/Article/']"))
            if cur == prev_count and cur > 0:
                break
            prev_count = cur
        page.wait_for_timeout(1500)
        for a in page.query_selector_all("a"):
            href = a.get_attribute("href") or ""
            if (re.search(r"/post/\d+", href)
                    or re.search(r"/t/\d+", href)
                    or re.match(r"^/a/\w+$", href)
                    or re.search(r"/Article/\d+", href)):
                title = a.inner_text().strip()
                if title:
                    full = href if href.startswith("http") else BASE + href
                    results.append({"url": full, "title": title})
                    if len(results) >= max_results:
                        break
        browser.close()
    return results


def scrape_articles_batch(urls):
    """单个浏览器实例依次抓多篇文章，比逐篇启动快 3-5x。"""
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=UA, locale="zh-CN")
        page = ctx.new_page()
        for url in urls:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(800)
                container = page.query_selector(".article-content")
                if not container:
                    results.append({})
                    continue
                text = container.inner_text().strip()
                lines = [l.strip() for l in text.split("\n") if l.strip()]
                title  = lines[0] if lines else ""
                meta   = lines[1] if len(lines) > 1 else ""
                body   = "\n".join(lines[2:])
                img_alts = page.eval_on_selector_all("img[alt]", "els => els.map(e => e.alt).filter(a => a.length > 2)")
                if img_alts:
                    body += " " + " ".join(img_alts[:5])
                views    = re.search(r"浏览\s*([\d,]+)", meta)
                comments = re.search(r"评论\s*([\d,]+)", meta)
                date_m   = re.search(r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})", meta)
                author_m = re.match(r"^(\S+)\s+淘股吧", meta)
                aid = re.search(r"/Article/(\d+)|/a/(\w+)", url)
                article_id = (aid.group(1) or aid.group(2)) if aid else url.split("/")[-1]
                results.append({
                    "id":            article_id,
                    "url":           url,
                    "title":         title,
                    "body":          body,
                    "author":        author_m.group(1) if author_m else "",
                    "created_at":    date_m.group(1) if date_m else "",
                    "view_count":    int(views.group(1).replace(",", "")) if views else 0,
                    "comment_count": int(comments.group(1).replace(",", "")) if comments else 0,
                })
            except Exception as e:
                results.append({})
        browser.close()
    return results


if __name__ == "__main__":
    mode = sys.argv[1]
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
