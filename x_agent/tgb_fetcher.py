"""淘股吧数据抓取层。

两种模式（通过环境变量 TGB_SCRAPER_URL 切换）：
  远程模式：TGB_SCRAPER_URL=http://<vps>:8765  → HTTP 调用 VPS 上的常驻爬虫服务
  本地模式：TGB_SCRAPER_URL 未设置            → subprocess 调用 _tgb_scraper.py
"""
from __future__ import annotations

import json
import subprocess
import datetime as dt
import os
import sys
import time

from .fetcher import Tweet

SCRAPER = os.path.join(os.path.dirname(__file__), "_tgb_scraper.py")
PYTHON3 = sys.executable
CHUNK   = 20   # articles_batch 每批上限

# ── 远程 HTTP 客户端 ──────────────────────────────────────────────────────
_SCRAPER_URL = os.environ.get("TGB_SCRAPER_URL", "").rstrip("/")
_API_KEY     = os.environ.get("TGB_API_KEY", "")


def _remote(path: str, payload: dict, timeout: int = 150) -> any:
    """向 VPS 爬虫服务发 POST 请求，返回解析后的 JSON。"""
    import urllib.request
    url  = f"{_SCRAPER_URL}{path}"
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "X-Api-Key": _API_KEY},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


# ── 本地 subprocess 客户端 ────────────────────────────────────────────────
def _run(args: list[str], retries: int = 1, timeout: int = 90) -> any:
    """运行本地 _tgb_scraper.py，超时后 killpg 整组。"""
    import signal
    for attempt in range(retries + 1):
        proc = subprocess.Popen(
            [PYTHON3, SCRAPER] + args,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
            preexec_fn=os.setsid,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                proc.kill()
            proc.communicate()
            if attempt < retries:
                time.sleep(2)
                continue
            raise RuntimeError(f"scraper 超时（{timeout}s）：{args}")
        if proc.returncode == 0:
            return json.loads(stdout)
        if attempt < retries:
            time.sleep(2)
    raise RuntimeError(f"scraper 异常: {stderr}")


# ── 统一调度：远程优先，本地兜底 ─────────────────────────────────────────
def _blog_since(user_id: str, since_date: str) -> list[dict]:
    if _SCRAPER_URL:
        return _remote("/blog_since", {"user_id": user_id, "since_date": since_date})
    return _run(["blog_since", user_id, since_date], retries=0, timeout=120)


def _blog(user_id: str, max_results: int) -> list[dict]:
    if _SCRAPER_URL:
        return _remote("/blog", {"user_id": user_id, "max_results": max_results})
    return _run(["blog", user_id, str(max_results)], retries=0, timeout=60)


def _articles_batch(urls: list[str]) -> list[dict]:
    """分批处理，每批 CHUNK 篇。"""
    arts = []
    for i in range(0, len(urls), CHUNK):
        chunk = urls[i:i + CHUNK]
        try:
            if _SCRAPER_URL:
                chunk_arts = _remote("/articles_batch", {"urls": chunk})
            else:
                chunk_arts = _run(["articles_batch"] + chunk, retries=0, timeout=150)
            arts.extend(chunk_arts)
        except Exception as e:
            print(f"[tgb] articles_batch 批次 {i//CHUNK+1} 失败: {e}")
    return arts


def _stock(stock_code: str, max_results: int) -> list[dict]:
    if _SCRAPER_URL:
        return _remote("/stock", {"stock_code": stock_code, "max_results": max_results})
    return _run(["stock", stock_code, str(max_results)], retries=0, timeout=60)


# ── 公共辅助 ─────────────────────────────────────────────────────────────
def _to_tweet(art: dict, user_id: str) -> Tweet:
    created_at = ""
    if art.get("created_at"):
        try:
            ts = dt.datetime.strptime(art["created_at"], "%Y-%m-%d %H:%M")
            created_at = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass
    text = art.get("title", "") + "\n" + art.get("body", "")
    return Tweet(
        id=f"tgb_{art['id']}",
        author=art.get("author") or user_id,
        author_id=user_id,
        text=text.strip(),
        created_at=created_at,
        url=art.get("url", ""),
        metrics={
            "view_count":    art.get("view_count", 0),
            "comment_count": art.get("comment_count", 0),
        },
        source_label=user_id,
        group_tag="taoguba",
    )


# ── 主客户端 ─────────────────────────────────────────────────────────────
class TgbClient:
    """淘股吧博客爬取客户端。"""

    def __init__(self):
        if _SCRAPER_URL:
            print(f"[tgb] 远程模式 → {_SCRAPER_URL}")
        else:
            print("[tgb] 本地模式（subprocess）")

    def user_posts(self, user_id: str, max_results: int, since: dt.datetime) -> list[Tweet]:
        since_str = since.strftime("%Y-%m-%d")
        if max_results <= 0:
            links = _blog_since(user_id, since_str)
        else:
            links = _blog(user_id, max_results)
        if not links:
            return []

        urls = [item["url"] for item in links if item.get("url")]
        arts = _articles_batch(urls)

        since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        results = []
        for art in arts:
            if not art:
                continue
            tw = _to_tweet(art, user_id)
            if tw.created_at and tw.created_at < since_iso:
                continue
            results.append(tw)
        return results

    def stock_posts(self, stock_code: str, max_results: int, since: dt.datetime) -> list[Tweet]:
        links = _stock(stock_code, max_results)
        if not links:
            return []

        urls = [item["url"] for item in links if item.get("url")]
        arts = _articles_batch(urls)

        results = []
        for art in arts:
            if not art:
                continue
            created_at = ""
            if art.get("created_at"):
                try:
                    ts = dt.datetime.strptime(art["created_at"], "%Y-%m-%d %H:%M")
                    created_at = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
                except ValueError:
                    pass
            text = art.get("title", "") + "\n" + art.get("body", "")
            tw = Tweet(
                id=f"tgb_stock_{stock_code}_{art['id']}",
                author=art.get("author") or stock_code,
                author_id=stock_code,
                text=text.strip(),
                created_at=created_at,
                url=art.get("url", ""),
                metrics={
                    "view_count":    art.get("view_count", 0),
                    "comment_count": art.get("comment_count", 0),
                },
                source_label=stock_code,
                group_tag="taoguba",
            )
            results.append(tw)
        return results
