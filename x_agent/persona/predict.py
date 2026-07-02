# -*- coding: utf-8 -*-
"""预测引擎：画像(截止 T) + 预测问题 → 结构化预测，追加写 predictions.jsonl。"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from .llm import extract_json, load_prompt, split_sections
from .profile import _person_dir

logger = logging.getLogger(__name__)

# 中文研报标题常见 "结论——主题" 结构的分隔符（长破折号优先）
_DASH_SPLIT = re.compile(r"——|—+|--+")


def question_from_title(title: str) -> str:
    """从真实文章标题生成"去结论化"的预测问题。

    如 "出口强劲增长—6月PMI数据点评" → 主题段 "6月PMI数据点评"。
    只能部分防泄漏（主题本身可能暗示方向），报告中已注明该局限。
    """
    title = re.sub(r"^\d{4}-\d{2}-\d{2}\s+", "", title or "").strip()
    parts = [p.strip() for p in _DASH_SPLIT.split(title) if p.strip()]
    topic = parts[-1] if len(parts) >= 2 else title
    return f"该分析师即将就「{topic}」发表文章，其核心观点方向最可能是什么？"


def predict(
    person: str,
    profile: dict,
    question: str,
    llm,
    log_to_file: bool = True,
) -> dict:
    """基于画像做一次结构化预测。返回 dict 并（可选）追加写 predictions.jsonl。"""
    tpl = split_sections(load_prompt("persona_predict.md"))["PREDICT"]
    prompt = tpl.format(
        person=person,
        until_date=profile.get("until_date", ""),
        question=question,
        profile=json.dumps(
            {k: v for k, v in profile.items() if not k.startswith("_")},
            ensure_ascii=False, indent=1,
        ),
    )
    system = "你是分析师行为预测器，只依据给定画像推断，输出严格 JSON。"
    raw = llm.complete(system, prompt, max_tokens=1500)
    result = extract_json(raw)

    record = {
        "person": person,
        "question": question,
        "proposition": result.get("proposition", ""),
        "direction": result.get("direction", ""),
        "confidence": result.get("confidence", None),
        "rationale": result.get("rationale", ""),
        "profile_until": profile.get("until_date", ""),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "model": getattr(llm, "model", "unknown"),
    }
    if log_to_file:
        path = _person_dir(person) / "predictions.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record
