"""淘股吧数据抓取层。

通过 subprocess 调用 _tgb_scraper.py（系统 python3 + playwright），
把结果映射成 Tweet 对象，统一进入分类/存储流程。
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
PYTHON3 = sys.executable   # 与主进程同一 Python/venv，确保 playwright 可用


def _run(args, retries=1, timeout=90):
    """
    运行 _tgb_scraper.py 子进程，超时后杀掉整个进程组（含 Chromium/Node 子进程）。
    """
    import signal
    for attempt in range(retries + 1):
        proc = subprocess.Popen(
            [PYTHON3, SCRAPER] + args,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
            # 在新进程组运行，方便超时时 killpg 整组杀掉
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


def _to_tweet(art, user_id):
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


class TgbClient:
    """淘股吧博客爬取客户端。"""

    def user_posts(self, user_id, max_results, since):
        """
        max_results <= 0 时使用 blog_since 全量模式（按日期滚动到 since 为止）；
        max_results > 0 时使用固定条数模式。
        """
        since_str = since.strftime("%Y-%m-%d")
        if max_results <= 0:
            # 列表页最多给 120s（滚动次数在 scraper 里已缩减）
            links = _run(["blog_since", user_id, since_str], retries=0, timeout=120)
        else:
            links = _run(["blog", user_id, str(max_results)], retries=0, timeout=60)
        if not links:
            return []

        # 批量抓文章：一次调用拿全部详情，避免每篇单独启动浏览器
        urls = [item["url"] for item in links if item.get("url")]
        results = []
        try:
            arts = _run(["articles_batch"] + urls, retries=0, timeout=120)
        except Exception as e:
            print(f"[tgb] 批量抓取失败，改逐篇: {e}")
            arts = []
            for item in links:
                try:
                    art = _run(["article", item["url"]], retries=0, timeout=45)
                    if art:
                        arts.append(art)
                except Exception as e2:
                    print(f"[tgb] 抓取失败 {item['url']}: {e2}")

        since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        for art in arts:
            if not art:
                continue
            tw = _to_tweet(art, user_id)
            if tw.created_at and tw.created_at < since_iso:
                continue
            results.append(tw)
        return results

    def stock_posts(self, stock_code, max_results, since):
        """抓个股讨论页帖子，转换为 Tweet 对象列表。"""
        links = _run(["stock", stock_code, str(max_results)], retries=0, timeout=60)
        if not links:
            return []
        urls = [item["url"] for item in links if item.get("url")]
        try:
            arts = _run(["articles_batch"] + urls, retries=0, timeout=120)
        except Exception as e:
            print(f"[tgb] 个股批量抓取失败 {stock_code}: {e}")
            return []
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
