"""产业链分析模块：抓取产业链上下游关系、行业数据，存入 SQLite 供分析。

数据源：
  - 东方财富行业板块数据（push2.eastmoney.com）
  - 新浪财经行业新闻（feed.mix.sina.com.cn）
  - 巨潮资讯公告（www.cninfo.com.cn）
"""
from __future__ import annotations

import datetime as dt
import json
import requests
from dataclasses import dataclass, field
from typing import List, Optional

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.eastmoney.com",
}


@dataclass
class IndustryNode:
    """产业链节点：一家公司或一个环节。"""
    code: str           # 股票代码或自定义 ID
    name: str           # 名称
    role: str           # 角色：upstream / core / downstream / competitor
    chain: str          # 所属产业链，如 "新能源汽车" / "AI算力"
    notes: str = ""     # 备注
    updated_at: str = ""


@dataclass
class ChainEvent:
    """产业链事件：公告、调研、产能变化等。"""
    chain: str
    title: str
    content: str
    source: str
    url: str
    published_at: str
    relevance_score: float = 0.0


class IndustryClient:
    """产业链数据抓取客户端。"""

    def fetch_sector_stocks(self, sector_code: str, max_results: int = 50) -> List[dict]:
        """
        拉取东方财富行业板块成分股。
        sector_code 示例：BK0471（新能源汽车）、BK0481（半导体）
        """
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": 1, "pz": max_results, "po": 1,
            "np": 1, "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": 2, "invt": 2,
            "fid": "f3",
            "fs": f"b:{sector_code}+f:!50",
            "fields": "f12,f14,f3,f4,f5,f6",  # 代码、名称、涨跌幅、净值、量、额
        }
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=10)
            data = r.json().get("data", {}) or {}
            diff = data.get("diff") or []
            return [
                {
                    "code": item.get("f12", ""),
                    "name": item.get("f14", ""),
                    "change_pct": (item.get("f3") or 0) / 100,
                }
                for item in diff
            ]
        except Exception as e:
            print(f"[industry] 拉取板块 {sector_code} 失败: {e}")
            return []

    def fetch_company_news(self, keyword: str, max_results: int = 20) -> List[ChainEvent]:
        """从新浪财经拉取关键词相关新闻。"""
        url = "https://feed.mix.sina.com.cn/api/roll/get"
        params = {
            "pageid": 153, "lid": 2516, "k": keyword,
            "num": max_results, "page": 1,
        }
        events = []
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=10)
            items = r.json().get("result", {}).get("data") or []
            for item in items:
                events.append(ChainEvent(
                    chain=keyword,
                    title=item.get("title", ""),
                    content=item.get("intro", ""),
                    source="新浪财经",
                    url=item.get("url", ""),
                    published_at=item.get("ctime", ""),
                ))
        except Exception as e:
            print(f"[industry] 新浪新闻 {keyword} 失败: {e}")
        return events

    def fetch_cninfo_announcements(self, stock_code: str, max_results: int = 10) -> List[ChainEvent]:
        """从巨潮资讯拉取上市公司公告。"""
        url = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
        payload = {
            "stock": stock_code,
            "tabName": "fulltext",
            "pageSize": max_results,
            "pageNum": 1,
            "column": "szse",
            "category": "",
            "plate": "",
            "seDate": "",
            "searchkey": "",
            "secid": "",
            "sortName": "",
            "sortType": "",
            "isHLtitle": True,
        }
        events = []
        try:
            r = requests.post(url, json=payload, headers=HEADERS, timeout=10)
            items = r.json().get("announcements") or []
            for item in items[:max_results]:
                events.append(ChainEvent(
                    chain=stock_code,
                    title=item.get("announcementTitle", ""),
                    content="",
                    source="巨潮资讯",
                    url=f"http://static.cninfo.com.cn/{item.get('adjunctUrl', '')}",
                    published_at=str(item.get("announcementTime", "")),
                ))
        except Exception as e:
            print(f"[industry] 巨潮公告 {stock_code} 失败: {e}")
        return events
