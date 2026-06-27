"""研报跟进模块：抓取券商研报、分析师评级、目标价，跟踪供应商动态。

数据源：
  - 东方财富研报（reportapi.eastmoney.com）
  - 同花顺研报（reportdatas.10jqka.com.cn）
  - 雪球公司动态（xueqiu.com）
"""
from __future__ import annotations

import datetime as dt
import requests
from dataclasses import dataclass
from typing import List, Optional

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://data.eastmoney.com/report/",
}


@dataclass
class ResearchReport:
    """一条研报记录。"""
    report_id: str
    stock_code: str
    stock_name: str
    title: str
    org_name: str       # 券商名称
    analyst: str        # 分析师
    rating: str         # 评级：买入 / 增持 / 中性 / 减持 / 卖出
    target_price: Optional[float]
    published_at: str
    url: str
    summary: str = ""


@dataclass
class SupplierUpdate:
    """供应商动态：订单、产能、合作关系变化。"""
    supplier_code: str
    supplier_name: str
    customer_name: str  # 对应的核心公司
    event_type: str     # order / capacity / cooperation / risk
    title: str
    content: str
    source: str
    published_at: str
    url: str = ""


class ResearchClient:
    """研报与供应商跟踪客户端。"""

    def fetch_reports_eastmoney(self, stock_code: str, max_results: int = 20) -> List[ResearchReport]:
        """从东方财富拉取个股研报列表。"""
        url = "https://reportapi.eastmoney.com/report/list"
        params = {
            "cb": "datatable",
            "industryCode": "*",
            "pageSize": max_results,
            "industry": "*",
            "rating": "*",
            "ratingChange": "*",
            "beginTime": (dt.datetime.now() - dt.timedelta(days=90)).strftime("%Y-%m-%d"),
            "endTime": dt.datetime.now().strftime("%Y-%m-%d"),
            "pageNo": 1,
            "fields": "",
            "stockCode": stock_code,
            "code": f"SZ{stock_code}" if stock_code.startswith("0") or stock_code.startswith("3") else f"SH{stock_code}",
            "queryType": 1,
        }
        reports = []
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=10)
            text = r.text
            # 东方财富 JSONP 格式：datatable(...)
            if text.startswith("datatable("):
                text = text[10:-1]
            import json
            data = json.loads(text)
            items = data.get("data") or []
            for item in items:
                # 目标价
                tp = item.get("priceNow")
                try:
                    tp = float(tp) if tp else None
                except (TypeError, ValueError):
                    tp = None
                reports.append(ResearchReport(
                    report_id=str(item.get("id", "")),
                    stock_code=stock_code,
                    stock_name=item.get("stockName", ""),
                    title=item.get("title", ""),
                    org_name=item.get("orgSName", ""),
                    analyst=item.get("researcher", ""),
                    rating=item.get("rating", ""),
                    target_price=tp,
                    published_at=item.get("publishDate", ""),
                    url=item.get("infoCode", ""),
                ))
        except Exception as e:
            print(f"[research] 东方财富研报 {stock_code} 失败: {e}")
        return reports

    def fetch_reports_ths(self, stock_code: str, max_results: int = 20) -> List[ResearchReport]:
        """从同花顺拉取研报（备用数据源）。"""
        url = "https://reportdatas.10jqka.com.cn/reportCenter/index"
        params = {
            "stockcode": stock_code,
            "page": 1,
            "perpage": max_results,
            "type": 2,  # 个股研报
        }
        reports = []
        try:
            r = requests.get(url, params=params, headers={**HEADERS, "Referer": "https://www.10jqka.com.cn"}, timeout=10)
            items = (r.json().get("data") or {}).get("list") or []
            for item in items:
                reports.append(ResearchReport(
                    report_id=str(item.get("id", "")),
                    stock_code=stock_code,
                    stock_name=item.get("stockname", ""),
                    title=item.get("title", ""),
                    org_name=item.get("orgname", ""),
                    analyst=item.get("author", ""),
                    rating=item.get("invest", ""),
                    target_price=None,
                    published_at=item.get("time", ""),
                    url=item.get("pdfurl", ""),
                ))
        except Exception as e:
            print(f"[research] 同花顺研报 {stock_code} 失败: {e}")
        return reports

    def fetch_xueqiu_updates(self, symbol: str, max_results: int = 20) -> List[SupplierUpdate]:
        """从雪球抓取公司动态（需要 cookie，此处为骨架）。"""
        # 雪球需要 cookie 登录，骨架预留接口
        print(f"[research] 雪球动态 {symbol}：需配置 xueqiu_cookie")
        return []

    def fetch_supplier_news(self, supplier_name: str, customer_name: str,
                             max_results: int = 10) -> List[SupplierUpdate]:
        """用新浪财经关键词搜索供应商与客户的关联新闻。"""
        keyword = f"{supplier_name} {customer_name}"
        url = "https://feed.mix.sina.com.cn/api/roll/get"
        params = {
            "pageid": 153, "lid": 2516, "k": keyword,
            "num": max_results, "page": 1,
        }
        updates = []
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=10)
            items = r.json().get("result", {}).get("data") or []
            for item in items:
                updates.append(SupplierUpdate(
                    supplier_code="",
                    supplier_name=supplier_name,
                    customer_name=customer_name,
                    event_type="news",
                    title=item.get("title", ""),
                    content=item.get("intro", ""),
                    source="新浪财经",
                    published_at=item.get("ctime", ""),
                    url=item.get("url", ""),
                ))
        except Exception as e:
            print(f"[research] 供应商新闻 {keyword} 失败: {e}")
        return updates
