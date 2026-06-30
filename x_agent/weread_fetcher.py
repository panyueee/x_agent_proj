"""
微信读书内容抓取器。

认证方式：Cookie 注入（手动登录一次，有效期约 30 天）。
将浏览器 Cookie 保存到 WEREAD_COOKIE 环境变量，或写入 config.yaml 的 weread.cookie。

API 端点均为 https://weread.qq.com/web/* 的非官方内部 API。
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Optional

import requests

_BASE = "https://weread.qq.com"
_API  = f"{_BASE}/web"

# 章节正文清洗用正则，预编译避免逐章重复编译（整本书可达数百章）
_TAG_RE       = re.compile(r"<[^>]+>")
_BLANKLINE_RE = re.compile(r"\n{3,}")

# 默认 User-Agent（模拟 Chrome）
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class WeReadClient:
    """
    微信读书 API 客户端。

    使用方式：
        client = WeReadClient(cookie="...")
        shelf = client.get_shelf()
        chapters = client.get_chapters(book_id)
        content = client.get_chapter_content(book_id, chapter_uid)
    """

    def __init__(self, cookie: str = "", cfg: Optional[dict] = None):
        raw = cookie or (cfg or {}).get("weread", {}).get("cookie", "")
        raw = raw or os.getenv("WEREAD_COOKIE", "")
        if not raw:
            raise ValueError(
                "未找到微信读书 Cookie。\n"
                "请在浏览器登录 weread.qq.com，复制全部 Cookie 后设置环境变量：\n"
                "  export WEREAD_COOKIE='wr_skey=...;wr_localkey=...;...'"
            )
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": _UA,
            "Referer": f"{_BASE}/",
            "Cookie": raw,
        })

    def _get(self, path: str, params: Optional[dict] = None, retries: int = 2) -> dict:
        url = f"{_API}{path}"
        for attempt in range(retries + 1):
            try:
                r = self._session.get(url, params=params, timeout=15)
                r.raise_for_status()
                data = r.json()
                if isinstance(data, dict) and data.get("errcode") not in (None, 0):
                    raise ValueError(f"API 错误: {data.get('errmsg', data)}")
                return data
            except Exception as e:
                if attempt < retries:
                    time.sleep(1)
                else:
                    raise RuntimeError(f"GET {path} 失败: {e}") from e
        return {}

    # ── 书架 ────────────────────────────────────────────────────────────────

    def get_shelf(self) -> list[dict]:
        """返回书架上所有书目信息。"""
        data = self._get("/shelf/sync", {"synckey": 0, "teenmode": 0, "count": 200})
        books = data.get("books", [])
        result = []
        for b in books:
            bi = b.get("bookInfo", b)
            result.append({
                "book_id":  bi.get("bookId", ""),
                "title":    bi.get("title", ""),
                "author":   bi.get("author", ""),
                "cover":    bi.get("cover", ""),
                "category": bi.get("categoryEnglishName", ""),
                "word_count": bi.get("wordCount", 0),
            })
        return result

    # ── 目录 ────────────────────────────────────────────────────────────────

    def get_chapters(self, book_id: str) -> list[dict]:
        """返回书籍目录（章节列表）。"""
        data = self._get("/book/chapterInfos", {
            "bookIds": f"[{json.dumps(book_id)}]",
            "synckeys": "[0]",
        })
        info_list = data.get("data", [])
        chapters = []
        if info_list:
            for ch in info_list[0].get("updated", []):
                chapters.append({
                    "chapter_uid": ch.get("chapterUid"),
                    "title":       ch.get("title", ""),
                    "level":       ch.get("level", 1),
                })
        return chapters

    # ── 章节正文 ─────────────────────────────────────────────────────────────

    def get_chapter_content(self, book_id: str, chapter_uid: int) -> str:
        """
        抓取单章正文，返回纯文本。
        微信读书渲染后的内容在 /book/read 接口以 JSON 形式返回。
        """
        data = self._get("/book/read", {
            "bookId": book_id,
            "chapterUid": chapter_uid,
            "readingTimestamp": int(time.time()),
        })
        # 内容在 data.chapterContentHtml 或 data.chapterContentStr
        html = data.get("chapterContentHtml") or data.get("chapterContentStr", "")
        if not html:
            return ""
        # 简单剥离 HTML 标签：标签/连续空行用预编译正则，实体替换用 str.replace 更快
        text = _TAG_RE.sub("", html)
        text = (text.replace("&nbsp;", " ")
                    .replace("&lt;", "<")
                    .replace("&gt;", ">")
                    .replace("&amp;", "&"))
        text = _BLANKLINE_RE.sub("\n\n", text.strip())
        return text

    # ── 高层接口：完整书籍 ────────────────────────────────────────────────────

    def fetch_book(
        self,
        book_id: str,
        title: str = "",
        author: str = "",
        max_chapters: int = 999,
        sleep_sec: float = 0.5,
    ) -> dict:
        """
        抓取整本书，返回 {"book_id", "title", "author", "chapters": [{"title", "content"}]}.
        sleep_sec: 章节间休眠（避免被封）。
        """
        print(f"[weread] 抓取《{title or book_id}》目录...")
        toc = self.get_chapters(book_id)
        print(f"[weread] 共 {len(toc)} 章，最多抓 {max_chapters} 章")

        chapters = []
        for i, ch in enumerate(toc[:max_chapters]):
            uid = ch["chapter_uid"]
            try:
                content = self.get_chapter_content(book_id, uid)
                chapters.append({"title": ch["title"], "content": content})
                print(f"[weread]   {i+1}/{min(len(toc),max_chapters)} 《{ch['title']}》 {len(content)} 字")
            except Exception as e:
                print(f"[weread]   ⚠ 章节 {uid} 失败: {e}")
                chapters.append({"title": ch["title"], "content": ""})
            if sleep_sec > 0:
                time.sleep(sleep_sec)

        return {
            "book_id":  book_id,
            "title":    title,
            "author":   author,
            "chapters": chapters,
        }


# ── 便捷函数：入库到 RAG ──────────────────────────────────────────────────────

def ingest_book_to_rag(
    book_id: str,
    client: WeReadClient,
    title: str = "",
    author: str = "",
    max_chapters: int = 999,
) -> int:
    """
    抓取一本书并直接入库到 RAG 向量库。
    返回新增的文本块数。
    """
    from x_agent.rag import ingest_book

    book = client.fetch_book(book_id, title=title, author=author,
                             max_chapters=max_chapters)
    n = ingest_book(
        title=book["title"] or book_id,
        chapters=book["chapters"],
        author=book.get("author", ""),
    )
    print(f"[weread] 《{book['title']}》入库完成：{n} 块")
    return n


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    cookie = os.getenv("WEREAD_COOKIE", "")
    if not cookie:
        print("请先设置 WEREAD_COOKIE 环境变量")
        sys.exit(1)

    wc = WeReadClient(cookie=cookie)

    if len(sys.argv) < 2 or sys.argv[1] == "shelf":
        shelf = wc.get_shelf()
        print(f"书架共 {len(shelf)} 本书：")
        for b in shelf:
            print(f"  {b['book_id']}  《{b['title']}》  {b['author']}  {b['word_count']:,} 字")

    elif sys.argv[1] == "fetch" and len(sys.argv) >= 3:
        # python -m x_agent.weread_fetcher fetch <book_id> [title] [author]
        bid   = sys.argv[2]
        title = sys.argv[3] if len(sys.argv) > 3 else bid
        author = sys.argv[4] if len(sys.argv) > 4 else ""
        n = ingest_book_to_rag(bid, wc, title=title, author=author)
        print(f"入库 {n} 块")

    elif sys.argv[1] == "fetch-all":
        # 批量入库书架全部书籍
        shelf = wc.get_shelf()
        total = 0
        for b in shelf:
            try:
                n = ingest_book_to_rag(b["book_id"], wc, title=b["title"], author=b["author"])
                total += n
            except Exception as e:
                print(f"[weread] 《{b['title']}》失败: {e}")
        print(f"全部完成，共入库 {total} 块")
