"""文本打分 / 触发匹配热路径回归测试。

锁定一次性能优化：三个模块的关键词表现在只在模块加载时小写化一次
（pipeline 的 _TRIGGER_RULES_LC、psych_analyzer 的 _FEAR_KEYWORDS_LC /
_GREED_KEYWORDS_LC、industry_learner 的 _CHAIN_HINTS_LC）。
本套件验证打分 / 匹配行为未变、且大小写不敏感。

覆盖纯函数：
  - pipeline._match_triggers           触发词命中 → 产业链列表
  - psych_analyzer._score_text         恐慌 / 贪婪打分
  - psych_analyzer.PsychAnalyzer.compute_panic_index  聚合 Panic Index
  - industry_learner._prefilter        产业链预筛

不触网、不调 LLM、不依赖真实磁盘 DB（仅用内存 sqlite）。

本文件既可用 pytest 运行：
    python -m pytest tests/test_text_logic.py -v
也可直接当脚本运行（无 pytest 依赖）：
    python tests/test_text_logic.py
"""
from __future__ import annotations

import datetime as dt
import os
import sqlite3
import sys

# 让 `import x_agent.*` 在任意 cwd 下都能工作
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from x_agent.pipeline import _match_triggers
from x_agent.psych_analyzer import (
    PsychAnalyzer,
    _score_text,
    _panic_from_ratio,
)
from x_agent.industry_learner import _prefilter


# ──────────────────────────────────────────────────────────────────────────────
# 1. pipeline._match_triggers —— 触发词匹配（守护 _TRIGGER_RULES_LC 优化）
# ──────────────────────────────────────────────────────────────────────────────

def test_match_triggers_hits_keyword():
    """含触发词的文本应命中对应产业链。"""
    assert _match_triggers("英伟达 GPU 算力需求暴增") == ["AI算力"]
    assert _match_triggers("宁德时代发布新电池") == ["新能源汽车"]


def test_match_triggers_none_when_unrelated():
    """无关文本应返回空列表。"""
    assert _match_triggers("今天天气不错，出去散步喝咖啡") == []
    assert _match_triggers("") == []


def test_match_triggers_multiple_chains():
    """同时命中两条链 → 两个都返回，且去重。"""
    res = _match_triggers("AI 算力火热，新能源电动车也在涨")
    assert set(res) == {"AI算力", "新能源汽车"}
    # 去重：同一条链不会重复出现
    assert len(res) == len(set(res))


def test_match_triggers_case_insensitive():
    """同一段文本大小写不同，匹配结果必须完全一致（守护小写化优化）。"""
    base = "Nvidia GPU and LLM inference, plus EV battery from CATL"
    assert _match_triggers(base.upper()) == _match_triggers(base.lower())
    # 且确实命中了两条链（非空 → 才能真正检验大小写）
    assert set(_match_triggers(base.upper())) == {"AI算力", "新能源汽车"}


# ──────────────────────────────────────────────────────────────────────────────
# 2. psych_analyzer._score_text —— 恐慌/贪婪打分（守护 _*_KEYWORDS_LC 优化）
# ──────────────────────────────────────────────────────────────────────────────

def test_score_text_fear_only():
    """含恐慌词的文本：fear>0 且 greed==0。"""
    fear, greed = _score_text("市场暴跌，大量爆仓踩踏")
    assert fear > 0
    assert greed == 0


def test_score_text_greed_only():
    """含贪婪词的文本：greed>0 且 fear==0。"""
    fear, greed = _score_text("梭哈！上车了 moon")
    assert greed > 0
    assert fear == 0


def test_score_text_neutral():
    """无情绪词 → (0, 0)。"""
    assert _score_text("今天开了一个产品复盘会") == (0, 0)


def test_score_text_case_insensitive():
    """英文情绪词大小写不敏感（守护小写化优化）。"""
    text = "CRASH and LIQUIDATION, total PANIC"
    assert _score_text(text.upper()) == _score_text(text.lower())
    # 大小写两种写法都应打出正的恐慌分
    assert _score_text(text.upper())[0] > 0


def test_score_text_weights_additive_distinct_words():
    """不同恐慌词分值应相加（暴跌3 + 崩盘4 = 7）。"""
    assert _score_text("暴跌")[0] == 3
    assert _score_text("崩盘")[0] == 4
    assert _score_text("暴跌又崩盘")[0] == 7


def test_score_text_substring_counts_once_per_entry():
    """契约说明（非 bug）：打分用子串成员判断（kw in text），
    同一个词在文本中重复出现仍只按该关键词计一次，不随出现次数翻倍。
    这是优化前后一致的既定语义。"""
    once = _score_text("暴跌")[0]
    thrice = _score_text("暴跌暴跌暴跌")[0]
    assert once == thrice == 3


def test_score_text_fomo_duplicate_double_counts():
    """守护 _GREED_KEYWORDS_LC 的「重复项保留」：
    GREED_KEYWORDS 同时含 "FOMO":4 与 "fomo":4 两个仅大小写不同的键，
    小写化后列表里保留两条 ("fomo", 4)，因此 "fomo" 文本贪婪分应为 8。
    若此值退回 4，说明优化误把重复项去掉了 → 真实回归。"""
    fear, greed = _score_text("fomo")
    assert greed == 8, f"fomo 贪婪分应为 8（FOMO/fomo 双计），实得 {greed}"
    # 大写 FOMO 同样应为 8
    assert _score_text("FOMO")[1] == 8


# ──────────────────────────────────────────────────────────────────────────────
# 3. _panic_from_ratio + compute_panic_index —— 聚合 Panic Index
# ──────────────────────────────────────────────────────────────────────────────

def test_panic_from_ratio_bounds_and_neutral():
    """无信号居中 50；纯恐慌 → 100；纯贪婪 → 0；恒在 0–100。"""
    assert _panic_from_ratio(0, 0) == 50.0
    assert _panic_from_ratio(10, 0) == 100.0
    assert _panic_from_ratio(0, 10) == 0.0
    mixed = _panic_from_ratio(3, 1)
    assert 0.0 <= mixed <= 100.0
    assert mixed == 75.0  # 3/(3+1)*100


# ── 内存 sqlite 假 Store（不触磁盘、不触网）────────────────────────────────────

class _FakeStore:
    """最小可用 Store 替身：提供 compute_panic_index 需要的
    .conn（含 tweets 表）与 recent_price_bars()。"""

    def __init__(self, texts: list[str]):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            "CREATE TABLE tweets (id TEXT, author TEXT, text TEXT, "
            "source TEXT, url TEXT, created_at TEXT)"
        )
        # created_at 设为「现在」之后一点，确保落在 lookback 窗口内
        now = (dt.datetime.utcnow() + dt.timedelta(minutes=1)).isoformat()
        for i, t in enumerate(texts):
            self.conn.execute(
                "INSERT INTO tweets VALUES (?,?,?,?,?,?)",
                (str(i), "tester", t, "twitter", f"http://x/{i}", now),
            )
        self.conn.commit()

    def recent_price_bars(self, *_args, **_kwargs):
        # 返回空 → 价格动量修正为 0 → panic_score == base_score（确定性）
        return []


def _panic(texts: list[str]) -> dict:
    analyzer = PsychAnalyzer(_FakeStore(texts), psych_cfg={})
    return analyzer.compute_panic_index(lookback_hours=24)


def test_compute_panic_index_score_in_range():
    """任意输入 panic_score 都应落在 0–100。"""
    for texts in (["崩盘爆仓"], ["梭哈 moon"], ["开了个会"], []):
        res = _panic(texts)
        assert 0.0 <= res["panic_score"] <= 100.0


def test_compute_panic_index_no_signal_is_neutral():
    """无情绪信号 → 居中 50、dominant=neutral。"""
    res = _panic(["今天复盘了一下产品路线图", "喝了杯咖啡"])
    assert res["panic_score"] == 50.0
    assert res["dominant_emotion"] == "neutral"
    assert res["fear_count"] == 0
    assert res["greed_count"] == 0
    assert res["neutral_count"] == 2


def test_compute_panic_index_fear_dominates():
    """全恐慌帖 → 高分、dominant=panic、逆向 buy。"""
    res = _panic(["暴跌崩盘爆仓", "踩踏 血洗 归零"])
    assert res["panic_score"] >= 70.0
    assert res["dominant_emotion"] == "panic"
    assert res["contrarian_signal"] == "buy"
    assert res["fear_count"] == 2


def test_compute_panic_index_greed_dominates():
    """全贪婪帖 → 低分、dominant=greed、逆向 sell。"""
    res = _panic(["梭哈 all-in moon", "疯涨 满仓 bullish"])
    assert res["panic_score"] <= 30.0
    assert res["dominant_emotion"] == "greed"
    assert res["contrarian_signal"] == "sell"
    assert res["greed_count"] == 2


def test_compute_panic_index_direction_more_fear_higher():
    """方向性：恐慌:贪婪比例越高，panic_score 越高。
    用「比例」而非饱和值来检验（两组都含贪婪，仅恐慌占比不同）。"""
    less_fear = _panic(["看空 谨慎", "梭哈 满仓 moon bullish 疯涨"])   # 恐慌占比低
    more_fear = _panic(["暴跌 崩盘 爆仓 踩踏 血洗", "上车 加仓"])        # 恐慌占比高
    assert more_fear["panic_score"] > less_fear["panic_score"]


# ──────────────────────────────────────────────────────────────────────────────
# 4. industry_learner._prefilter —— 产业链预筛（守护 _CHAIN_HINTS_LC 优化）
# ──────────────────────────────────────────────────────────────────────────────

def test_prefilter_hits_chain_hint():
    """命中链关键词 → 返回链名。"""
    assert _prefilter("英伟达 GPU 算力紧缺") == "AI算力"
    assert _prefilter("宁德时代扩产磷酸铁锂电池") == "新能源汽车"


def test_prefilter_none_when_unrelated():
    """无关帖子 → None。"""
    assert _prefilter("今天去爬山看了日出") is None
    assert _prefilter("") is None


def test_prefilter_case_insensitive():
    """英文链关键词大小写不敏感（守护小写化优化）。"""
    assert _prefilter("nvda earnings beat") == _prefilter("NVDA earnings beat")
    assert _prefilter("NVDA EARNINGS") == "AI算力"
    assert _prefilter("byd ev sales surge") == _prefilter("BYD EV sales surge")
    assert _prefilter("BYD EV SALES") == "新能源汽车"


# ── 独立运行入口（无 pytest 时）───────────────────────────────────────────────

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
