"""确认博客列表加载方式 + 文章数据字段。"""
from playwright.sync_api import sync_playwright
import re

USER_ID  = "11056656"
BLOG_URL = f"https://www.tgb.cn/blog/{USER_ID}"
ART_URL  = "https://www.tgb.cn/Article/6575050/1"

api_calls = []
api_responses = {}

def on_request(req):
    if req.resource_type in ("xhr", "fetch") and "tgb.cn" in req.url:
        api_calls.append((req.method, req.url))

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        locale="zh-CN",
    )
    page = ctx.new_page()
    page.on("request", on_request)

    # ---- 博客列表：滚动触发懒加载 ----
    print(f"打开博客列表: {BLOG_URL}")
    page.goto(BLOG_URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2000)

    # 多次滚动触发懒加载
    for _ in range(5):
        page.keyboard.press("End")
        page.wait_for_timeout(1000)
    page.wait_for_timeout(2000)

    # 尝试各种文章链接选择器
    all_links = page.query_selector_all("a")
    article_hrefs = []
    for a in all_links:
        href = a.get_attribute("href") or ""
        if "/Article/" in href or re.search(r"/a/\w+", href):
            txt = a.inner_text().strip()
            article_hrefs.append((txt, href))

    print(f"找到文章链接: {len(article_hrefs)}")
    for txt, href in article_hrefs[:10]:
        print(f"  {txt[:60]} → {href}")

    # 打印页面部分 HTML 帮助调试
    content_area = page.query_selector(".blog-list, .article-list, #blogList, .list-wrap, main")
    if content_area:
        print(f"\n内容区域 HTML (前500字):\n{content_area.inner_html()[:500]}")
    else:
        print("\n未找到内容区域，打印页面文本(前500字):")
        print(page.inner_text("body")[:500])

    print("\n--- 拦截到的全部 API ---")
    for method, url in api_calls:
        print(f"  [{method}] {url}")

    # ---- 文章详情：确认字段 ----
    api_calls.clear()
    print(f"\n{'='*60}")
    print(f"文章详情: {ART_URL}")
    page.goto(ART_URL, wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(2000)

    body_text = page.inner_text(".article-content")
    lines = [l.strip() for l in body_text.split("\n") if l.strip()]
    # 提取热度数据
    meta = lines[1] if len(lines) > 1 else ""
    views    = re.search(r"浏览\s*([\d,]+)", meta)
    comments = re.search(r"评论\s*([\d,]+)", meta)
    print(f"标题: {lines[0]}")
    print(f"作者行: {meta}")
    print(f"浏览量: {views.group(1) if views else '未找到'}")
    print(f"评论数: {comments.group(1) if comments else '未找到'}")
    print(f"正文: {chr(10).join(lines[3:8])}")

    browser.close()
