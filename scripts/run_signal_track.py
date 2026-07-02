# -*- coding: utf-8 -*-
"""信号绩效 + track-record 一键跑：算前瞻收益落 signal_performance，聚合信源/关键词
track-record，产出 CSV(output/signal_track/) 与建议报告(docs/signal_track_report.md)。

    .venv/bin/python scripts/run_signal_track.py --horizon 1 --since 2026-04-01

反哺只出建议，不自动改 classifier。
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=FutureWarning)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from x_agent.signal_perf import load_events, compute_performance, write_performance  # noqa: E402
from x_agent.signal_track import build_track_record  # noqa: E402

OUT_DIR = Path("output/signal_track")
REPORT = Path("docs/signal_track_report.md")
ALL_HORIZONS = (1, 5, 20)


def _fmt_pct(x) -> str:
    return "n/a" if pd.isna(x) else f"{x * 100:+.2f}%"


def _table_md(df: pd.DataFrame, cols: list[str], n: int = 12) -> str:
    if df is None or len(df) == 0:
        return "_(无样本)_\n"
    d = df.head(n)
    head = "| " + " | ".join(cols) + " |\n"
    sep = "| " + " | ".join("---" for _ in cols) + " |\n"
    body = ""
    for _, r in d.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            if c in ("avg_excess", "median_excess", "avg_ret"):
                cells.append(_fmt_pct(v))                      # 带符号百分比
            elif c in ("hit_rate", "short_share", "excess_cov"):
                cells.append("n/a" if pd.isna(v) else f"{v * 100:.0f}%")
            else:
                cells.append(str(v))
        body += "| " + " | ".join(cells) + " |\n"
    return head + sep + body


def _weight_suggestions(by_kw: pd.DataFrame, min_signals: int = 3) -> str:
    if by_kw is None or len(by_kw) == 0:
        return "_(关键词样本不足，暂无建议)_\n"
    elig = by_kw[by_kw["n_signals"] >= min_signals].copy()
    if len(elig) == 0:
        return f"_(无关键词达到 n_signals>={min_signals} 门槛，样本太小不建议动权重)_\n"
    up = elig.sort_values("avg_excess", ascending=False).head(6)
    down = elig.sort_values("avg_excess", ascending=True).head(6)
    lines = ["**建议上调权重（正超额、样本≥%d）**：" % min_signals]
    for _, r in up.iterrows():
        if r["avg_excess"] > 0:
            lines.append(f"- `{r['keyword']}`（{r['table']}）: 平均超额 {_fmt_pct(r['avg_excess'])}, "
                         f"命中 {r['hit_rate']*100:.0f}%, n_signals={r['n_signals']}, "
                         f"真实超额覆盖 {r['excess_cov']*100:.0f}%")
    lines.append("\n**建议下调/观察（负超额）**：")
    for _, r in down.iterrows():
        if r["avg_excess"] < 0:
            lines.append(f"- `{r['keyword']}`（{r['table']}）: 平均超额 {_fmt_pct(r['avg_excess'])}, "
                         f"命中 {r['hit_rate']*100:.0f}%, n_signals={r['n_signals']}, "
                         f"真实超额覆盖 {r['excess_cov']*100:.0f}%")
    return "\n".join(lines) + "\n"


def render_report(tracks: dict, perf: pd.DataFrame, horizon: int, since: str | None) -> str:
    n_perf = len(perf)
    by_mh = perf.groupby(["market", "horizon"]).size() if n_perf else pd.Series(dtype=int)
    excess_cov = f"{perf['excess'].notna().sum()}/{n_perf}" if n_perf else "0/0"
    if n_perf and "tradable_entry" in perf.columns:
        n_untrade = int((perf["tradable_entry"] == 0).sum())
    else:
        n_untrade = 0
    L = []
    L.append("# 信号 track-record 报告（signal → 前瞻收益闭环）\n")
    L.append(f"> 生成参数: horizon={horizon}, since={since or '全部'}. "
             f"Aladdin 路线图 3b. 反哺建议仅供人工核验，**未自动改 classifier**。\n")
    L.append("## 0. 重要免责\n")
    L.append("- **样本极小、且信号多集中在近两周** → 本报告是**管道验证，非统计结论**。逐桶看 n_signals，"
             "个位数样本不要据此下注。\n")
    L.append("- **方向假设**：所有信号按\"做多\"计收益。feed 内含做空/看空/跌停论点（extracted 为空无法确知方向），"
             "对疑似做空信号打了 short_share 列披露；一个\"看空且股票真跌\"的正确信号会被记成负超额，勿据此判信源好坏。\n")
    L.append("- **超额口径**：crypto 基准 BTC-USD、A股 000300、美股 GSPC。基准真实数据未覆盖的窗口"
             f"（如 000300 止于 2026-06-26）**不外推**，excess 置空、回退用原始收益。当前 excess 有效覆盖 {excess_cov}。\n")
    L.append("- **cashtag 消歧**：`$SYM` 先按加密（存在 {SYM}-USD）再按美股再按 ETF，数据驱动。\n")
    L.append("- **入场防未来函数**：入场=信号本地日\"严格之后\"的第一个交易日，收益窗口不足则丢弃该行（不补值）。\n")
    L.append(f"- **入场可成交性**：复用 backtest 涨跌停/停牌约定，入场日停牌或一字涨停记 tradable_entry=0"
             f"（收盘价无法建仓，收益虚高）；当前 {n_untrade} 行如此，未剔除仅打标，可自行过滤。\n")
    L.append("- **excess_cov 列**：该桶里\"真实超额\"观测占比。偏低说明 avg_excess 实为原始收益"
             "（基准未覆盖回退），别当 alpha 读——尤其 A 股桶。\n\n")

    L.append("## 1. signal_performance 概况\n")
    L.append(f"- 落表行数（signal×security×horizon）: **{n_perf}**\n")
    if n_perf:
        L.append("- 各市场 × horizon 行数:\n\n```\n" + by_mh.to_string() + "\n```\n")
        L.append("- 说明: 美股行情本地数据止于 ~06-29 而美股信号多为 06-27，h=1 出场即越界被丢弃；"
                 "crypto/A股 h=5、h=20 多数信号太新，待数据积累后重跑。\n\n")

    L.append(f"## 2. Track-record（horizon={horizon}，信号级折叠，多标的信号只算一票）\n")
    _sc = ["n_signals", "n_obs", "hit_rate", "avg_excess", "excess_cov", "avg_ret", "short_share"]
    L.append("### 2.1 按信源大类 (source)\n")
    L.append(_table_md(tracks["by_source"], ["source"] + _sc))
    L.append("\n### 2.2 按 feed (source_label)\n")
    L.append(_table_md(tracks["by_feed"], ["source_label"] + _sc))
    L.append("\n### 2.3 按作者 (author) — 只列样本≥2\n")
    au = tracks["by_author"]
    au = au[au["n_signals"] >= 2] if len(au) else au
    L.append(_table_md(au, ["author"] + _sc))
    L.append("\n### 2.4 按信号类型 (category)\n")
    L.append(_table_md(tracks["by_category"], ["category"] + _sc))
    L.append("\n### 2.5 按关键词 (keyword) — 只列样本≥2\n")
    kw = tracks["by_keyword"]
    kw2 = kw[kw["n_signals"] >= 2] if len(kw) else kw
    L.append(_table_md(kw2, ["keyword", "table", "n_signals", "hit_rate", "avg_excess",
                             "excess_cov", "avg_ret", "short_share"], n=20))

    L.append("\n## 3. 给 classifier 的权重调整建议（仅建议）\n")
    L.append("> 改 `x_agent/classifier.py` 对应表的权重前，先看 n_signals 是否够大。当前样本下这些只是**方向性提示**。\n\n")
    L.append(_weight_suggestions(kw))
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="信号绩效 + track-record")
    ap.add_argument("--horizon", type=int, default=1, help="报告聚合用的持有天数（默认 1，当前数据下 5/20 样本极少）")
    ap.add_argument("--since", type=str, default=None, help="仅纳入信号本地日 >= since 的信号 (YYYY-MM-DD)")
    ap.add_argument("--db", type=str, default="output/x_agent.db")
    ap.add_argument("--data-dir", type=str, default="data")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT.parent.mkdir(parents=True, exist_ok=True)

    print("[1/4] 读取信号并映射标的 ...")
    events = load_events(args.db, since=args.since, data_dir=args.data_dir)
    print(f"      事件(signal×security): {len(events)}  市场分布: {events.groupby('market').size().to_dict() if len(events) else {}}")

    print("[2/4] 计算前瞻收益 (h=1/5/20) ...")
    perf = compute_performance(events, horizons=ALL_HORIZONS, data_dir=args.data_dir)
    n = write_performance(perf, args.db)
    print(f"      signal_performance 落表 {n} 行")
    if len(perf):
        perf.to_csv(OUT_DIR / "performance.csv", index=False)

    print(f"[3/4] 聚合 track-record (horizon={args.horizon}) ...")
    tracks = build_track_record(args.db, horizon=args.horizon)
    for name, df in tracks.items():
        if len(df):
            df.to_csv(OUT_DIR / f"{name}.csv", index=False)

    print("[4/4] 渲染报告 ...")
    md = render_report(tracks, perf if len(perf) else pd.DataFrame(columns=["market", "horizon", "excess"]),
                       args.horizon, args.since)
    REPORT.write_text(md, encoding="utf-8")
    print(f"      报告: {REPORT}")
    print(f"      CSV : {OUT_DIR}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
