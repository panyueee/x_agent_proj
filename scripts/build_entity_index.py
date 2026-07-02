#!/usr/bin/env python
"""幂等重建 security_mentions 反向索引 + 打印覆盖率报告。

用法:
    .venv/bin/python scripts/build_entity_index.py [--db output/x_agent.db] [--no-rebuild]
    .venv/bin/python scripts/build_entity_index.py --view sz.300750   # 看某标的跨来源汇总

中间产物落 output/entity/build_report.json。
"""
from __future__ import annotations

import os
import sys
import json
import argparse
import datetime as dt

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from x_agent.entity_resolver import (  # noqa: E402
    build_security_mentions,
    get_security_view,
    DEFAULT_DB,
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--no-rebuild", action="store_true", help="不清空、增量追加")
    ap.add_argument("--view", default=None, help="打印某 security_id 的跨来源汇总后退出")
    args = ap.parse_args()

    if args.view:
        view = get_security_view(args.view, db_path=args.db)
        print(json.dumps(view, ensure_ascii=False, indent=2, default=str))
        return

    stats = build_security_mentions(args.db, rebuild=not args.no_rebuild)

    out_dir = os.path.join(_ROOT, "output", "entity")
    os.makedirs(out_dir, exist_ok=True)
    report = {"generated_at": dt.datetime.now().isoformat(timespec="seconds"), **stats}
    with open(os.path.join(out_dir, "build_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("=== security_mentions 重建完成 ===")
    print(f"总行数              : {stats['total_rows']}")
    print(f"覆盖不同标的        : {stats['distinct_securities']}")
    print(f"高置信(>=0.9)/低置信: {stats['high_conf']} / {stats['low_conf']}")
    print("按来源:")
    for st, n in sorted(stats["by_source"].items()):
        scanned = stats["scanned"].get(st, 0)
        res = stats["resolvable"].get(st, 0)
        print(f"  {st:10s} 提及{n:5d} 行  | 源记录 {scanned} 条, 其中 {res} 条解析出>=1标的")
    print("按方法:")
    for m, n in sorted(stats["by_method"].items(), key=lambda x: -x[1]):
        print(f"  {m:20s} {n}")
    print(f"\n报告已写入 {os.path.join(out_dir, 'build_report.json')}")


if __name__ == "__main__":
    main()
