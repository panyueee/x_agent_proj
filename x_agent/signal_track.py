# -*- coding: utf-8 -*-
"""track-record 聚合：把 signal_performance 按信源/作者/关键词/类型聚合，回答
"哪个信源的信号真有 alpha"。反哺 classifier 打分权重（只出建议，不自动改）。

纪律：
- **多标的膨胀**：一条 13 个 ticker 的自选股推文会灌爆 obs 级统计。故先把同一 (signal, horizon)
  的多标的折叠成"信号级"（对超额取均值），headline 用信号级；同时报 n_signals 与 n_obs 两列。
- **方向假设**：所有信号默认按"做多"计收益（超额>0 记 hit）。feed 里有做空/看空/跌停论点，
  extracted 为空无法确知方向 → 对疑似做空关键词打 short_flag，报告单列披露，不当作失败的做多。
- 样本小（每桶几条）→ 报告逐桶标注"管道验证非统计结论"。
"""
from __future__ import annotations

import sqlite3

import pandas as pd

from .classifier import (STRATEGY_KEYWORDS, STRATEGY_KEYWORDS_ZH, WEB3_KEYWORDS,
                         WEB3_KEYWORDS_ZH, STOCK_KEYWORDS, STOCK_KEYWORDS_ZH,
                         FINANCE_KEYWORDS, FINANCE_KEYWORDS_ZH)

# 关键词 → 所属打分表（供反哺建议时定位改哪张表的权重）
_TABLE_OF = {}
for _name, _tbl in [("STRATEGY_KEYWORDS", STRATEGY_KEYWORDS), ("STRATEGY_KEYWORDS_ZH", STRATEGY_KEYWORDS_ZH),
                    ("WEB3_KEYWORDS", WEB3_KEYWORDS), ("WEB3_KEYWORDS_ZH", WEB3_KEYWORDS_ZH),
                    ("STOCK_KEYWORDS", STOCK_KEYWORDS), ("STOCK_KEYWORDS_ZH", STOCK_KEYWORDS_ZH),
                    ("FINANCE_KEYWORDS", FINANCE_KEYWORDS), ("FINANCE_KEYWORDS_ZH", FINANCE_KEYWORDS_ZH)]:
    for _kw in _tbl:
        _TABLE_OF.setdefault(_kw, _name)

# 英文关键词需对小写文本匹配，中文直接匹配
_EN_TABLES = {**STRATEGY_KEYWORDS, **WEB3_KEYWORDS, **STOCK_KEYWORDS, **FINANCE_KEYWORDS}
_ZH_TABLES = {**STRATEGY_KEYWORDS_ZH, **WEB3_KEYWORDS_ZH, **STOCK_KEYWORDS_ZH, **FINANCE_KEYWORDS_ZH}

# 疑似"做空/看空"关键词：命中则 short_flag（收益方向可能反转，报告单独披露）
SHORT_KEYWORDS = ["做空", "空单", "看空", "跌停", "逃顶", "short", "sell", "put", "bear market"]


def matched_keywords(text: str) -> list[str]:
    """返回 text 命中的所有打分关键词（英文按小写匹配，中文直接匹配）。"""
    if not text:
        return []
    low = text.lower()
    hits = [kw for kw in _EN_TABLES if kw in low]
    hits += [kw for kw in _ZH_TABLES if kw in text]
    return sorted(set(hits))


def is_short(text: str) -> bool:
    if not text:
        return False
    low = text.lower()
    return any(k in text or k in low for k in SHORT_KEYWORDS)


def load_joined(db_path: str = "output/x_agent.db", horizon: int = 5) -> pd.DataFrame:
    """读 signal_performance JOIN tweets（指定 horizon），带 source/author/category/text。

    返回逐 (signal, security) 行 + 派生 short_flag。excess 缺失时回退用 ret 参与统计。
    """
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT p.signal_id, p.security_id, p.market, p.horizon, p.ret, p.excess, p.hit, "
            "p.score, t.source, t.source_label, t.author, s.category, t.text "
            "FROM signal_performance p "
            "JOIN tweets t ON p.signal_id = t.id "
            "LEFT JOIN signals s ON p.signal_id = s.tweet_id "
            "WHERE p.horizon = ?", (horizon,)
        ).fetchall()
        cols = [d[0] for d in con.execute(
            "SELECT p.signal_id, p.security_id, p.market, p.horizon, p.ret, p.excess, p.hit, "
            "p.score, t.source, t.source_label, t.author, s.category, t.text "
            "FROM signal_performance p JOIN tweets t ON p.signal_id=t.id "
            "LEFT JOIN signals s ON p.signal_id=s.tweet_id WHERE 1=0").description]
    finally:
        con.close()
    df = pd.DataFrame(rows, columns=cols)
    if len(df) == 0:
        return df
    # excess 缺失（无基准/基准数据未覆盖）回退用 ret，保证统计有值；同时记真实超额覆盖
    df["metric"] = df["excess"].where(df["excess"].notna(), df["ret"])
    df["excess_real"] = df["excess"].notna().astype(int)
    df["short_flag"] = df["text"].apply(is_short)
    return df


def _signal_level(df: pd.DataFrame) -> pd.DataFrame:
    """把多标的信号折叠成信号级：同一 signal_id 的 metric/ret 取均值，元数据取首行。"""
    agg = (df.groupby("signal_id")
             .agg(metric=("metric", "mean"), ret=("ret", "mean"),
                  n_obs=("security_id", "size"), n_excess=("excess_real", "sum"),
                  source=("source", "first"), source_label=("source_label", "first"),
                  author=("author", "first"), category=("category", "first"),
                  text=("text", "first"), score=("score", "first"),
                  short_flag=("short_flag", "first"))
             .reset_index())
    agg["hit"] = (agg["metric"] > 0).astype(int)
    return agg


def _bucket_stats(sig: pd.DataFrame, key: str) -> pd.DataFrame:
    """按 key 分桶：n_signals / n_obs / hit_rate / avg_excess / avg_ret / median_excess / short_share。"""
    g = sig.groupby(key)
    out = g.agg(
        n_signals=("signal_id", "size"),
        n_obs=("n_obs", "sum"),
        n_excess=("n_excess", "sum"),
        hit_rate=("hit", "mean"),
        avg_excess=("metric", "mean"),
        median_excess=("metric", "median"),
        avg_ret=("ret", "mean"),
        short_share=("short_flag", "mean"),
    ).reset_index()
    # excess_cov = 真实超额观测占比；低说明 avg_excess 实为原始收益（基准未覆盖），别当 alpha 读
    out["excess_cov"] = out["n_excess"] / out["n_obs"]
    out = out.drop(columns=["n_excess"])
    return out.sort_values(["avg_excess", "n_signals"], ascending=[False, False]).reset_index(drop=True)


def build_track_record(db_path: str = "output/x_agent.db", horizon: int = 5) -> dict[str, pd.DataFrame]:
    """产出各维度 track-record 表：by_source / by_feed / by_author / by_category / by_keyword。

    信号级折叠后统计（多标的信号只算一票）。by_keyword 对每条信号命中的关键词做 explode。
    """
    df = load_joined(db_path, horizon)
    if len(df) == 0:
        empty = pd.DataFrame()
        return {k: empty for k in ("by_source", "by_feed", "by_author", "by_category", "by_keyword")}
    sig = _signal_level(df)

    result = {
        "by_source": _bucket_stats(sig, "source"),
        "by_feed": _bucket_stats(sig, "source_label"),
        "by_author": _bucket_stats(sig, "author"),
        "by_category": _bucket_stats(sig, "category"),
    }

    # 关键词维度：explode 每条信号命中的关键词
    kw_rows = []
    for _, r in sig.iterrows():
        for kw in matched_keywords(r["text"]):
            kw_rows.append({"keyword": kw, "signal_id": r["signal_id"], "n_obs": r["n_obs"],
                            "n_excess": r["n_excess"], "hit": r["hit"], "metric": r["metric"],
                            "ret": r["ret"], "short_flag": r["short_flag"],
                            "table": _TABLE_OF.get(kw, "")})
    kw = pd.DataFrame(kw_rows)
    if len(kw):
        by_kw = kw.groupby(["keyword", "table"]).agg(
            n_signals=("signal_id", "size"), n_obs=("n_obs", "sum"),
            n_excess=("n_excess", "sum"),
            hit_rate=("hit", "mean"), avg_excess=("metric", "mean"),
            median_excess=("metric", "median"), avg_ret=("ret", "mean"),
            short_share=("short_flag", "mean"),
        ).reset_index()
        by_kw["excess_cov"] = by_kw["n_excess"] / by_kw["n_obs"]
        by_kw = by_kw.drop(columns=["n_excess"]).sort_values(
            ["avg_excess", "n_signals"], ascending=[False, False]).reset_index(drop=True)
    else:
        by_kw = pd.DataFrame()
    result["by_keyword"] = by_kw
    return result
