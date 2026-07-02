# -*- coding: utf-8 -*-
"""画像/预测/评估流水线测试（FakeLLM，零 API 成本）。

覆盖：画像落盘与 until 双保险、预测记录格式（predictions.jsonl）、
标题去结论化、评估时间切分不重叠、命中率计算。
"""
import json
from datetime import datetime

import pytest

import x_agent.persona.profile as profile_mod
from x_agent.persona.corpus import Article
from x_agent.persona.evaluate import VERDICT_SCORE, judge_one, split_test_set
from x_agent.persona.llm import extract_json, split_sections
from x_agent.persona.predict import predict, question_from_title
from x_agent.persona.profile import build_profile, load_profile


PROFILE_JSON = json.dumps({
    "person": "张瑜", "analytical_framework": ["框架1"], "worldview": ["假设1"],
    "timeline": [{"period": "2026-05", "stance": "看多出口", "shift": ""}],
    "explicit_predictions": [{"date": "2026-05-10", "prediction": "出口回升", "verifiable_by": "6月数据"}],
    "style": ["数据驱动"], "summary": "测试画像",
}, ensure_ascii=False)

PREDICT_JSON = json.dumps({
    "proposition": "出口仍将强劲", "direction": "看多",
    "confidence": 0.7, "rationale": "画像显示持续看多出口",
}, ensure_ascii=False)

JUDGE_JSON = json.dumps({"verdict": "hit", "reason": "方向一致"}, ensure_ascii=False)


class FakeLLM:
    """按 prompt 内容返回预置 JSON；记录调用供断言。"""
    model = "fake-model"

    def __init__(self):
        self.prompts = []

    def complete(self, system, prompt, max_tokens=1000):
        self.prompts.append(prompt)
        if "批级笔记" in prompt and "严格输出一个 JSON" in prompt:   # REDUCE
            return f"```json\n{PROFILE_JSON}\n```"
        if "分析师行为预测器" in prompt or "预测问题" in prompt:      # PREDICT
            return f"```json\n{PREDICT_JSON}\n```"
        if "盲预测" in prompt:                                        # JUDGE
            return f"```json\n{JUDGE_JSON}\n```"
        return "- 批级笔记内容"                                       # MAP


def art(day, title, text="正文", st="wechat"):
    return Article(article_id=f"id-{day}-{title}", title=title,
                   date=datetime(2026, 6, day), source_type=st, text=text)


@pytest.fixture
def personas_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_mod, "PERSONAS_DIR", tmp_path)
    return tmp_path


# ---------- 画像 ----------

def test_build_profile_writes_json_and_md(personas_dir):
    llm = FakeLLM()
    arts = [art(1, "文A"), art(2, "文B")]
    p = build_profile("张瑜", "2026-06-10", llm, articles=arts)
    assert p["person"] == "张瑜" and p["until_date"] == "2026-06-10"
    assert p["_meta"]["n_articles"] == 2
    jp = personas_dir / "张瑜" / "profile_until_2026-06-10.json"
    mp = personas_dir / "张瑜" / "profile_until_2026-06-10.md"
    assert jp.exists() and mp.exists()
    assert "看多出口" in mp.read_text(encoding="utf-8")
    assert load_profile("张瑜", "2026-06-10")["summary"] == "测试画像"


def test_build_profile_enforces_until_even_with_injected_articles(personas_dir):
    """双保险：即使调用方塞进未来文章，build_profile 也要过滤掉。"""
    llm = FakeLLM()
    arts = [art(1, "过去文"), art(20, "未来文")]
    build_profile("张瑜", "2026-06-10", llm, articles=arts)
    # map 阶段的 prompt 里不允许出现未来文
    map_prompts = [x for x in llm.prompts if "过去文" in x or "未来文" in x]
    assert map_prompts and all("未来文" not in x for x in map_prompts)


def test_build_profile_empty_corpus_raises(personas_dir):
    with pytest.raises(ValueError):
        build_profile("张瑜", "2026-06-10", FakeLLM(), articles=[art(20, "未来文")])


# ---------- 预测 ----------

def test_question_from_title_strips_conclusion():
    q = question_from_title("出口强劲增长—6月PMI数据点评")
    assert "6月PMI数据点评" in q and "出口强劲增长" not in q
    q2 = question_from_title("2026-06-28 两个世界，两手准备——A股策略周报")
    assert "A股策略周报" in q2 and "两个世界" not in q2
    # 无破折号：用全标题
    assert "政策周观察" in question_from_title("政策周观察")


def test_predict_record_format_and_jsonl(personas_dir):
    llm = FakeLLM()
    profile = json.loads(PROFILE_JSON)
    profile["until_date"] = "2026-06-10"
    rec = predict("张瑜", profile, "测试问题？", llm)
    for key in ("person", "question", "proposition", "direction",
                "confidence", "rationale", "profile_until", "timestamp"):
        assert key in rec, key
    assert rec["direction"] == "看多" and rec["profile_until"] == "2026-06-10"
    lines = (personas_dir / "张瑜" / "predictions.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["proposition"] == "出口仍将强劲"


# ---------- 评估切分 ----------

def test_split_test_set_no_overlap_with_train():
    arts = [art(d, f"文{d}") for d in (1, 5, 10, 15, 25)]
    train_until = "2026-06-10"
    test = split_test_set(arts, train_until, test_window_days=10)
    # [06-10, 06-20)：只有 10、15 号
    assert [a.date.day for a in test] == [10, 15]
    # 训练/测试零重叠
    from x_agent.persona.corpus import normalize_until
    t0 = normalize_until(train_until)
    train = [a for a in arts if a.date < t0]
    assert {a.article_id for a in train} & {a.article_id for a in test} == set()


def test_judge_one_and_score_table():
    llm = FakeLLM()
    j = judge_one("张瑜", {"proposition": "出口仍将强劲", "direction": "看多"},
                  art(10, "出口点评", text="出口超预期强劲"), llm)
    assert j["verdict"] == "hit"
    assert VERDICT_SCORE == {"hit": 1.0, "partial": 0.5, "miss": 0.0}


# ---------- 工具 ----------

def test_extract_json_variants():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('前言 {"a": {"b": 2}} 后记') == {"a": {"b": 2}}
    with pytest.raises(ValueError):
        extract_json("没有 json")


def test_split_sections():
    s = split_sections("# 头\n\n## MAP\nmap内容\n\n## REDUCE\nreduce内容\n")
    assert s["MAP"] == "map内容" and s["REDUCE"] == "reduce内容"
