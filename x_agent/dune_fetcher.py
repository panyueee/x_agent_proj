"""Dune Analytics 链上数据抓取模块。

使用 dune-client 官方 Python SDK，通过 get_latest_result 读取缓存结果，
不消耗 execution credit。返回数据统一转为 Tweet dataclass 存入 SQLite。
"""
from __future__ import annotations

import os
import datetime as dt
from typing import List

from .fetcher import Tweet


# ── 已验证的社区公共查询 ID ──────────────────────────────────────
_QUERY_SMART_MONEY = 2537251   # ETH 聪明钱钱包动向
_QUERY_WHALE_TRANSFER = 1329533  # 大额 ETH/USDT 转账
_QUERY_BTC_HOLDERS = 3324963   # BTC 大户持仓变化


def _safe_float(val) -> float:
    """安全转换为浮点数，转换失败返回 0.0。"""
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _fmt_usd(val: float) -> str:
    """把美元数值格式化成可读字符串，如 32.5M、1.2B。"""
    if val >= 1_000_000_000:
        return f"${val / 1_000_000_000:.1f}B"
    if val >= 1_000_000:
        return f"${val / 1_000_000:.1f}M"
    if val >= 1_000:
        return f"${val / 1_000:.1f}K"
    return f"${val:.0f}"


def _fmt_token(val: float) -> str:
    """把代币数量格式化，加千位分隔符。"""
    if val >= 1_000_000:
        return f"{val / 1_000_000:.2f}M"
    if val >= 1_000:
        return f"{val / 1_000:.1f}K"
    return f"{val:.2f}"


class DuneFetcher:
    """Dune Analytics 链上数据抓取器。

    通过官方 dune-client SDK 拉取聪明钱动向和鲸鱼大额转账，
    返回统一的 Tweet 列表，可直接存入现有 tweets 表。
    """

    def __init__(self, api_key: str):
        """
        参数：
            api_key: Dune Analytics API Key（从 dune.com 账户页面获取）
        """
        try:
            from dune_client.client import DuneClient
        except ImportError:
            raise ImportError(
                "缺少 dune-client 依赖，请执行：pip install dune-client"
            )
        # DuneClient 也接受直接传入 api_key 参数
        self._client = DuneClient(api_key=api_key)

    # ── 内部辅助：执行查询并返回行列表 ──────────────────────────
    def _fetch_rows(self, query_id: int) -> List[dict]:
        """拉取指定查询的最新缓存结果，返回行列表（每行为字典）。

        使用 get_latest_result，不消耗 credit。
        若结果为空或查询缓存已过期，返回空列表。
        """
        try:
            result = self._client.get_latest_result(query_id)
            # dune-client 结果对象：result.result.rows 是 list[dict]
            rows = (result.result.rows if result and result.result else []) or []
            return rows
        except Exception as e:
            print(f"[dune] 查询 {query_id} 获取失败: {e}")
            return []

    @staticmethod
    def _make_tweet(query_id: int, idx: int, text: str, metrics: dict) -> Tweet:
        """把一行链上数据包装成 Tweet 对象。"""
        now = dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        return Tweet(
            id=f"dune_{query_id}_{idx}",
            author="dune_analytics",
            author_id="dune_analytics",
            text=text,
            created_at=now,
            url=f"https://dune.com/queries/{query_id}",
            metrics=metrics,
            source_label="onchain",
            group_tag="onchain",
        )

    # ── 聪明钱追踪 ──────────────────────────────────────────────
    def fetch_smart_money(self) -> List[Tweet]:
        """拉取 ETH 聪明钱钱包动向（query_id=2537251），返回 Tweet 列表。

        聪明钱查询通常包含字段：wallet / token / amount / usd_value / action 等，
        字段名依社区查询实际返回为准，做容错处理。
        """
        rows = self._fetch_rows(_QUERY_SMART_MONEY)
        tweets = []
        for idx, row in enumerate(rows):
            # 兼容不同字段命名风格
            wallet = (
                row.get("wallet") or row.get("address") or row.get("trader") or "unknown"
            )
            token = row.get("token") or row.get("symbol") or "ETH"
            action = row.get("action") or row.get("type") or row.get("side") or "move"
            amount = _safe_float(
                row.get("amount") or row.get("token_amount") or row.get("value") or 0
            )
            usd_value = _safe_float(
                row.get("usd_value") or row.get("usd_amount") or row.get("amount_usd") or 0
            )

            # 构建可读文本
            wallet_short = str(wallet)[:10] + "..." if len(str(wallet)) > 10 else str(wallet)
            text = (
                f"🧠 聪明钱异动：{action.upper()} {_fmt_token(amount)} {token} "
                f"({_fmt_usd(usd_value)}) — 地址 {wallet_short}"
            )
            metrics = {
                "usd_value": usd_value,
                "token_amount": amount,
                "token": token,
                "action": action,
            }
            tweets.append(self._make_tweet(_QUERY_SMART_MONEY, idx, text, metrics))

        print(f"[dune] 聪明钱异动 {len(tweets)} 条")
        return tweets

    # ── 鲸鱼大额转账 ─────────────────────────────────────────────
    def fetch_whale_alerts(self, min_usd: float = 1_000_000) -> List[Tweet]:
        """拉取百万美元以上大额 ETH/USDT 转账（query_id=1329533），返回 Tweet 列表。

        参数：
            min_usd: 最低美元金额门槛，低于此值的记录过滤掉，默认 100 万美元
        """
        rows = self._fetch_rows(_QUERY_WHALE_TRANSFER)
        tweets = []
        for idx, row in enumerate(rows):
            usd_value = _safe_float(
                row.get("usd_value") or row.get("amount_usd") or row.get("value_usd") or 0
            )
            # 过滤小额转账
            if usd_value < min_usd:
                continue

            token = row.get("token") or row.get("symbol") or "ETH"
            amount = _safe_float(
                row.get("amount") or row.get("token_amount") or row.get("value") or 0
            )
            from_addr = str(row.get("from") or row.get("from_address") or "unknown")
            to_addr = str(row.get("to") or row.get("to_address") or "unknown")
            from_short = from_addr[:10] + "..." if len(from_addr) > 10 else from_addr
            to_short = to_addr[:10] + "..." if len(to_addr) > 10 else to_addr

            text = (
                f"🐋 大额转账：{_fmt_token(amount)} {token} ({_fmt_usd(usd_value)}) "
                f"from {from_short} → {to_short}"
            )
            metrics = {
                "usd_value": usd_value,
                "token_amount": amount,
                "token": token,
                "from": from_addr,
                "to": to_addr,
            }
            tweets.append(self._make_tweet(_QUERY_WHALE_TRANSFER, idx, text, metrics))

        print(f"[dune] 鲸鱼大额转账（≥{_fmt_usd(min_usd)}）{len(tweets)} 条")
        return tweets

    # ── BTC 大户持仓变化 ──────────────────────────────────────────
    def fetch_btc_holders(self) -> List[Tweet]:
        """拉取 BTC 大户持仓变化（query_id=3324963），返回 Tweet 列表。"""
        rows = self._fetch_rows(_QUERY_BTC_HOLDERS)
        tweets = []
        for idx, row in enumerate(rows):
            holder = row.get("address") or row.get("wallet") or row.get("entity") or "unknown"
            btc_amount = _safe_float(
                row.get("btc_amount") or row.get("amount") or row.get("balance") or 0
            )
            usd_value = _safe_float(
                row.get("usd_value") or row.get("amount_usd") or row.get("value_usd") or 0
            )
            change = _safe_float(row.get("change") or row.get("delta") or 0)
            holder_short = str(holder)[:12] + "..." if len(str(holder)) > 12 else str(holder)

            change_str = (
                f"增持 +{_fmt_token(change)} BTC" if change > 0
                else f"减持 {_fmt_token(change)} BTC" if change < 0
                else "持仓不变"
            )
            text = (
                f"₿ BTC 大户：{holder_short} 持仓 {_fmt_token(btc_amount)} BTC "
                f"({_fmt_usd(usd_value)}) — {change_str}"
            )
            metrics = {
                "usd_value": usd_value,
                "token_amount": btc_amount,
                "token": "BTC",
                "change": change,
            }
            tweets.append(self._make_tweet(_QUERY_BTC_HOLDERS, idx, text, metrics))

        print(f"[dune] BTC 大户持仓变化 {len(tweets)} 条")
        return tweets
