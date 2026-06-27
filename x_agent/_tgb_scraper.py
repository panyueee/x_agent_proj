"""淘股吧 Playwright 爬取脚本（由系统 python3 运行，输出 JSON）。

调用方式：
    python3 _tgb_scraper.py blog <user_id> <max_results>
    python3 _tgb_scraper.py article <url>
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
        for _ in range(5):
            page.keyboard.press("End")
            page.wait_for_timeout(800)
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


def scrape_article(url):
    """抓单篇文章，返回结构化数据。"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(user_agent=UA, locale="zh-CN").new_page()
        page.goto(url, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(1500)
        container = page.query_selector(".article-content")
        if not container:
            return {}
        text = container.inner_text().strip()
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        title  = lines[0] if lines else ""
        meta   = lines[1] if len(lines) > 1 else ""
        body   = "\n".join(lines[2:])[:600]
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


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "blog":
        user_id, max_r = sys.argv[2], int(sys.argv[3])
        print(json.dumps(scrape_blog(user_id, max_r), ensure_ascii=False))
    elif mode == "article":
        url = sys.argv[2]
        print(json.dumps(scrape_article(url), ensure_ascii=False))
