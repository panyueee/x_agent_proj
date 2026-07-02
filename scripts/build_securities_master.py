#!/usr/bin/env python
"""构建证券主表 securities（一次性 + 幂等重跑，约 2.1 万行，<1 分钟）。

数据来源全部现成：
  - backtest.data.list_symbols：各市场 parquet 清单（has_parquet=1）
  - data/a_share_names.json：A 股代码 → 名称
  - sw_sector_cache 表：GICS 行业（6 位裸代码 → sh./sz. 前缀转换）

用法：
    .venv/bin/python scripts/build_securities_master.py [--db output/x_agent.db] [--data-dir data]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest.data import MARKET_DIRS, list_symbols  # noqa: E402
from x_agent.risk.factors import load_sector_map     # noqa: E402
from x_agent.storage import Store                    # noqa: E402


def build_records(data_dir: str, sector_map: dict[str, str]) -> list[dict]:
    names_path = Path(data_dir) / "a_share_names.json"
    try:
        names = json.loads(names_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        names = {}

    records: list[dict] = []
    for market in sorted(MARKET_DIRS):
        for symbol in list_symbols(market, data_dir):
            name, aliases = "", []
            if market == "a":
                name = names.get(symbol, "")
                bare = symbol.split(".")[-1]
                aliases = [bare] + ([name] if name else [])
            elif market == "etf" and "_" in symbol:
                # 文件名形如 159915_创业板ETF
                code, _, etf_name = symbol.partition("_")
                name, aliases = etf_name, [code]
            elif market == "crypto" and symbol.endswith("-USD"):
                aliases = [f"${symbol[:-4]}"]  # $BTC 写法，供信号 ticker 解析
            records.append({
                "symbol": symbol,
                "market": market,
                "name": name,
                "sector_gics": sector_map.get(symbol, ""),
                "aliases": aliases,
                "has_parquet": 1,
            })
    return records


def main() -> None:
    ap = argparse.ArgumentParser(description="构建/刷新 securities 证券主表")
    ap.add_argument("--db", default="output/x_agent.db")
    ap.add_argument("--data-dir", default="data")
    args = ap.parse_args()

    sector_map = load_sector_map(args.db)
    records = build_records(args.data_dir, sector_map)
    store = Store(args.db)
    n = store.upsert_securities(records)

    by_market = {}
    for r in records:
        by_market[r["market"]] = by_market.get(r["market"], 0) + 1
    print(f"[securities] 写入 {n} 行：" +
          " ".join(f"{m}={c}" for m, c in sorted(by_market.items())))
    n_sector = sum(1 for r in records if r["sector_gics"])
    print(f"[securities] 带 GICS 行业 {n_sector} 只（来自 sw_sector_cache）")


if __name__ == "__main__":
    main()
