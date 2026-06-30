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

# A股 / 美股相关词及权重（英文）
STOCK_KEYWORDS = {
    "earnings": 3, "eps": 3, "guidance": 2, "revenue": 2,
    "ipo": 2, "buyback": 2, "dividend": 2, "pe ratio": 2,
    "bull run": 2, "bear market": 2, "rally": 1, "pullback": 1,
    "52 week high": 2, "breakout": 1, "sector rotation": 2,
    "fed": 1, "fomc": 2, "rate hike": 2, "rate cut": 2,
    "unemployment": 1, "gdp": 1, "inflation": 1, "cpi": 2,
}

# A股相关词及权重（中文）
STOCK_KEYWORDS_ZH = {
    "涨停": 3, "跌停": 3, "板块": 2, "龙头": 2, "打板": 2,
    "主力": 2, "游资": 2, "北向资金": 2, "融资融券": 2,
    "炒股": 1, "个股": 1, "大盘": 1, "指数": 1, "沪深": 1,
    "创业板": 1, "科创板": 2, "A股": 1, "港股": 1, "美股": 1,
    "财报": 2, "业绩": 2, "营收": 2, "净利润": 2, "分红": 2,
    "ETF": 2, "基金": 1, "可转债": 2, "配股": 2,
}

# 股票/财务相关词及权重（英文）—— finance 类别
FINANCE_KEYWORDS = {
    "earnings": 3, "revenue": 3, "guidance": 2, "pe ratio": 2,
    "eps": 2, "net income": 2, "gross margin": 2, "operating income": 2,
    "annual report": 2, "quarterly results": 2, "profit": 1, "loss": 1,
    "market cap": 1, "valuation": 1, "dividend": 1, "buyback": 1,
}

# 股票/财务相关词及权重（中文）—— finance 类别
FINANCE_KEYWORDS_ZH = {
    "市盈率": 2, "财报": 3, "营收": 3, "净利润": 2, "毛利率": 2,
    "大盘": 1, "龙头": 2, "涨停": 3, "跌停": 3,
    "主力": 2, "筹码": 2, "游资": 2, "北向资金": 2,
    "业绩": 2, "分红": 1, "配股": 2, "可转债": 2,
    "估值": 1, "市值": 1, "利润": 1, "亏损": 1,
}

TICKER_RE = re.compile(r"\$[A-Za-z]{2,6}\b")   # $BTC $ETH $SOL ...
# A股股票代码：以 0（深主板）、3（创业板/深交所）、6（沪主板）开头的6位数字
ASHARE_RE = re.compile(r'\b([036]\d{5})\b')

STRATEGY_THRESHOLD = 3
WEB3_THRESHOLD = 3
STOCK_THRESHOLD = 3
FINANCE_THRESHOLD = 3

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
    category: str            # strategy | web3 | finance | both | both+finance | none
    score: int
    tickers: list = field(default_factory=list)
    extracted: dict = field(default_factory=dict)   # LLM 抽取的结构化字段


def _score(text_lower: str, table: dict) -> int:
    # 传入的 text_lower 必须已是小写，避免在 classify 中重复 .lower()
    return sum(weight for kw, weight in table.items() if kw in text_lower)


def _score_zh(text: str, table: dict) -> int:
    # 中文不需要 lower，直接匹配
    return sum(weight for kw, weight in table.items() if kw in text)


def classify(tweet: Tweet) -> Signal:
    # 广告黑名单过滤：命中任意 spam 词则直接返回 none，不进行后续打分
    text_lower = tweet.text.lower()
    for spam_kw in SPAM_KEYWORDS:
        if spam_kw in text_lower:
            return Signal(tweet.id, "none", 0)

    # text_lower 复用上面的 spam 检查结果，避免重复 .lower()
    s_score = _score(text_lower, STRATEGY_KEYWORDS) + _score_zh(tweet.text, STRATEGY_KEYWORDS_ZH)
    w_score = _score(text_lower, WEB3_KEYWORDS) + _score_zh(tweet.text, WEB3_KEYWORDS_ZH)
    st_score = _score(text_lower, STOCK_KEYWORDS) + _score_zh(tweet.text, STOCK_KEYWORDS_ZH)
    f_score = _score(text_lower, FINANCE_KEYWORDS) + _score_zh(tweet.text, FINANCE_KEYWORDS_ZH)
    # A股股票代码（6位数字，0/3/6开头）命中也加分（复用已编译的 ASHARE_RE）
    if ASHARE_RE.search(tweet.text):
        st_score += 1
        f_score += 1

    tickers = sorted(set(TICKER_RE.findall(tweet.text)))
    # 提取 A股股票代码，一并并入 tickers 列表
    ashare_tickers = ASHARE_RE.findall(tweet.text)
    if ashare_tickers:
        tickers = sorted(set(tickers + ashare_tickers))
    if tickers:
        s_score += 1   # 带 $TICKER 更像是行情/策略

    is_strategy = s_score >= STRATEGY_THRESHOLD
    is_web3 = w_score >= WEB3_THRESHOLD
    is_stock = st_score >= STOCK_THRESHOLD
    is_finance = f_score >= FINANCE_THRESHOLD

    # 组合 category：支持 strategy+finance、both+finance 等用 + 拼接的组合
    cats = []
    if is_strategy:
        cats.append("strategy")
    if is_web3:
        cats.append("web3")
    if is_stock:
        cats.append("stock")
    if is_finance:
        cats.append("finance")

    if len(cats) == 0:
        cat = "none"
    elif "strategy" in cats and "web3" in cats and len(cats) > 2:
        # strategy + web3 + 其他（finance 或 stock）时用 both+ 前缀
        extras = [c for c in cats if c not in ("strategy", "web3")]
        cat = "both+" + "+".join(extras)
    elif "strategy" in cats and "web3" in cats:
        cat = "both"
    else:
        cat = "+".join(cats)

    return Signal(tweet.id, cat, max(s_score, w_score, st_score, f_score), tickers)


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
