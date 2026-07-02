# -*- coding: utf-8 -*-
"""方法C 打分器：分析师"判断五元组"→ 逐笔 excess-return 命中 + 引擎回测 demo。

用法：
  .venv/bin/python scripts/pm_backtest_c.py output/personas/牟一凌/C_extraction.json \
      --out output/personas/牟一凌/C_eval.md [--engine]

输入 JSON 结构（提取子 agent 产出）:
{
  "person": "牟一凌", "cutoff": "2026-03-31",
  "calls": [
    {"id":"c1","date":"2026-01-10","target":"半导体","direction":"看多",
     "benchmark_type":"relative","quote":"...原文片段...","source":"research::..."},
    ...
  ]
}
- benchmark_type: "relative"(行业/风格，对沪深300超额) | "absolute"(大类资产，看绝对方向)
- direction: 看多 / 看空 / 中性

打分（防未来函数：outcome 严格取 call_date 之后的收盘价）:
  ret_h(sym) = close[call_date + h 交易日] / close[first trading day > call_date] - 1
  relative: metric = ret_h(target) - ret_h(沪深300);  absolute: metric = ret_h(target)
  看多 hit: metric>0; 看空 hit: metric<0; 中性 hit: |metric|<=NEUTRAL_BAND
  |metric|<FLAT_BAND 记 partial（方向太弱，未能明确兑现）
horizon 固定 20 与 60 交易日两档。
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
HORIZONS = [20, 60]
FLAT_BAND = 0.005      # |metric|<0.5% 视为方向未明确兑现 → partial
NEUTRAL_BAND = 0.02    # 中性判断：|metric|<=2% 算命中

BENCH_SYM = "沪深300"

# 受控词表：分析师判断的 target 只能用这些键（提取子 agent prompt 内会给出）
# 值：("etf", 文件名关键词) 或 ("index", ticker)
TARGET_MAP: dict[str, tuple] = {
    # 大盘 / 风格
    "沪深300": ("etf", "510300_"), "上证50": ("etf", "510050_"),
    "中证500": ("etf", "510500_"), "中证1000": ("etf", "512100_"),
    "创业板": ("etf", "159915_"), "创业板50": ("etf", "159949_"),
    "科创50": ("etf", "588000_"), "深证100": ("etf", "159901_"),
    # 行业 / 主题
    "半导体": ("etf", "512480_"), "芯片": ("etf", "512760_"),
    "医药": ("etf", "512010_"), "医疗": ("etf", "512170_"),
    "军工": ("etf", "512660_"), "白酒": ("etf", "512690_"),
    "消费": ("etf", "159928_"), "房地产": ("etf", "512200_"),
    "有色": ("etf", "512400_"), "有色金属": ("etf", "512400_"),
    "银行": ("etf", "512800_"), "券商": ("etf", "512880_"),
    "证券": ("etf", "512880_"), "传媒": ("etf", "512980_"),
    "科技": ("etf", "515000_"), "新能源车": ("etf", "515030_"),
    "新能源": ("etf", "516160_"), "煤炭": ("etf", "515220_"),
    "光伏": ("etf", "515790_"), "通信": ("etf", "515880_"),
    "游戏": ("etf", "516010_"), "基建": ("etf", "516950_"),
    "电力": ("etf", "159611_"), "旅游": ("etf", "159766_"),
    "农业": ("etf", "159825_"), "中概互联": ("etf", "513050_"),
    "恒生科技": ("etf", "513180_"), "5G": ("etf", "515050_"),
    # 大类资产
    "黄金": ("etf", "518880_"), "国债": ("etf", "511010_"),
    "十年国债": ("etf", "511260_"), "可转债": ("etf", "511380_"),
    "纳指": ("etf", "513100_"), "标普500": ("etf", "513500_"),
    "恒生": ("etf", "159920_"), "港股": ("etf", "159920_"),
    "美股": ("index", "IXIC"),
}

_CACHE: dict[str, pd.Series] = {}


def load_close(target: str) -> pd.Series | None:
    """返回该 target 的收盘价序列（DatetimeIndex, 升序, 去 NaN）。"""
    if target in _CACHE:
        return _CACHE[target]
    if target not in TARGET_MAP:
        return None
    kind, key = TARGET_MAP[target]
    if kind == "etf":
        matches = glob.glob(str(DATA / "etf_history" / f"{key}*.parquet"))
        if not matches:
            return None
        df = pd.read_parquet(matches[0])
    else:
        df = pd.read_parquet(DATA / "index_history" / f"{key}.parquet")
    df["date"] = pd.to_datetime(df["date"])
    s = df.set_index("date")["close"].sort_index().dropna()
    s = s[~s.index.duplicated(keep="last")]
    _CACHE[target] = s
    return s


def fwd_return(s: pd.Series, call_date: pd.Timestamp, h: int) -> float | None:
    """严格取 call_date 之后的收盘价：进场=首个 > call_date 的交易日收盘，
    出场=其后第 h 个交易日收盘。"""
    after = s[s.index > call_date]
    if len(after) < h + 1:
        return None
    entry = after.iloc[0]
    exit_ = after.iloc[h]
    if entry == 0 or pd.isna(entry) or pd.isna(exit_):
        return None
    return float(exit_ / entry - 1.0)


def grade_call(call: dict) -> dict:
    date = pd.Timestamp(call["date"])
    target = call["target"]
    direction = call["direction"]
    btype = call.get("benchmark_type", "relative")
    ts = load_close(target)
    res = {"id": call.get("id"), "date": call["date"], "target": target,
           "direction": direction, "benchmark_type": btype}
    if ts is None:
        res["error"] = f"target 无行情映射: {target}"
        return res
    bench = load_close(BENCH_SYM) if btype == "relative" else None
    for h in HORIZONS:
        rt = fwd_return(ts, date, h)
        if rt is None:
            res[f"h{h}"] = None
            continue
        if btype == "relative":
            rb = fwd_return(bench, date, h)
            metric = None if rb is None else rt - rb
        else:
            rb = None
            metric = rt
        if metric is None:
            res[f"h{h}"] = None
            continue
        # 判定
        if direction == "中性":
            verdict = "hit" if abs(metric) <= NEUTRAL_BAND else "miss"
        elif abs(metric) < FLAT_BAND:
            verdict = "partial"
        elif direction == "看多":
            verdict = "hit" if metric > 0 else "miss"
        elif direction == "看空":
            verdict = "hit" if metric < 0 else "miss"
        else:
            verdict = "na"
        res[f"h{h}"] = {"ret_target": round(rt, 4),
                        "ret_bench": None if rb is None else round(rb, 4),
                        "metric": round(metric, 4), "verdict": verdict}
    return res


def summarize(graded: list[dict]) -> dict:
    out = {}
    for h in HORIZONS:
        cnt = {"hit": 0, "partial": 0, "miss": 0, "na": 0}
        n = 0
        for g in graded:
            cell = g.get(f"h{h}")
            if not isinstance(cell, dict):
                continue
            v = cell["verdict"]
            cnt[v] = cnt.get(v, 0) + 1
            n += 1
        scored = cnt["hit"] + cnt["partial"] + cnt["miss"]
        strict = cnt["hit"] / scored if scored else 0.0
        weighted = (cnt["hit"] + 0.5 * cnt["partial"]) / scored if scored else 0.0
        out[h] = {"n": n, **cnt, "strict_hit": round(strict, 3),
                  "weighted_hit": round(weighted, 3)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("calls_json")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    data = json.loads(Path(args.calls_json).read_text(encoding="utf-8"))
    person = data.get("person", "?")
    cutoff = data.get("cutoff", "?")
    calls = data["calls"]
    graded = [grade_call(c) for c in calls]
    summ = summarize(graded)

    lines = [f"# 方法C 逐笔命中评估 — {person}", "",
             f"- cutoff（提取只看此前语料）：{cutoff}",
             f"- 判断数：{len(calls)}",
             f"- 打分口径：excess-return 逐笔命中，horizon 20/60 交易日；行情严格取判断日之后收盘价（防未来函数）",
             "- 映射：行业/风格判断对沪深300超额；大类资产看绝对方向",
             "> LLM=sonnet subagent（手工提取判断，非 API）；本脚本机械打分，提取者无权决定哪条计入。", ""]
    for h in HORIZONS:
        s = summ[h]
        lines.append(f"## horizon={h} 交易日")
        lines.append(f"- 可打分 {s['hit']+s['partial']+s['miss']} 笔 / 数据不足 na={s['na']}")
        lines.append(f"- 严格命中率 hit-only：**{s['strict_hit']*100:.1f}%**（hit {s['hit']} / partial {s['partial']} / miss {s['miss']}）")
        lines.append(f"- 加权命中率 hit=1,partial=0.5：**{s['weighted_hit']*100:.1f}%**")
        lines.append("")
    lines.append("## 逐笔明细")
    lines.append("| id | 日期 | target | 方向 | 基准 | h20 verdict | h20 metric | h60 verdict | h60 metric |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for g in graded:
        if "error" in g:
            lines.append(f"| {g.get('id')} | {g['date']} | {g['target']} | {g['direction']} | - | ERR | {g['error']} | | |")
            continue
        def cell(h):
            c = g.get(f"h{h}")
            if not isinstance(c, dict):
                return ("na", "")
            return (c["verdict"], f"{c['metric']*100:+.1f}%")
        v20, m20 = cell(20); v60, m60 = cell(60)
        lines.append(f"| {g.get('id')} | {g['date']} | {g['target']} | {g['direction']} | {g['benchmark_type']} | {v20} | {m20} | {v60} | {m60} |")

    Path(args.out).write_text("\n".join(lines), encoding="utf-8")
    print(f"写入 {args.out}")
    print(json.dumps(summ, ensure_ascii=False))


if __name__ == "__main__":
    main()
