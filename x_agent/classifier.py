"""把抓到的推文分类、打分：交易策略信号 + Web3 资讯。

两层：
1. 关键词打分（免费、快，决定是否值得保留）。
2. 可选的 LLM 抽取（把策略推文解析成结构化字段：方向/入场/目标/止损）。
"""
from __future__ import annotations

import re
import json
from dataclasses import dataclass, field

from .fetcher import Tweet

# 交易策略相关词及权重（英文）
STRATEGY_KEYWORDS = {
    "take profit": 3, "stop loss": 3, "risk/reward": 2, "r:r": 2,
    "entry": 2, "exit": 2, "leverage": 2, "breakout": 2, "setup": 2,
    "scalp": 2, "rsi": 2, "macd": 2, "tp": 2, "sl": 2,
    "long": 1, "short": 1, "buy": 1, "sell": 1, "target": 1,
    "support": 1, "resistance": 1, "ema": 1, "fib": 1, "swing": 1,
    "liquidation": 1,
}

# 交易策略相关词及权重（中文）
STRATEGY_KEYWORDS_ZH = {
    "止盈": 3, "止损": 3, "风险回报": 2,
    "入场": 2, "入场点": 2, "出场": 2, "杠杆": 2, "突破": 2,
    "做多": 2, "做空": 2, "多单": 2, "空单": 2, "爆仓": 2,
    "超短": 2, "日内": 2, "均线": 1, "支撑": 1, "阻力": 1,
    "目标价": 1, "压力位": 1, "回调": 1, "趋势": 1, "仓位": 1,
    "买入": 1, "卖出": 1, "抄底": 1, "逃顶": 1,
}

# Web3 相关词及权重（英文）
WEB3_KEYWORDS = {
    "airdrop": 3, "restaking": 2, "mainnet": 2, "testnet": 2,
    "depin": 2, "defi": 2, "tge": 2, "rollup": 2,
    "token": 1, "staking": 1, "l2": 1, "onchain": 1,
    "wallet": 1, "protocol": 1, "governance": 1, "bridge": 1,
}

# Web3 相关词及权重（中文）
WEB3_KEYWORDS_ZH = {
    "空投": 3, "再质押": 2, "主网": 2, "测试网": 2,
    "去中心化": 2, "代币发行": 2, "二层": 2, "跨链": 2,
    "链上": 1, "质押": 1, "钱包": 1, "治理": 1,
    "协议": 1, "代币": 1, "公链": 1, "矿工": 1,
}

TICKER_RE = re.compile(r"\$[A-Za-z]{2,6}\b")   # $BTC $ETH $SOL ...

STRATEGY_THRESHOLD = 3
WEB3_THRESHOLD = 3

# 广告 / 垃圾账号黑名单关键词（全小写，命中任一即视为 spam，直接返回 none）
SPAM_KEYWORDS = [
    "join me at bybit",
    "claim a 20 usdt",
    "join our free signal",
    "free signals group",
    "click on the link below",
]


@dataclass
class Signal:
    tweet_id: str
    category: str            # strategy | web3 | both | none
    score: int
    tickers: list = field(default_factory=list)
    extracted: dict = field(default_factory=dict)   # LLM 抽取的结构化字段


def _score(text: str, table: dict) -> int:
    t = text.lower()
    return sum(weight for kw, weight in table.items() if kw in t)


def _score_zh(text: str, table: dict) -> int:
    # 中文不需要 lower，直接匹配
    return sum(weight for kw, weight in table.items() if kw in text)


def classify(tweet: Tweet) -> Signal:
    # 广告黑名单过滤：命中任意 spam 词则直接返回 none，不进行后续打分
    text_lower = tweet.text.lower()
    for spam_kw in SPAM_KEYWORDS:
        if spam_kw in text_lower:
            return Signal(tweet.id, "none", 0)

    s_score = _score(tweet.text, STRATEGY_KEYWORDS) + _score_zh(tweet.text, STRATEGY_KEYWORDS_ZH)
    w_score = _score(tweet.text, WEB3_KEYWORDS) + _score_zh(tweet.text, WEB3_KEYWORDS_ZH)
    tickers = sorted(set(TICKER_RE.findall(tweet.text)))
    if tickers:
        s_score += 1   # 带 $TICKER 更像是行情/策略

    is_strategy = s_score >= STRATEGY_THRESHOLD
    is_web3 = w_score >= WEB3_THRESHOLD
    if is_strategy and is_web3:
        cat = "both"
    elif is_strategy:
        cat = "strategy"
    elif is_web3:
        cat = "web3"
    else:
        cat = "none"

    return Signal(tweet.id, cat, max(s_score, w_score), tickers)


# ---------- 可选：用 Claude 抽取结构化策略 ----------
_EXTRACT_PROMPT = (
    "你是行情助手。从下面这条推文中抽取交易策略信息，"
    "只输出 JSON（不要 markdown、不要解释），字段如下，"
    "无法判断的填 null：\n"
    '{"asset": null, "direction": "long|short|none", "entry": null, '
    '"target": null, "stop": null, "timeframe": null, '
    '"thesis": "一句话逻辑", "confidence": 0.0}\n\n'
    "推文：\n{text}"
)


def extract_with_llm(tweet: Tweet, model: str, client) -> dict:
    """用 Anthropic API 把策略推文解析成结构化字段。失败则返回空 dict。"""
    try:
        msg = client.messages.create(
            model=model,
            max_tokens=400,
            messages=[{"role": "user", "content": _EXTRACT_PROMPT.format(text=tweet.text)}],
        )
        raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(raw)
    except Exception as e:   # 抽取失败不应中断主流程
        print(f"[llm] 抽取失败：{e}")
        return {}
