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


def _run(args, retries=2):
    for attempt in range(retries + 1):
        r = subprocess.run(
            [PYTHON3, SCRAPER] + args,
            capture_output=True, text=True, timeout=60
        )
        if r.returncode == 0:
            return json.loads(r.stdout)
        if attempt < retries:
            time.sleep(3)
    raise RuntimeError(f"scraper 异常: {r.stderr}")


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
        # 1. 获取博客文章链接列表
        links = _run(["blog", user_id, str(max_results)])
        results = []
        for item in links:
            # 2. 逐篇抓取详情，间隔 2 秒避免被封 IP
            time.sleep(2)
            try:
                art = _run(["article", item["url"]])
            except Exception as e:
                print(f"[tgb] 抓取失败 {item['url']}: {e}")
                continue
            if not art:
                continue
            tw = _to_tweet(art, user_id)
            results.append(tw)
        return results

    def stock_posts(self, stock_code, max_results, since):
        """抓个股讨论页帖子，转换为 Tweet 对象列表。"""
        # 1. 获取个股讨论帖链接列表
        links = _run(["stock", stock_code, str(max_results)])
        results = []
        for item in links:
            # 2. 逐篇抓取详情，间隔 2 秒避免被封 IP
            time.sleep(2)
            try:
                art = _run(["article", item["url"]])
            except Exception as e:
                print(f"[tgb] 个股帖抓取失败 {item['url']}: {e}")
                continue
            if not art:
                continue
            # 用 stock_code 作为 source_label，区分来源
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
