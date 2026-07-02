#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dossier 证据桥：把本项目 RAG 知识库（output/rag.db，公众号/研报/书籍/大牛/星球等
40 万+ 分块）转成 progressive-investment-research skill 能直接消费的
Source Card + 候选证据行（Number/Claim/Quote Row 的原料）。

设计意图（见 .claude/skills/.../references/data-acquisition-layer.md）：
    skill 明确不重复造搜索/PDF/RSS/RAG，而是复用"已有能力"。本项目就是那层
    已有能力——所以 dossier 做行业/公司/人物研究时，第一步不该从 web 零开始，
    而是先问我们自己的库。本桥即那个入口。

纪律（与 skill 一致）：
    - 检索结果是"候选证据"，不是已核验事实。review_status 一律标 raw。
    - 每条都带 citation_anchor（标题 + 页码/source_id），可回溯到原文。
    - 不下结论、不改任何 dossier 文件；只产出可粘贴的 markdown。

用法：
    .venv/bin/python scripts/dossier_evidence.py "碳酸锂 价格 产能" --top-k 8
    .venv/bin/python scripts/dossier_evidence.py "张瑜 汇率 判断" --source-type wechat
    .venv/bin/python scripts/dossier_evidence.py "NVDA capex" -o output/dossier/_evidence.md

不依赖 ANTHROPIC_API_KEY / VOYAGE_API_KEY：retrieve 无向量时自动降级为 BM25+FTS。
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

# 允许从项目根直接运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from x_agent import rag  # noqa: E402


# 本项目 source_type → skill 的 source_tier（保守映射，宁可低估可信度）。
# 全部经本地 RAG 取回，permission 一律 user_local_file、acquisition 为 local RAG。
_TIER_MAP = {
    "research":  ("licensed_secondary", "medium", "券商研报正文；分析师观点，非独立事实"),
    "wechat":    ("press_source_lead",  "medium", "公众号文章；作者观点/转述，需回原文核实数字"),
    "column":    ("press_source_lead",  "medium", "专栏文章（格隆汇/新浪等）；作者观点"),
    "guru":      ("expert_interview",   "medium", "海外投资人备忘录/长文；观点与框架为主"),
    "book":      ("licensed_secondary", "medium", "已出版书籍；原理性内容，非当期数据"),
    "zsxq":      ("press_source_lead",  "low",    "知识星球帖子；来源杂、需强核实"),
    "netdisk":   ("user_local_file",    "medium", "本地网盘 PDF；来源多样，看原文判定"),
    "article":   ("press_source_lead",  "medium", "新闻/资讯正文"),
}
_TIER_DEFAULT = ("user_local_file", "low", "本地入库内容，来源类型未标注")


def _anchor(meta: dict) -> str:
    """构造引用锚点：优先页码，其次 source_id。"""
    ps, pe = meta.get("page_start"), meta.get("page_end")
    if ps is not None:
        return f"p{ps}" + (f"-{pe}" if pe and pe != ps else "")
    sid = meta.get("source_id") or meta.get("id") or ""
    return sid or "(无锚点)"


def build_markdown(query: str, hits: list[dict], date: str) -> str:
    """把检索命中渲染成 Source Card + 候选 Claim/Quote 行。"""
    lines: list[str] = []
    lines.append(f"# RAG 证据检索：{query}")
    lines.append("")
    lines.append(f"> 来源：本项目 RAG（output/rag.db），检索日 {date}，命中 {len(hits)} 条。")
    lines.append("> **候选证据，非已核验事实**——数字入 Current Model 前须回原文核对。")
    lines.append("")

    if not hits:
        lines.append("_知识库无相关命中。建议改写查询或转 web 发现。_")
        return "\n".join(lines)

    # 每个命中 = 一张 Source Card + 一条候选 Claim Row
    lines.append("## Source Cards")
    lines.append("")
    for i, h in enumerate(hits, 1):
        meta = h.get("meta", {})
        st = meta.get("source_type", "")
        tier, rel, boundary = _TIER_MAP.get(st, _TIER_DEFAULT)
        sc_id = f"SC-{date.replace('-', '')}-{i:03d}"
        title = meta.get("title", "?") or "?"
        author = meta.get("author", "") or ""
        url = meta.get("url", "") or ""
        lines.append(f"### {sc_id} — 《{title}》")
        lines.append("")
        lines.append("| field | value |")
        lines.append("| --- | --- |")
        lines.append(f"| source_card_id | {sc_id} |")
        lines.append(f"| source_tier | {tier} |")
        lines.append(f"| title | {title} |")
        lines.append(f"| author_person | {author} |")
        lines.append(f"| url_or_file | {url} |")
        lines.append("| permission_status | user_local_file |")
        lines.append("| acquisition_method | local RAG (x_agent.rag.retrieve) |")
        lines.append(f"| document_id | {meta.get('source_id', '')} |")
        lines.append(f"| citation_anchor | {_anchor(meta)} |")
        lines.append(f"| reliability_baseline | {rel} |")
        lines.append(f"| retrieval_score | {h.get('score', 0):.4f} |")
        lines.append(f"| source_boundary_note | {boundary} |")
        lines.append("")
        # 候选片段（供人工提炼 Number/Claim/Quote 行；此处只做原料）
        snippet = (h.get("content", "") or "").strip().replace("\n", " ")
        if len(snippet) > 500:
            snippet = snippet[:500] + "…"
        lines.append(f"> {snippet}")
        lines.append("")

    # 给一张空的候选 Claim Row 表头，提示人工填写（不代填，避免伪造已核验）
    lines.append("## 候选 Claim Rows（人工提炼后填，review_status 起始为 raw）")
    lines.append("")
    lines.append("| claim_row_id | source_card_id | claim_type | subject | claim_text | confidence | review_status |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    lines.append("| CR-001 |  | fact/estimate/forecast/judgment |  |  | low | raw |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="把 RAG 命中转成 dossier 可用的 Source Card + 候选证据行")
    ap.add_argument("query", help="检索查询（自然语言或关键词）")
    ap.add_argument("--top-k", type=int, default=8, help="返回命中数（默认 8）")
    ap.add_argument("--source-type", default=None,
                    help="按来源过滤：research/wechat/column/guru/book/zsxq/netdisk 等")
    ap.add_argument("-o", "--out", default=None, help="输出 md 路径；缺省打印到 stdout")
    args = ap.parse_args()

    hits = rag.retrieve(args.query, top_k=args.top_k, source_type=args.source_type)
    hits = hits[: args.top_k]
    date = dt.date.today().isoformat()
    md = build_markdown(args.query, hits, date)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
        print(f"[dossier] {len(hits)} 条证据 → {out}")
    else:
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
