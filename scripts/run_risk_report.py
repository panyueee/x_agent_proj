#!/usr/bin/env python
"""组合风险因子分解日报 CLI（个人版 Green Package，批次一）。

用法：
    .venv/bin/python scripts/run_risk_report.py --portfolio demo
    .venv/bin/python scripts/run_risk_report.py --portfolio demo --date 2026-06-30
    .venv/bin/python scripts/run_risk_report.py --portfolio demo --force   # 重算冻结缓存

每晚跑批（收盘冻结）：数据日更后跑一次即可，因子收益/协方差/beta 都会缓存到
output/factors/，白天重跑同日报告只做矩阵小运算。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from x_agent.risk.report import render_risk_report  # noqa: E402
from x_agent.storage import Store                   # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="组合风险因子分解日报")
    ap.add_argument("--portfolio", default="demo")
    ap.add_argument("--date", default=None,
                    help="报告日 YYYY-MM-DD（默认因子数据最新交易日）")
    ap.add_argument("--db", default="output/x_agent.db")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--out-dir", default="output/risk")
    ap.add_argument("--force", action="store_true",
                    help="忽略缓存，重算因子收益/协方差/beta")
    args = ap.parse_args()

    store = Store(args.db)
    path = render_risk_report(
        store, portfolio_id=args.portfolio, date=args.date,
        data_dir=args.data_dir, db_path=args.db,
        out_dir=args.out_dir, force=args.force)
    print(f"[riskreport] 已生成 {path}")
    snap = store.latest_risk_snapshot(args.portfolio)
    if snap:
        te = f"{snap['te_ann']:.2%}" if snap.get("te_ann") else "—"
        print(f"[riskreport] σ_ann={snap['vol_ann']:.2%} VaR99_1d={snap['var99_1d']:.2%} "
              f"TE={te} 因子/特质={snap['factor_vol']:.2%}/{snap['specific_vol']:.2%}")


if __name__ == "__main__":
    main()
