#!/usr/bin/env python
"""持仓录入 CLI（不可变账本：每次变更以当日 date 写一条新快照，历史可回放）。

用法：
    # 逐笔加仓/改仓（在最近快照基础上叠加，写为今日新快照）
    .venv/bin/python scripts/manage_portfolio.py add --portfolio demo \
        --symbol sh.600519 --qty 200 --cost 1450 [--weight 0.2] [--note ...]

    # 直接给整个组合定权重（覆盖式新快照）
    .venv/bin/python scripts/manage_portfolio.py set-weights --portfolio demo \
        --weights '{"sh.600519": 0.2, "NVDA": 0.2, "BTC-USD": 0.1}'

    # 查看最近快照（含 parquet 尾价现值估算）
    .venv/bin/python scripts/manage_portfolio.py show --portfolio demo
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from x_agent.risk.portfolio import infer_market, latest_close  # noqa: E402
from x_agent.storage import Store                              # noqa: E402


def _today() -> str:
    return dt.date.today().isoformat()


def cmd_add(store: Store, args) -> None:
    market = infer_market(args.symbol, store, args.data_dir)
    if market is None:
        sys.exit(f"[err] 本地找不到 {args.symbol} 的 parquet，且 securities 主表无记录")
    store.upsert_portfolio(args.portfolio)

    rows = {r["symbol"]: r for r in store.latest_positions(args.portfolio)}
    rows[args.symbol] = {
        "symbol": args.symbol, "quantity": args.qty, "cost_price": args.cost,
        "weight": args.weight, "note": args.note,
    }
    date = args.date or _today()
    n = store.save_positions_snapshot(args.portfolio, date, list(rows.values()))
    print(f"[portfolio] {args.portfolio} @ {date} 快照已写入（{n} 只，含新仓 {args.symbol}）")


def cmd_set_weights(store: Store, args) -> None:
    weights = json.loads(args.weights)
    if not isinstance(weights, dict) or not weights:
        sys.exit("[err] --weights 需要非空 JSON 对象")
    for sym in weights:
        if infer_market(sym, store, args.data_dir) is None:
            sys.exit(f"[err] 本地找不到 {sym} 的 parquet，且 securities 主表无记录")
    total = sum(weights.values())
    if abs(total - 1.0) > 0.01:
        print(f"[warn] 权重和 = {total:.4f}（报告侧会归一化到 1）")
    store.upsert_portfolio(args.portfolio)
    date = args.date or _today()
    rows = [{"symbol": s, "weight": float(w)} for s, w in weights.items()]
    n = store.save_positions_snapshot(args.portfolio, date, rows)
    print(f"[portfolio] {args.portfolio} @ {date} 权重快照已写入（{n} 只）")


def cmd_show(store: Store, args) -> None:
    rows = store.latest_positions(args.portfolio)
    if not rows:
        sys.exit(f"[err] 组合 {args.portfolio!r} 没有持仓快照")
    print(f"组合 {args.portfolio} — 快照日 {rows[0]['date']}")
    print(f"{'symbol':<14}{'market':<8}{'qty':>10}{'cost':>10}{'weight':>9}{'last':>12}")
    for r in rows:
        market = infer_market(r["symbol"], store, args.data_dir) or "?"
        px = latest_close(r["symbol"], market, args.data_dir) if market != "?" else None
        qty = f"{r['quantity']:g}" if r["quantity"] is not None else "—"
        cost = f"{r['cost_price']:.2f}" if r["cost_price"] is not None else "—"
        wt = f"{r['weight']:.2%}" if r["weight"] is not None else "—"
        last = f"{px:.2f}" if px is not None else "—"
        print(f"{r['symbol']:<14}{market:<8}{qty:>10}{cost:>10}{wt:>9}{last:>12}")


def main() -> None:
    ap = argparse.ArgumentParser(description="组合持仓录入/查看")
    ap.add_argument("--db", default="output/x_agent.db")
    ap.add_argument("--data-dir", default="data")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add", help="加/改一笔持仓（叠加最近快照写为新快照）")
    p.add_argument("--portfolio", required=True)
    p.add_argument("--symbol", required=True, help="统一 symbol，如 sh.600519 / NVDA / BTC-USD")
    p.add_argument("--qty", type=float, default=None)
    p.add_argument("--cost", type=float, default=None)
    p.add_argument("--weight", type=float, default=None)
    p.add_argument("--note", default="")
    p.add_argument("--date", default=None, help="快照日 YYYY-MM-DD（默认今天）")

    p = sub.add_parser("set-weights", help="整组合定权重（覆盖式新快照）")
    p.add_argument("--portfolio", required=True)
    p.add_argument("--weights", required=True, help='JSON，如 \'{"sh.600519":0.3}\'')
    p.add_argument("--date", default=None)

    p = sub.add_parser("show", help="查看最近快照")
    p.add_argument("--portfolio", required=True)

    args = ap.parse_args()
    store = Store(args.db)
    {"add": cmd_add, "set-weights": cmd_set_weights, "show": cmd_show}[args.cmd](store, args)


if __name__ == "__main__":
    main()
