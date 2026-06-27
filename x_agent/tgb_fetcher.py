"""淘股吧数据抓取层。

通过 subprocess 调用 _tgb_scraper.py（系统 python3 + playwright），
把结果映射成 Tweet 对象，统一进入分类/存储流程。
"""
from __future__ import annotations

import json
import subprocess
import datetime as dt
import os

from .fetcher import Tweet

SCRAPER = os.path.join(os.path.dirname(__file__), "_tgb_scraper.py")
PYTHON3 = "python3"   # 系统 python3，playwright 装在这里


def _run(args):
    r = subprocess.run(
        [PYTHON3, SCRAPER] + args,
        capture_output=True, text=True, timeout=60
    )
    if r.returncode != 0:
        raise RuntimeError(f"scraper 异常: {r.stderr[:200]}")
    return json.loads(r.stdout)


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
            # 2. 逐篇抓取详情
            try:
                art = _run(["article", item["url"]])
            except Exception as e:
                print(f"[tgb] 抓取失败 {item['url']}: {e}")
                continue
            if not art:
                continue
            tw = _to_tweet(art, user_id)
            # 3. 时间过滤
            if tw.created_at and tw.created_at < since.strftime("%Y-%m-%dT%H:%M:%SZ"):
                continue
            results.append(tw)
        return results
