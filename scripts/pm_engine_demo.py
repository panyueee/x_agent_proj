# -*- coding: utf-8 -*-
"""方法C 引擎回测 demo：跟随某人的【看多】判断持有对应ETF 60交易日，NAV vs 沪深300。
只取 direction=看多 且 target 能映射到 ETF 的判断；每持有期60td，重叠时等权。
成本/涨跌停沿用引擎默认(a/etf)。管道验证非统计结论。"""
import sys, json, glob
from pathlib import Path
import pandas as pd
sys.path.insert(0, ".")
from backtest.data import load_market_data, load_benchmark
from backtest.engine import run_backtest
from scripts.pm_backtest_c import TARGET_MAP

person_json, out_md = sys.argv[1], sys.argv[2]
HOLD = 60
d = json.loads(Path(person_json).read_text())
person = d["person"]

def etf_stem(target):
    if target not in TARGET_MAP: return None
    kind, key = TARGET_MAP[target]
    if kind != "etf": return None
    m = glob.glob(f"data/etf_history/{key}*.parquet")
    return Path(m[0]).stem if m else None

# 收集看多 + etf 可映射的判断
calls = [c for c in d["calls"] if c["direction"] == "看多"]
mapped = [(c["date"], etf_stem(c["target"]), c["target"]) for c in calls]
mapped = [(dt, st, tg) for dt, st, tg in mapped if st]
symbols = sorted({st for _, st, _ in mapped})
if not symbols:
    Path(out_md).write_text(f"# {person} 方法C 引擎demo\n无可映射ETF的看多判断。\n"); print("no symbols"); sys.exit()

md = load_market_data("etf", symbols, start="2025-06-01", end="2026-07-02")
cal = md.calendar
# 构造目标权重：每个看多判断，从其call_date当日起持有60个交易日；重叠等权
raw = pd.DataFrame(0.0, index=cal, columns=symbols)
for dt, st, tg in mapped:
    dt = pd.Timestamp(dt)
    pos = cal[cal >= dt]
    if len(pos) == 0: continue
    hold_days = pos[:HOLD]
    raw.loc[hold_days, st] += 1.0
# 行归一化到 ≤1（等权）
rs = raw.sum(axis=1)
w = raw.div(rs.where(rs > 0, 1.0), axis=0)

bench = load_benchmark("000300_SS", start="2025-06-01", end="2026-07-02")
res = run_backtest(md, w, benchmark=bench, name=f"{person}_bull_C", trade_at="open")
nav = res.nav
bm = res.benchmark
# 对齐到共同有效区间
common = nav.index[(nav.notna()) & (bm.notna())]
nav_f = nav.loc[common]; bm_f = bm.loc[common]
# 归一到区间起点
nav_n = nav_f / nav_f.iloc[0]; bm_n = bm_f / bm_f.iloc[0]
tot_ret = nav_n.iloc[-1] - 1
bm_ret = bm_n.iloc[-1] - 1
excess = tot_ret - bm_ret
mdd = (nav_n / nav_n.cummax() - 1).min()
lines = [f"# 方法C 引擎回测 demo — {person}（跟随看多判断持有对应ETF {HOLD}交易日）", "",
    "> LLM=sonnet subagent 提取判断；本回测用 backtest 引擎(成本/涨跌停默认)机械执行。管道验证非统计结论。",
    f"- 纳入看多判断（可映射ETF）：{len(mapped)} 条，覆盖ETF：{', '.join(symbols)}",
    f"- 回测区间：{common[0].date()} ~ {common[-1].date()}（{len(common)}交易日）",
    f"- 组合总收益：**{tot_ret*100:+.1f}%**",
    f"- 沪深300同期：**{bm_ret*100:+.1f}%**",
    f"- 超额收益：**{excess*100:+.1f}%**",
    f"- 组合最大回撤：{mdd*100:.1f}%",
    f"- 期末换手次数(trades)：{len(res.trades)}",
    "", "说明：每条看多判断映射到主题/行业ETF，从判断日起持有60交易日，重叠期等权；",
    "看空判断无法在多头引擎表达，已剔除（其方向命中见 C_eval.md 的逐笔excess打分）。"]
Path(out_md).write_text("\n".join(lines))
print("\n".join(lines[3:11]))
