"""跨 agent 联动管道：X 信号 → 产业链深挖 → 研报跟进。

流程：
  1. 扫描最新 X 信号，命中产业链触发词 → 写 industry_trigger 事件
  2. 消费 industry_trigger → IndustryClient 深挖板块/新闻 → 写 research_trigger 事件
  3. 消费 research_trigger → ResearchClient 抓研报+供应商动态
  4. 生成联动摘要写入 pipeline_digest.md
"""
from __future__ import annotations

import datetime as dt
import json
from typing import List

from .storage import Store
from .industry_fetcher import IndustryClient, IndustryNode, ChainEvent
from .research_fetcher import ResearchClient
from .industry_learner import run_learning_step, learning_summary


# ── 触发规则：关键词 → 产业链名称（对应 config.yaml industry.chains[].name）──

_TRIGGER_RULES: list[dict] = [
    {
        "chain": "AI算力",
        "keywords": [
            "AI", "人工智能", "算力", "GPU", "大模型", "LLM",
            "芯片", "AI chip", "semiconductor", "inference", "推理",
            "NVDA", "nvidia", "英伟达", "海光", "寒武纪",
        ],
    },
    {
        "chain": "新能源汽车",
        "keywords": [
            "新能源", "电动车", "EV", "电池", "碳酸锂", "磷酸铁锂",
            "宁德时代", "比亚迪", "CATL", "BYD", "锂矿", "储能",
        ],
    },
]


def _match_triggers(text: str) -> List[str]:
    """返回 text 命中的产业链名称列表（可能多个）。"""
    text_lower = text.lower()
    matched = []
    for rule in _TRIGGER_RULES:
        for kw in rule["keywords"]:
            if kw.lower() in text_lower:
                if rule["chain"] not in matched:
                    matched.append(rule["chain"])
                break
    return matched


def _recent_x_tweets(store: Store, hours: int = 24) -> list:
    """读取最近 N 小时的 X 推文（source=twitter）。"""
    since = (dt.datetime.utcnow() - dt.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = store.conn.execute(
        "SELECT t.id, t.author, t.text, t.url, t.created_at "
        "FROM tweets t "
        "WHERE t.source = 'twitter' AND t.created_at >= ? "
        "ORDER BY t.created_at DESC",
        (since,),
    ).fetchall()
    return [{"id": r[0], "author": r[1], "text": r[2], "url": r[3], "created_at": r[4]}
            for r in rows]


# ─────────────────────────────────────────────────────────────────
# Step 1 — X 信号扫描，写入 industry_trigger 事件
# ─────────────────────────────────────────────────────────────────

def scan_x_for_triggers(store: Store, cfg: dict, hours: int = 24) -> int:
    """扫描最近 X 推文，发现产业链触发词后写入 pipeline_events。
    返回新写入的事件数量。
    """
    # 只处理 Serenity 监控列表中的作者（或不限制，取决于 config）
    watch_authors = set()
    pipeline_cfg = cfg.get("pipeline", {})
    for group in cfg.get("account_groups", {}).values():
        watch_authors.update(a.lstrip("@").lower() for a in group)

    tweets = _recent_x_tweets(store, hours)
    written = 0

    for tw in tweets:
        if watch_authors and tw["author"].lower() not in watch_authors:
            continue

        chains = _match_triggers(tw["text"])
        for chain in chains:
            new = store.push_pipeline_event(
                source_agent="x",
                target_agent="industry",
                event_type="industry_trigger",
                payload={"chain": chain, "tweet_id": tw["id"],
                         "author": tw["author"], "text": tw["text"][:200]},
                idempotency_key=f"x|industry_trigger|{chain}|{tw['id']}",
            )
            if new:
                written += 1
                print(f"[pipeline] 触发: @{tw['author']} 提到「{chain}」→ industry_trigger")

    return written


# ─────────────────────────────────────────────────────────────────
# Step 2 — 消费 industry_trigger，产业链深挖
# ─────────────────────────────────────────────────────────────────

def run_industry_step(store: Store, cfg: dict) -> int:
    """消费所有待处理的 industry_trigger 事件，执行产业链深挖。
    返回写入的 research_trigger 事件数。
    """
    events = store.pending_pipeline_events("industry")
    if not events:
        return 0

    industry_cfg = cfg.get("industry", {})
    chain_map = {c["name"]: c for c in industry_cfg.get("chains", [])}
    client = IndustryClient()
    research_codes: dict[str, set] = {}   # chain → set of stock codes

    for ev in events:
        store.mark_pipeline_event(ev["id"], "processing")
        chain_name = ev["payload"].get("chain", "")
        chain_cfg = chain_map.get(chain_name)

        print(f"[pipeline] 产业链深挖: {chain_name}")

        # 2a. 从配置里直接保存已知节点
        if chain_cfg:
            for stock in chain_cfg.get("core_stocks", []):
                node = IndustryNode(
                    code=stock["code"], name=stock["name"],
                    role=stock.get("role", "core"), chain=chain_name,
                )
                store.save_industry_node(node)
                research_codes.setdefault(chain_name, set()).add(stock["code"])

        # 2b. 从东方财富板块拉取更多成分股（前 30 名）
        if chain_cfg and chain_cfg.get("sector_code"):
            try:
                stocks = client.fetch_sector_stocks(chain_cfg["sector_code"], max_results=30)
                for s in stocks:
                    if not s["code"]:
                        continue
                    node = IndustryNode(
                        code=s["code"], name=s["name"],
                        role="core", chain=chain_name,
                    )
                    store.save_industry_node(node)
                    research_codes.setdefault(chain_name, set()).add(s["code"])
                print(f"[pipeline]   板块成分股 {len(stocks)} 家")
            except Exception as e:
                print(f"[pipeline]   板块抓取失败: {e}")

        # 2c. 拉取关键词行业新闻
        keywords = (chain_cfg or {}).get("keywords", [chain_name])
        event_count = 0
        for kw in keywords[:3]:   # 限 3 个词避免过多请求
            try:
                news = client.fetch_company_news(kw, max_results=10)
                for ev_item in news:
                    ev_item.chain = chain_name
                    store.save_chain_event(ev_item)
                event_count += len(news)
            except Exception as e:
                print(f"[pipeline]   新闻抓取 {kw} 失败: {e}")
        print(f"[pipeline]   行业新闻 {event_count} 条")

        store.mark_pipeline_event(ev["id"], "done")

    # 2d. 对每条产业链写 research_trigger 事件
    written = 0
    for chain_name, codes in research_codes.items():
        for code in codes:
            new = store.push_pipeline_event(
                source_agent="industry",
                target_agent="research",
                event_type="research_trigger",
                payload={"stock_code": code, "chain": chain_name},
                idempotency_key=f"industry|research_trigger|{code}|"
                                + dt.datetime.utcnow().strftime("%Y-%m-%d"),
            )
            if new:
                written += 1

    print(f"[pipeline] industry step 完成，写入 {written} 条 research_trigger")
    return written


# ─────────────────────────────────────────────────────────────────
# Step 3 — 消费 research_trigger，抓研报和供应商动态
# ─────────────────────────────────────────────────────────────────

def run_research_step(store: Store, cfg: dict) -> int:
    """消费所有待处理的 research_trigger 事件，抓研报与供应商动态。
    返回保存的研报总数。
    """
    events = store.pending_pipeline_events("research")
    if not events:
        return 0

    # 供应商映射：从 config research.watch_stocks 读取
    supplier_map: dict[str, list] = {}
    for ws in cfg.get("research", {}).get("watch_stocks", []):
        supplier_map[ws["code"]] = ws.get("suppliers", [])

    client = ResearchClient()
    total_reports = 0
    processed_codes: set = set()

    for ev in events:
        store.mark_pipeline_event(ev["id"], "processing")
        code = ev["payload"].get("stock_code", "")
        chain = ev["payload"].get("chain", "")

        if code in processed_codes:
            store.mark_pipeline_event(ev["id"], "done")
            continue
        processed_codes.add(code)

        print(f"[pipeline] 研报抓取: {code}（{chain}）")

        # 3a. 东方财富研报
        try:
            reports = client.fetch_reports_eastmoney(code, max_results=10)
            for r in reports:
                store.save_report(r)
            total_reports += len(reports)
            if reports:
                print(f"[pipeline]   研报 {len(reports)} 篇，最新: {reports[0].title[:30]}")
        except Exception as e:
            print(f"[pipeline]   研报抓取失败: {e}")

        # 3b. 供应商动态（config 里配置的供应商）
        suppliers = supplier_map.get(code, [])
        for sup in suppliers:
            sup_name = sup.get("name", "") if isinstance(sup, dict) else str(sup)
            if not sup_name:
                continue
            try:
                # 用股票代码反查名称
                stock_name = _get_stock_name(store, code) or code
                updates = client.fetch_supplier_news(sup_name, stock_name, max_results=5)
                for u in updates:
                    store.save_supplier_update(u)
            except Exception as e:
                print(f"[pipeline]   供应商动态 {sup_name} 失败: {e}")

        store.mark_pipeline_event(ev["id"], "done")

    print(f"[pipeline] research step 完成，共保存 {total_reports} 篇研报")
    return total_reports


def _get_stock_name(store: Store, code: str) -> str:
    """从 industry_nodes 或 research_reports 里查股票名称。"""
    row = store.conn.execute(
        "SELECT name FROM industry_nodes WHERE code=? LIMIT 1", (code,)
    ).fetchone()
    if row:
        return row[0]
    row = store.conn.execute(
        "SELECT stock_name FROM research_reports WHERE stock_code=? LIMIT 1", (code,)
    ).fetchone()
    return row[0] if row else ""


# ─────────────────────────────────────────────────────────────────
# 联动摘要
# ─────────────────────────────────────────────────────────────────

def build_pipeline_digest(store: Store, output_path: str = "./pipeline_digest.md") -> None:
    """生成联动摘要：触发来源 + 产业链节点 + 研报评级概览。"""
    lines = [
        f"# 产业链联动摘要",
        f"生成时间：{dt.datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
    ]

    chains = store.all_chains()
    if not chains:
        lines.append("_暂无数据_")
    else:
        for chain in chains:
            nodes = store.chain_nodes(chain)
            events = store.recent_chain_events(chain, limit=5)

            lines.append(f"## {chain}")
            lines.append("")

            # 节点按角色分组
            roles = {"upstream": "上游", "core": "核心", "downstream": "下游"}
            for role_key, role_label in roles.items():
                role_nodes = [n for n in nodes if n[2] == role_key]
                if role_nodes:
                    lines.append(f"**{role_label}**：" +
                                 "、".join(f"{n[1]}({n[0]})" for n in role_nodes))
            lines.append("")

            # 最新事件
            if events:
                lines.append("**最新动态：**")
                for ev in events:
                    lines.append(f"- [{ev[5][:10]}] {ev[1]}")   # published_at, title
            lines.append("")

            # 研报评级汇总
            core_codes = [n[0] for n in nodes if n[2] == "core"]
            for code in core_codes[:3]:
                summary = store.rating_summary(code)
                if summary:
                    name = _get_stock_name(store, code) or code
                    rating_str = "  ".join(f"{k}×{v}" for k, v in summary.items())
                    lines.append(f"**{name} 研报评级**：{rating_str}")
                    recent = store.recent_reports(code, limit=3)
                    for r in recent:
                        lines.append(f"- {r[7][:10]} [{r[2]}] {r[0]}：{r[1][:40]}")
            lines.append("")
            lines.append("---")
            lines.append("")

    # 学习洞察摘要
    lines.append("## 学习洞察")
    lines.append("")
    insights = store.recent_insights(limit=10)
    if insights:
        for ins in insights:
            if not ins["chain"] or ins["confidence"] < 0.5:
                continue
            comps = "、".join(c.get("name", "") for c in ins["companies"][:3] if c.get("name"))
            rels  = "；".join(
                f"{r.get('from','?')}→{r.get('to','?')}({r.get('type','')})"
                for r in ins["relationships"][:2]
            )
            lines.append(f"- **[{ins['chain']}]** `{ins['source']}`  "
                         f"置信:{ins['confidence']:.2f}")
            if comps:
                lines.append(f"  公司: {comps}")
            if rels:
                lines.append(f"  关系: {rels}")
    else:
        lines.append("_暂无学习洞察（需运行 --source pipeline 或 use_llm: true）_")
    lines.append("")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[pipeline] 联动摘要 → {output_path}")


# ─────────────────────────────────────────────────────────────────
# 入口：完整跑一遍三步联动
# ─────────────────────────────────────────────────────────────────

def run_pipeline(store: Store, cfg: dict, llm_client=None) -> None:
    """完整联动流程：学习 → 扫描 X → 产业链深挖 → 研报跟进 → 生成摘要。"""
    print("\n═══ 联动管道启动 ═══")

    # Step 0：从已有信号帖学习产业链洞察（有 LLM 则用，无则降级到轻量模式）
    learned = run_learning_step(store, cfg, llm_client=llm_client)
    print(f"[pipeline] Step 0 完成：学习 {learned} 条帖子")
    if learned:
        print(f"[pipeline] {learning_summary(store)}")

    # Step 1：扫描 X 推文检测触发词
    triggered = scan_x_for_triggers(store, cfg)
    print(f"[pipeline] Step 1 完成：检测到 {triggered} 条新触发")

    # Step 2：产业链深挖
    research_jobs = run_industry_step(store, cfg)
    print(f"[pipeline] Step 2 完成：产生 {research_jobs} 个研报任务")

    # Step 3：研报跟进
    reports_saved = run_research_step(store, cfg)
    print(f"[pipeline] Step 3 完成：保存 {reports_saved} 篇研报")

    digest_path = cfg.get("pipeline", {}).get("digest_path", "./pipeline_digest.md")
    build_pipeline_digest(store, digest_path)

    print("═══ 联动管道完成 ═══\n")
