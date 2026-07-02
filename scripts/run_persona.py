#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""人物建模 CLI。

用法（需 ANTHROPIC_API_KEY，见 .env）：
  .venv/bin/python scripts/run_persona.py --person 张瑜 --build-profile --until 2026-06-19
  .venv/bin/python scripts/run_persona.py --person 张瑜 --predict "下月CPI公布后对货币政策的判断" --until 2026-06-19
  .venv/bin/python scripts/run_persona.py --person 张瑜 --evaluate --train-until 2026-06-19 --test-window 30
  .venv/bin/python scripts/run_persona.py --person 张瑜 --corpus-stats   # 不花钱，看语料分布
"""
import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from x_agent.persona.corpus import PERSON_SOURCES, build_corpus  # noqa: E402
from x_agent.persona.llm import DEFAULT_MODEL, ClaudeLLM  # noqa: E402
from x_agent.persona.profile import build_profile, load_profile  # noqa: E402
from x_agent.persona.predict import predict  # noqa: E402
from x_agent.persona.evaluate import evaluate  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("run_persona")


def main():
    ap = argparse.ArgumentParser(description="人物建模：画像构建 / 观点预测 / 时间切分评估")
    ap.add_argument("--person", required=True, choices=list(PERSON_SOURCES))
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--corpus-stats", action="store_true", help="只看语料分布，不调 LLM")
    ap.add_argument("--build-profile", action="store_true")
    ap.add_argument("--until", help="画像截止日期 YYYY-MM-DD（严格小于）")
    ap.add_argument("--predict", metavar="QUESTION", help="预测问题（需已有 --until 画像）")
    ap.add_argument("--evaluate", action="store_true")
    ap.add_argument("--train-until", help="评估：训练截止日期")
    ap.add_argument("--test-window", type=int, default=30, help="评估：测试窗口天数")
    ap.add_argument("--max-test", type=int, default=None, help="评估：最多测试篇数（控成本）")
    ap.add_argument("--rebuild-profile", action="store_true", help="评估时强制重建画像")
    args = ap.parse_args()

    if args.corpus_stats:
        arts = build_corpus(args.person)
        print(f"{args.person}: {len(arts)} 篇")
        for a in arts:
            print(f"  {a.date.date()} [{a.source_type}] {a.title[:50]} ({len(a.text)} 字)")
        return

    llm = ClaudeLLM(model=args.model)
    try:
        if args.build_profile:
            if not args.until:
                ap.error("--build-profile 需要 --until")
            profile = build_profile(args.person, args.until, llm)
            print(json.dumps({k: v for k, v in profile.items() if k in ("summary", "_meta")},
                             ensure_ascii=False, indent=2))
        if args.predict:
            if not args.until:
                ap.error("--predict 需要 --until 指定画像版本")
            profile = load_profile(args.person, args.until)
            if profile is None:
                log.info("画像不存在，先构建……")
                profile = build_profile(args.person, args.until, llm)
            rec = predict(args.person, profile, args.predict, llm)
            print(json.dumps(rec, ensure_ascii=False, indent=2))
        if args.evaluate:
            if not args.train_until:
                ap.error("--evaluate 需要 --train-until")
            summary = evaluate(args.person, args.train_until, args.test_window, llm,
                               rebuild_profile=args.rebuild_profile,
                               max_test_articles=args.max_test)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        print(llm.usage_summary(), file=sys.stderr)


if __name__ == "__main__":
    main()
