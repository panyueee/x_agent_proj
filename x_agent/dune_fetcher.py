"""链上聪明钱/鲸鱼异动数据层：Dune Analytics 官方 Python SDK。

环境变量：
    DUNE_API_KEY   Dune Analytics API Key（https://dune.com/settings/api）

使用 get_latest_result() 读取缓存结果，不消耗 execution credit。
"""
from __future__ import annotations

import os
import datetime as dt
from typing import Optional, List

from .fetcher import Tweet

# Dune 社区现成查询 ID
QUERY_SMART_MONEY   = 2537251   # ETH 聪明钱钱包动向
QUERY_WHALE_TRANSFER = 1329533  # 大额 ETH/USDT 转账
QUERY_BTC_HOLDERS   = 3324963   # BTC 大户持仓变化


def _safe_float(val) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _fmt_addr(addr: str) -> str:
    """长地址缩写显示：0x1234...abcd。"""
    s = str(addr or "")
    return f"{s[:6]}...{s[-4:]}" if len(s) > 12 else s


class DuneFetcher:
    """Dune Analytics 数据客户端，返回链上异动 Tweet 列表。"""

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("DUNE_API_KEY 未配置")
        try:
            from dune_client.client import DuneClient
        except ImportError:
            raise ImportError("dune-client 未安装，请运行: pip install dune-client")
        self._client = DuneClient(api_key)

    def _latest(self, query_id: int):
        """拉取指定查询的最新缓存结果，失败返回空列表。"""
        try:
            result = self._client.get_latest_result(query_id)
            return result.result.rows if result and result.result else []
        except Exception as e:
            print(f"[dune] query {query_id} 获取失败: {e}")
            return []

    def fetch_smart_money(self, min_usd: float = 500_000) -> List[Tweet]:
        """拉取聪明钱钱包近期动向，返回 Tweet 列表。"""
        rows = self._latest(QUERY_SMART_MONEY)
        tweets = []
        now = dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        for i, row in enumerate(rows):
            wallet  = _fmt_addr(row.get("wallet") or row.get("address") or "")
            action  = str(row.get("action") or row.get("type") or "操作")
            token   = str(row.get("token") or row.get("symbol") or "")
            usd_val = _safe_float(row.get("usd_value") or row.get("amount_usd") or 0)
            if usd_val < min_usd:
                continue
            text = f"🧠 聪明钱异动：{wallet} {action} {token}（${usd_val:,.0f}）"
            tweets.append(Tweet(
                id=f"dune_sm_{QUERY_SMART_MONEY}_{i}",
                author="dune_analytics",
                author_id="dune",
                text=text,
                created_at=str(row.get("block_time") or row.get("time") or now),
                url=f"https://dune.com/queries/{QUERY_SMART_MONEY}",
                metrics={"usd_value": int(usd_val), "token_amount": 0},
                source_label="onchain",
                group_tag="onchain",
            ))
        print(f"[dune] 聪明钱异动: {len(tweets)} 条（过滤门槛 ${min_usd:,.0f}）")
        return tweets

    def fetch_whale_alerts(self, min_usd: float = 1_000_000) -> List[Tweet]:
        """拉取大额转账（默认 >$100万），返回 Tweet 列表。"""
        rows = self._latest(QUERY_WHALE_TRANSFER)
        tweets = []
        now = dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        for i, row in enumerate(rows):
            from_addr = _fmt_addr(row.get("from") or row.get("from_address") or "")
            to_addr   = _fmt_addr(row.get("to") or row.get("to_address") or "")
            token     = str(row.get("token_symbol") or row.get("symbol") or "ETH")
            amount    = _safe_float(row.get("token_amount") or row.get("amount") or 0)
            usd_val   = _safe_float(row.get("usd_value") or row.get("amount_usd") or 0)
            if usd_val < min_usd:
                continue
            text = (f"🐋 大额转账：{amount:,.0f} {token}"
                    f"（${usd_val/1e6:.1f}M）{from_addr} → {to_addr}")
            tweets.append(Tweet(
                id=f"dune_wh_{QUERY_WHALE_TRANSFER}_{i}",
                author="dune_analytics",
                author_id="dune",
                text=text,
                created_at=str(row.get("block_time") or row.get("time") or now),
                url=f"https://dune.com/queries/{QUERY_WHALE_TRANSFER}",
                metrics={"usd_value": int(usd_val), "token_amount": int(amount)},
                source_label="onchain",
                group_tag="onchain",
            ))
        print(f"[dune] 鲸鱼转账: {len(tweets)} 条（过滤门槛 ${min_usd/1e6:.0f}M）")
        return tweets

    def fetch_btc_holders(self) -> List[Tweet]:
        """拉取 BTC 大户持仓变化，返回 Tweet 列表。"""
        rows = self._latest(QUERY_BTC_HOLDERS)
        tweets = []
        now = dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        for i, row in enumerate(rows[:10]):   # 只取前 10 条
            cohort  = str(row.get("cohort") or row.get("holder_type") or "大户")
            balance = _safe_float(row.get("balance") or row.get("btc_balance") or 0)
            change  = _safe_float(row.get("change") or row.get("balance_change") or 0)
            if abs(change) < 10:   # 变化不足 10 BTC 忽略
                continue
            sign = "+" if change >= 0 else ""
            text = f"₿ BTC持仓变化（{cohort}）：{sign}{change:,.0f} BTC（持仓 {balance:,.0f} BTC）"
            tweets.append(Tweet(
                id=f"dune_btc_{QUERY_BTC_HOLDERS}_{i}",
                author="dune_analytics",
                author_id="dune",
                text=text,
                created_at=now,
                url=f"https://dune.com/queries/{QUERY_BTC_HOLDERS}",
                metrics={"usd_value": 0, "token_amount": int(balance)},
                source_label="onchain",
                group_tag="onchain",
            ))
        print(f"[dune] BTC大户持仓: {len(tweets)} 条")
        return tweets


def build_dune_client(cfg: dict) -> Optional[DuneFetcher]:
    """从配置或环境变量构建 DuneFetcher，未配置时返回 None。"""
    dune_cfg = cfg.get("dune", {})
    if not dune_cfg.get("enabled"):
        return None
    api_key = os.environ.get("DUNE_API_KEY", "")
    if not api_key:
        print("[dune] 未配置 DUNE_API_KEY，跳过链上数据")
        return None
    try:
        return DuneFetcher(api_key)
    except Exception as e:
        print(f"[dune] 初始化失败: {e}")
        return None
