"""把库里命中的信号汇总成一份 Markdown 摘要。"""
from __future__ import annotations

import json
import datetime as dt


# 是否给摘要的头部信号附加「相关投资原理」书籍注解。
# 纯本地检索（rag.retrieve，BM25/FTS5），不触发 LLM、不需要任何 API key。
# 想整体关掉直接把这里改成 False，或调 build_digest(annotate_books=False)。
BOOK_ANNOTATION_ENABLED = True


def _book_annotation(query: str, top_k: int = 2, min_score: float = 0.01) -> str:
    """从书籍知识库检索与 query 相关的投资原理，格式化为简短 Markdown 引用块。

    走 rag.retrieve(..., source_type="book")，纯检索、不调用 LLM、无需 API key。
    返回形如：

        > 📚 **相关投资原理**
        > 截断后的片段…  —— 《书名》 by 作者

    知识库缺失/为空、rag 导入失败、无命中或得分过低时一律返回 ""，
    调用方据此跳过注解，摘要其余部分行为完全不变。
    """
    try:
        from x_agent import rag
        # 书库为空就别查了，省得做无用功
        if rag.collection_stats().get("by_type", {}).get("book", 0) == 0:
            return ""
        hits = rag.retrieve(query, top_k=top_k, source_type="book")
    except Exception:
        # rag 不可用 / DB 缺失或被锁 / 检索异常 —— 一律降级为无注解
        return ""

    quotes = []
    for h in hits[:top_k]:
        if h.get("score", 0.0) < min_score:
            continue
        # 清掉换行/多余空白，截成一行短摘要
        snippet = " ".join((h.get("content") or "").split())[:150].strip()
        if not snippet:
            continue
        meta = h.get("meta", {}) or {}
        # 入库时书名常为「书名 — 章节」，注解只取书名部分更干净
        title = (meta.get("title") or "").split(" — ")[0].strip() or "未知来源"
        author = (meta.get("author") or "").strip()
        attribution = f"《{title}》" + (f" by {author}" if author else "")
        quotes.append(f"> {snippet}…  —— {attribution}")

    if not quotes:
        return ""
    return "\n".join(["> 📚 **相关投资原理**"] + quotes)


def _format_pct(pct: float) -> str:
    """格式化涨跌幅，加上 + / - 符号和颜色前缀（纯文本用箭头区分）。"""
    if pct >= 0:
        return f"+{pct:.2f}%"
    return f"{pct:.2f}%"


def _market_section(store, market: str, title: str, limit: int = 30) -> list:
    """生成单一市场的行情 Markdown 行列表。"""
    try:
        rows = store.recent_price_bars(market, limit=limit)
    except Exception:
        return []
    if not rows:
        return []

    lines = [f"### {title}", ""]
    lines.append("| 代码 | 名称 | 最新价 | 涨跌幅 | 更新时间 |")
    lines.append("| ---- | ---- | ------: | ------: | -------- |")
    for row in rows:
        # row: (symbol, name, market, timestamp, open, high, low, close, volume, change_pct)
        symbol, name, _market, timestamp, _o, _h, _l, close, _vol, change_pct = row
        ts_short = timestamp[:16].replace("T", " ") if timestamp else "-"
        pct_str = _format_pct(change_pct)
        lines.append(f"| `{symbol}` | {name} | {close:.4g} | {pct_str} | {ts_short} |")
    lines.append("")
    return lines


def _portfolio_section(store) -> list:
    """生成组合权重建议板块。"""
    try:
        pw = store.latest_portfolio_weights()
    except Exception:
        return []
    if not pw or not pw.get("weights"):
        return []

    weights = pw["weights"]
    views   = pw.get("views", {})
    method_cn = {"black_litterman": "Black-Litterman（信号加权）",
                 "max_sharpe": "最大夏普（历史收益）",
                 "equal_weight": "等权组合"}.get(pw["method"], pw["method"])
    ts = pw["computed_at"][:16].replace("T", " ")

    lines = ["## 📊 组合权重建议", ""]
    lines.append(f"方法：**{method_cn}**  （{ts} UTC）")
    lines.append("")
    lines.append("| 品种 | 权重 | 信号观点 |")
    lines.append("| ---- | ---: | -------- |")
    for sym, w in sorted(weights.items(), key=lambda x: x[1], reverse=True):
        if w < 0.001:
            continue
        view_str = f"{views[sym]:+.1%}" if sym in views else "—"
        lines.append(f"| `{sym}` | {w:.1%} | {view_str} |")
    lines.append("")
    return lines


def _psych_section(store) -> list:
    """生成 Panic Index 板块的 Markdown 行列表。"""
    try:
        snapshots = store.recent_panic_snapshots(limit=5)
    except Exception:
        return []
    if not snapshots:
        return []

    latest = snapshots[0]
    score  = latest["panic_score"]
    emotion_cn = {
        "panic":   "恐慌",
        "greed":   "贪婪",
        "neutral": "中性",
    }.get(latest["dominant_emotion"], latest["dominant_emotion"])
    signal_cn = {
        "buy":     "逆向买入预警",
        "sell":    "逆向减仓预警",
        "neutral": "无逆向信号",
    }.get(latest["contrarian_signal"], latest["contrarian_signal"])

    # 温度计条形
    filled = int(score / 5)
    bar = "█" * filled + "░" * (20 - filled)

    ts = latest["computed_at"][:16].replace("T", " ")
    lines = ["## 🧠 市场心理 / Panic Index", ""]
    lines.append(f"**{score:.0f} / 100** `[{bar}]`  —  {emotion_cn}  ·  {signal_cn}")
    lines.append("")
    lines.append(
        f"恐慌信号帖 **{latest['fear_count']}** 条 / "
        f"贪婪信号帖 **{latest['greed_count']}** 条 / "
        f"共扫描 {latest['total_posts']} 条  （{ts} UTC）"
    )

    llm = latest.get("llm_report") or {}
    if not llm:
        lines.append("\n*（LLM 解读未启用，在 config.yaml 设置 psych.use_llm: true 开启）*")
    if llm:
        sentiment_cn = {"bullish": "看多", "bearish": "看空", "neutral": "中性"}.get(
            llm.get("sentiment", ""), ""
        )
        confidence_cn = {"high": "高", "medium": "中", "low": "低"}.get(
            llm.get("confidence", ""), ""
        )
        if llm.get("market_phase"):
            suffix = f"  （置信：{confidence_cn}）" if confidence_cn else ""
            lines.append(f"\n**市场阶段**：{llm['market_phase']}  逆向倾向：{sentiment_cn}{suffix}")
        if llm.get("crowd_psychology"):
            lines.append(f"\n> {llm['crowd_psychology']}")
        if llm.get("key_drivers"):
            lines.append("\n**情绪驱动**：" + "、".join(llm["key_drivers"]))
        if llm.get("contrarian_rationale"):
            lines.append(f"\n**逆向逻辑**：{llm['contrarian_rationale']}")
        if llm.get("short_term_outlook"):
            lines.append(f"\n**24-48h 展望**：{llm['short_term_outlook']}")
        if llm.get("risk_warning"):
            lines.append(f"\n**风险提示**：{llm['risk_warning']}")

    if len(snapshots) > 1:
        lines.append("\n**历史趋势**（最近 5 次）：")
        lines.append("| 时间 | Panic Index | 情绪 | 信号 |")
        lines.append("| ---- | ----------: | ---- | ---- |")
        for s in snapshots:
            t = s["computed_at"][:16].replace("T", " ")
            em = {"panic": "恐慌", "greed": "贪婪", "neutral": "中性"}.get(
                s["dominant_emotion"], s["dominant_emotion"]
            )
            sig = {"buy": "买入↑", "sell": "卖出↓", "neutral": "─"}.get(
                s["contrarian_signal"], s["contrarian_signal"]
            )
            lines.append(f"| {t} | {s['panic_score']:.0f} | {em} | {sig} |")

    lines.append("")
    return lines


def _tgb_section(store, limit: int = 20) -> list:
    """生成淘股吧大V帖子 + 评论板块。"""
    try:
        rows = store.conn.execute(
            "SELECT author, text, url, created_at, group_tag "
            "FROM tweets WHERE source='taoguba' "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    except Exception:
        return []
    if not rows:
        return []

    posts   = [(a, t, u, c) for a, t, u, c, g in rows if g == "taoguba"]
    replies = [(a, t, u, c) for a, t, u, c, g in rows if g == "taoguba_reply"]

    lines = ["## 📝 淘股吧动态", ""]

    if posts:
        lines.append(f"### 博文（{len(posts)} 篇）")
        lines.append("")
        for author, text, url, created_at in posts[:10]:
            ts = (created_at or "")[:16].replace("T", " ")
            # 取标题行（第一行）和正文摘要
            title_line = text.split("\n")[0][:80]
            lines.append(f"- **{author}** · {ts}")
            lines.append(f"  > {title_line}")
            if url:
                lines.append(f"  - {url}")
        lines.append("")

    if replies:
        lines.append(f"### 评论/回复（{len(replies)} 条）")
        lines.append("")
        for author, text, url, created_at in replies[:10]:
            ts = (created_at or "")[:16].replace("T", " ")
            # text 格式：[评论]原文标题\n评论内容
            summary = text.replace("[评论]", "→ ").strip()[:200]
            lines.append(f"- **{author}** · {ts}")
            lines.append(f"  > {summary}")
            if url:
                lines.append(f"  - {url}")
        lines.append("")

    return lines


def _rag_section() -> list:
    """显示 RAG 知识库入库统计。"""
    try:
        from x_agent.rag import collection_stats
        stats = collection_stats()
    except Exception:
        return []
    if stats.get("total_chunks", 0) == 0:
        return []

    by_type = stats.get("by_type", {})
    lines = ["## 📚 知识库状态", ""]
    lines.append(f"共 **{stats['total_chunks']}** 个知识块  |  书籍 **{stats['book_count']}** 本")
    lines.append("")
    lines.append("| 类型 | 块数 |")
    lines.append("| ---- | ---: |")
    type_cn = {"book": "微信读书", "pdf": "PDF 文档", "article": "文章",
                "report": "研报", "other": "其他"}
    for t, n in sorted(by_type.items(), key=lambda x: x[1], reverse=True):
        lines.append(f"| {type_cn.get(t, t)} | {n} |")
    lines.append("")
    return lines


def _wencai_section(store, limit: int = 200) -> list:
    """生成问财自然语言选股板块：只展示最新一天的结果，按查询语句分组。"""
    try:
        rows = store.recent_wencai_picks(limit=limit)
    except Exception:
        return []
    if not rows:
        return []

    # rows 已按 date 倒序，只取最新一天
    latest_date = rows[0][0]
    rows = [r for r in rows if r[0] == latest_date]

    # 按查询语句分组（保持出现顺序）
    grouped: dict[str, list] = {}
    for r in rows:
        grouped.setdefault(r[1], []).append(r)

    lines = ["## 🔍 问财选股", ""]
    lines.append(f"日期：**{latest_date}**  （同花顺问财自然语言查询）")
    lines.append("")
    for query, picks in grouped.items():
        label = picks[0][2]
        title = f"### {query}" + (f"  `{label}`" if label else "")
        lines.append(f"{title}  — {len(picks)} 只")
        lines.append("")
        lines.append("| 代码 | 名称 | 最新价 | 涨跌幅 |")
        lines.append("| ---- | ---- | ------: | ------: |")
        for _date, _q, _label, code, name, price, change_pct in picks:
            price_str = f"{price:.4g}" if price is not None else "—"
            pct_str = _format_pct(change_pct) if change_pct is not None else "—"
            lines.append(f"| `{code}` | {name} | {price_str} | {pct_str} |")
        lines.append("")
    return lines


def _factor_section(store) -> list:
    """因子收益率摘要：最近 20 天各因子表现。"""
    try:
        cur = store.conn.execute(
            "SELECT * FROM factor_returns ORDER BY date DESC LIMIT 20"
        )
        rows = cur.fetchall()
        if not rows:
            return []
        # 列名直接取自同一游标的 description，省去额外的 SELECT 查询
        cols = [d[0] for d in cur.description]
    except Exception:
        return []

    import json as _json
    lines = ["## 📊 因子收益率（近 20 日）", ""]

    # 表头
    factor_cols = [c for c in cols if c != "date"]
    header = "| 日期 | " + " | ".join(f"{c[:8]}" for c in factor_cols) + " |"
    sep    = "| --- | " + " | ".join("---:" for _ in factor_cols) + " |"
    lines += [header, sep]

    for row in rows:
        d = dict(zip(cols, row))
        vals = " | ".join(
            f"{d[c]*100:+.2f}%" if d[c] is not None else "—"
            for c in factor_cols
        )
        lines.append(f"| {d['date']} | {vals} |")

    # 累计收益摘要
    try:
        cum = store.conn.execute(
            "SELECT " + ", ".join(f"AVG({c})" for c in factor_cols) +
            " FROM factor_returns"
        ).fetchone()
        if cum:
            lines.append("")
            lines.append("**日均因子收益（全周期）**：")
            parts = [f"{c[:8]}={v*100:+.3f}%" for c, v in zip(factor_cols, cum) if v is not None]
            lines.append("  " + " | ".join(parts))
    except Exception:
        pass

    lines.append("")
    return lines


def _risk_section(store) -> list:
    """组合风险快照摘要：读 risk_snapshots 最新一条（表不存在/为空返回 []）。"""
    try:
        snap = store.latest_risk_snapshot()
    except Exception:
        return []
    if not snap:
        return []

    lines = [f"## 🛡 组合风险 — {snap['portfolio_id']} @ {snap['date']}", ""]
    te = f"{snap['te_ann']*100:.2f}%" if snap.get("te_ann") else "—"
    lines.append(
        f"- 年化波动 **{snap['vol_ann']*100:.2f}%** | 1日99%VaR {snap['var99_1d']*100:.2f}% "
        f"| TE {te} | 因子/特质 {snap['factor_vol']*100:.2f}%/{snap['specific_vol']*100:.2f}%"
    )
    contrib = snap.get("risk_contrib") or {}
    if contrib:
        top = sorted(contrib.items(), key=lambda kv: abs(kv[1]), reverse=True)[:5]
        parts = [f"{k}={v*100:+.1f}%" for k, v in top]
        lines.append("- 风险贡献 Top 因子：" + " | ".join(parts))
    stocks = snap.get("stock_contrib") or []
    if stocks:
        parts = [f"{s.get('name') or s['symbol']}={s['pct']*100:.1f}%" for s in stocks[:5]]
        lines.append("- 个股贡献 Top：" + " | ".join(parts))
    lines.append("- 详情见 `output/risk_report.md`")
    lines.append("")
    return lines


def build_digest(store, path: str, annotate_books: bool | None = None) -> str:
    # annotate_books=None 时沿用模块级开关 BOOK_ANNOTATION_ENABLED；
    # 显式传 True/False 可临时覆盖，便于按调用方需求开关书籍注解。
    do_annotate = BOOK_ANNOTATION_ENABLED if annotate_books is None else annotate_books

    rows = store.recent_signals(["strategy", "web3", "both"], limit=80)
    strat = [r for r in rows if r[4] in ("strategy", "both")]
    web3 = [r for r in rows if r[4] in ("web3", "both")]

    lines = [f"# X 资讯摘要 — {dt.datetime.utcnow():%Y-%m-%d %H:%M} UTC", ""]

    lines.append(f"## 📈 交易策略信号（{len(strat)} 条）")
    lines.append("")
    for author, text, url, _created, _cat, score, tickers, extracted in strat:
        tk = ", ".join(json.loads(tickers)) or "-"
        lines.append(f"- **@{author}** · {tk} · 评分 {score}")
        lines.append(f"  > {text.strip()[:240]}")
        ex = json.loads(extracted) if extracted else {}
        if ex:
            lines.append(
                f"  - 方向 `{ex.get('direction')}` | 入场 `{ex.get('entry')}` "
                f"| 目标 `{ex.get('target')}` | 止损 `{ex.get('stop')}` "
                f"| 置信 `{ex.get('confidence')}`"
            )
            if ex.get("thesis"):
                lines.append(f"  - 逻辑：{ex['thesis']}")
        lines.append(f"  - {url}")
    # ── 头部策略信号附一段书籍投资原理（可选，无命中则不输出）──
    if do_annotate and strat:
        note = _book_annotation(strat[0][1])
        if note:
            lines.append("")
            lines.append(note)
    lines.append("")

    lines.append(f"## 🌐 Web3 资讯（{len(web3)} 条）")
    lines.append("")
    for author, text, url, _created, _cat, score, _tickers, _extracted in web3:
        lines.append(f"- **@{author}** · 评分 {score}")
        lines.append(f"  > {text.strip()[:240]}")
        lines.append(f"  - {url}")
    # ── 头部 Web3 信号附一段书籍投资原理（可选，无命中则不输出）──
    if do_annotate and web3:
        note = _book_annotation(web3[0][1])
        if note:
            lines.append("")
            lines.append(note)
    lines.append("")

    # ---- 市场行情板块（从 price_bars 表读取）----
    lines.append("## 💹 市场行情")
    lines.append("")
    lines += _market_section(store, "a_shares",  "A 股")
    lines += _market_section(store, "us_stocks", "美 股")
    lines += _market_section(store, "crypto",    "加密货币")
    lines += _market_section(store, "index",     "全球指数")

    # ── 淘股吧大V动态板块 ──
    lines += _tgb_section(store)

    # ── 问财选股板块 ──
    lines += _wencai_section(store)

    # ── 因子收益率板块 ──
    lines += _factor_section(store)

    # ── 组合风险板块（因子行情 → 组合风险 → 调仓建议的逻辑顺序）──
    lines += _risk_section(store)

    # ── 组合权重板块 ──
    lines += _portfolio_section(store)

    # ── 市场心理板块 ──
    lines += _psych_section(store)

    # ── RAG 知识库状态 ──
    lines += _rag_section()

    # ── 溯源页脚 + 输出 QA 门禁 ──
    from x_agent.report_qa import provenance_footer, qa_and_warn
    out = "\n".join(lines) + provenance_footer("见各板块（X/淘股吧/东财/RAG 等）")
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)
    qa_and_warn(out, "digest")   # 非硬拦，仅打印规范问题
    return out
