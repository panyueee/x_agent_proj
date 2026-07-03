#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""报告 QA 门禁 CLI：校验一份报告文件是否符合结构/溯源/免责规范。

    .venv/bin/python scripts/qa_report.py output/digest.md --kind digest
    .venv/bin/python scripts/qa_report.py output/risk/scenarios_demo.md --kind scenario --strict

--strict：有问题时退出码非零（可串进 nightly 跑批做门禁）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from x_agent.report_qa import validate_report, PROFILES  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="报告输出 QA 校验")
    ap.add_argument("path", help="报告 md 文件路径")
    ap.add_argument("--kind", default="generic", choices=sorted(PROFILES),
                    help="报告类型（digest/dossier/risk/persona/scenario/generic）")
    ap.add_argument("--strict", action="store_true", help="有问题时退出码非零")
    args = ap.parse_args()

    text = Path(args.path).read_text(encoding="utf-8")
    issues = validate_report(text, kind=args.kind)

    if not issues:
        print(f"✓ QA 通过（{args.kind}）：{args.path}")
        return 0
    print(f"✗ QA 发现 {len(issues)} 个问题（{args.kind}）：{args.path}")
    for i in issues:
        print(f"  - {i}")
    return 1 if args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
