"""
classifier.py 关键词打分回归测试。

只覆盖「纯关键词打分」路径（_score / _score_zh / classify）；
LLM 抽取 extract_with_llm 需要 ANTHROPIC_API_KEY 与真实 API，本文件不触碰，
也不产生任何网络请求或写库。

classify 本身不调用 LLM（LLM 抽取在独立的 extract_with_llm 中），
因此无需关闭任何开关即可纯本地测试。

本文件既可用 pytest 运行：
    python -m pytest tests/test_classifier.py -v
也可直接当脚本运行（无 pytest 依赖）：
    python tests/test_classifier.py
"""
from __future__ import annotations

import os
import sys

# 让 `import x_agent.classifier` 在任意 cwd 下都能工作
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from x_agent.classifier import (
    ASHARE_RE,
    FINANCE_KEYWORDS,
    FINANCE_KEYWORDS_ZH,
    STOCK_KEYWORDS,
    STRATEGY_KEYWORDS,
    STRATEGY_KEYWORDS_ZH,
    WEB3_KEYWORDS,
    Signal,
    _score,
    _score_zh,
    classify,
)
from x_agent.fetcher import Tweet


# ── 辅助 ─────────────────────────────────────────────────────────────────────

def _tw(text: str, tweet_id: str = "t1") -> Tweet:
    """构造一条最小可用的 Tweet（classify 只读 .text 和 .id）。"""
    return Tweet(
        id=tweet_id,
        author="tester",
        author_id="uid",
        text=text,
        created_at="2026-01-01T00:00:00Z",
        url="https://x.com/tester/status/1",
    )


# ── 1. classify 分类正确性 ───────────────────────────────────────────────────

def test_classify_web3_tweet():
    """明显的 Web3/加密推文 → 落在 web3 类别。"""
    sig = classify(_tw("New airdrop on mainnet, claim your tokens via the bridge protocol"))
    assert isinstance(sig, Signal)
    assert sig.tweet_id == "t1"
    # airdrop(3)+mainnet(2)+token(1)+bridge(2)+protocol(1)=9 ≥ 阈值 3
    assert "web3" in sig.category, f"应识别为 web3，实际 category={sig.category!r}"
    assert sig.score >= 3


def test_classify_ashare_ticker_bonus():
    """A股 6 位代码（600519）应被提取进 tickers，并给 stock/finance 加分。"""
    sig = classify(_tw("600519 茅台 涨停"))
    assert "600519" in sig.tickers, f"未提取到 A股代码，tickers={sig.tickers!r}"
    # 涨停(3) 同时命中 STOCK_ZH 和 FINANCE_ZH，再叠加 ashare 命中 +1，必过阈值
    assert "stock" in sig.category, f"应包含 stock，实际 {sig.category!r}"
    assert "finance" in sig.category, f"应包含 finance，实际 {sig.category!r}"

    # 验证「ticker bonus」确实生效：去掉代码后 score 应更低
    sig_no_code = classify(_tw("茅台 涨停"))
    assert "600519" not in sig_no_code.tickers
    assert sig.score > sig_no_code.score, (
        f"带 A股代码 score={sig.score} 应高于不带代码 score={sig_no_code.score}"
    )


def test_classify_english_finance_tweet():
    """英文价值投资/财务推文 → 落在 finance / stock 桶。"""
    sig = classify(_tw(
        "Company reported strong earnings and revenue growth, raised full year guidance"
    ))
    assert sig.category != "none"
    assert ("finance" in sig.category) or ("stock" in sig.category), (
        f"应落在 finance/stock，实际 {sig.category!r}"
    )
    assert sig.score >= 3


def test_classify_spam_returns_none():
    """命中 spam 关键词 → category=none, score=0。"""
    sig = classify(_tw("Join me at Bybit and trade now! breakout setup take profit airdrop"))
    assert sig.category == "none", f"spam 应返回 none，实际 {sig.category!r}"
    assert sig.score == 0
    # 即便文中混入大量高分关键词，spam 仍应短路返回 none
    assert sig.tickers == []


# ── 2. 大小写不敏感（守护「预先 lower 一次」的优化） ──────────────────────────

def test_case_insensitive_same_score():
    """同一条英文推文，全大写 vs 全小写，score 必须完全一致。"""
    base = "Take Profit and Stop Loss on the breakout setup with a clean entry"
    up = classify(_tw(base.upper()))
    lo = classify(_tw(base.lower()))
    assert up.score == lo.score, (
        f"大小写导致 score 不一致：upper={up.score} lower={lo.score}"
    )
    assert up.category == lo.category


def test_case_insensitive_web3():
    up = classify(_tw("AIRDROP MAINNET TOKEN STAKING PROTOCOL"))
    lo = classify(_tw("airdrop mainnet token staking protocol"))
    assert up.score == lo.score
    assert up.category == lo.category


# ── 3. ASHARE_RE 行为 ─────────────────────────────────────────────────────────

def test_ashare_re_matches_valid_codes():
    assert ASHARE_RE.search("600519")   # 沪主板 6 开头
    assert ASHARE_RE.search("000001")   # 深主板 0 开头
    assert ASHARE_RE.search("300750")   # 创业板 3 开头


def test_ashare_re_rejects_invalid():
    assert not ASHARE_RE.search("12345"), "5 位数字不应匹配"
    assert not ASHARE_RE.search("1234567"), "7 位数字不应匹配"
    assert not ASHARE_RE.search("900001"), "9 开头不应匹配"
    assert not ASHARE_RE.search("500000"), "5 开头不应匹配"


def test_ashare_re_findall_extracts_all():
    codes = ASHARE_RE.findall("今日关注 600519 与 000001，还有 300750")
    assert codes == ["600519", "000001", "300750"], f"findall 结果异常: {codes!r}"


# ── 4. _score / _score_zh 加权可加性 ─────────────────────────────────────────

def test_score_additive_english():
    # _score 约定传入的文本已是小写
    text = "take profit and stop loss"
    assert _score(text, STRATEGY_KEYWORDS) == 6  # take profit(3)+stop loss(3)


def test_score_no_match_zero():
    assert _score("hello friendly world greeting", STRATEGY_KEYWORDS) == 0
    assert _score("hello friendly world greeting", WEB3_KEYWORDS) == 0
    assert _score("", STOCK_KEYWORDS) == 0


def test_score_zh_additive():
    assert _score_zh("止盈止损", STRATEGY_KEYWORDS_ZH) == 6  # 止盈(3)+止损(3)
    assert _score_zh("财报营收", FINANCE_KEYWORDS_ZH) == 6   # 财报(3)+营收(3)


def test_score_zh_no_match_zero():
    assert _score_zh("今天天气不错", STRATEGY_KEYWORDS_ZH) == 0
    assert _score_zh("", FINANCE_KEYWORDS_ZH) == 0


# ── 5. 边界用例 ───────────────────────────────────────────────────────────────

def test_empty_text():
    sig = classify(_tw("", tweet_id="empty"))
    assert sig.tweet_id == "empty"
    assert sig.category == "none"
    assert sig.score == 0
    assert sig.tickers == []


def test_text_with_no_keywords():
    sig = classify(_tw("hello world, just a friendly greeting here"))
    assert sig.category == "none", f"无关键词应为 none，实际 {sig.category!r}"
    assert sig.score == 0
    assert sig.tickers == []


# ── 独立运行入口（无 pytest 时） ──────────────────────────────────────────────

def _run_standalone() -> int:
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {fn.__name__}: {e}")
            failed += 1
        except Exception as e:  # noqa: BLE001
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed (total {passed + failed})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_standalone())
