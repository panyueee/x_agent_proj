#!/usr/bin/env python
"""历史危机场景重放 CLI（个人版压力测试，批次二 / 提案 2）。

用法：
    .venv/bin/python scripts/run_scenario_replay.py --portfolio demo
    .venv/bin/python scripts/run_scenario_replay.py --portfolio demo --scenario 2015_crash
    .venv/bin/python scripts/run_scenario_replay.py --list

把当前组合整体放进历史危机窗口，输出各情景总损益/最大回撤/最惨单日对照，
落 output/risk/scenarios_<portfolio>.md。方法与局限见 x_agent/risk/scenarios.py。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from x_agent.risk.report import render_scenario_report  # noqa: E402
from x_agent.risk.scenarios import SCENARIOS             # noqa: E402
from x_agent.storage import Store                        # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="历史危机场景重放（压力测试）")
    ap.add_argument("--portfolio", default="demo")
    ap.add_argument("--scenario", default="all",
                    help="情景名或 all（默认）；--list 看可选项")
    ap.add_argument("--date", default=None, help="beta 估计截止日 YYYY-MM-DD（默认最新）")
    ap.add_argument("--db", default="output/x_agent.db")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--out-dir", default="output/risk")
    ap.add_argument("--list", action="store_true", help="列出情景库后退出")
    args = ap.parse_args()

    if args.list:
        print("可选情景（--scenario）：")
        for name, (s, e, b, desc) in SCENARIOS.items():
            print(f"  {name:16s} {s}~{e}  基准={b}  {desc}")
        return

    store = Store(args.db)
    path = render_scenario_report(
        store, portfolio_id=args.portfolio, date=args.date,
        scenario=args.scenario, data_dir=args.data_dir, db_path=args.db,
        out_dir=args.out_dir)
    print(f"[scenario] 已生成 {path}")
    print(path.read_text(encoding="utf-8").split("## 各情景明细")[0].rstrip())


if __name__ == "__main__":
    main()
