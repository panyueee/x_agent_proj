"""产业链自我精进：从 X/淘股吧/小红书的策略帖中提取产业链洞察，
持续完善节点图谱、上下游关系和行业事件库。

流程：
  1. 从 signals 表读取高分（score≥4）策略/金融帖，跳过已处理过的
  2. 批量送 Claude Haiku 做结构化提取（公司、关系、事件）
  3. 提取结果写入 industry_insights 表（去重）
  4. 同步合并到 industry_nodes（新公司/更新角色）和 chain_events（新事件）
"""
from __future__ import annotations

import json
import re
from typing import List

from .storage import Store
from .industry_fetcher import IndustryNode, ChainEvent

# ── 产业链关键词预筛——只有包含这些词的帖子才值得送 LLM ──────────────

_CHAIN_HINTS = {
    "AI算力":    ["AI", "算力", "GPU", "芯片", "大模型", "英伟达", "NVDA",
                   "海光", "寒武纪", "智算", "推理", "训练", "半导体"],
    "新能源汽车": ["新能源", "电动车", "EV", "电池", "碳酸锂", "磷酸铁锂",
                   "宁德", "CATL", "比亚迪", "BYD", "锂矿", "储能",
                   "正极", "负极", "隔膜", "电解液"],
}

# LLM 提取的 system prompt
_SYSTEM_PROMPT = """\
你是一名 A 股产业链研究员。用户会给你一段来自 X/淘股吧/小红书的策略或财经帖子，
请从中提取产业链结构信息，以 JSON 格式返回，不要额外说明。

输出格式（严格遵守）：
{
  "chain": "产业链名称（如：AI算力 / 新能源汽车，无关则填 null）",
  "companies": [
    {"code": "A股代码或空字符串", "name": "公司名", "role": "upstream|core|downstream|competitor"}
  ],
  "relationships": [
    {"from": "公司A", "to": "公司B", "type": "供货|采购|竞争|投资|合作", "detail": "简述"}
  ],
  "events": [
    {"title": "事件标题", "content": "简述（≤80字）", "significance": "high|medium|low"}
  ],
  "confidence": 0.0到1.0之间的浮点数
}

- 不确定的字段填空字符串或空列表，不要猜测
- companies 最多8个，events 最多3个
- A股代码格式：6位数字（如 300750），不确定则填空字符串
"""

# 不需要 LLM 的简单公司代码正则（辅助提取 A 股代码）
_CODE_RE = re.compile(r'\b([036]\d{5})\b')

# 预筛关键词只在模块加载时小写化一次，避免每条帖子都重复 h.lower()
_CHAIN_HINTS_LC = [(chain, [h.lower() for h in hints]) for chain, hints in _CHAIN_HINTS.items()]


def _prefilter(text: str) -> str | None:
    """返回命中的产业链名称，无关内容返回 None。"""
    text_lower = text.lower()
    for chain, hints in _CHAIN_HINTS_LC:
        for h in hints:
            if h in text_lower:
                return chain
    return None


def _batch_extract(posts: list[dict], llm_client) -> list[dict]:
    """把多条帖子打包成一个 LLM 请求，返回各自的提取结果列表。"""
    if not posts:
        return []

    # 一次最多处理 8 条（Haiku context 够用，控制 token 消耗）
    results = []
    for i in range(0, len(posts), 8):
        batch = posts[i:i + 8]
        user_msg = "\n\n---\n\n".join(
            f"[帖子{j+1}] 来源:{p['source']} 作者:{p['author']}\n{p['text'][:500]}"
            for j, p in enumerate(batch)
        )
        user_msg += f"\n\n请对以上 {len(batch)} 条帖子分别提取，返回 JSON 数组（{len(batch)} 个元素）。"

        try:
            resp = llm_client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=2000,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            )
            text = resp.content[0].text.strip()
            # 提取 JSON 数组
            m = re.search(r'\[.*\]', text, re.DOTALL)
            if m:
                parsed = json.loads(m.group())
                # 确保长度匹配，不足则补空
                while len(parsed) < len(batch):
                    parsed.append({})
                results.extend(parsed[:len(batch)])
            else:
                results.extend([{}] * len(batch))
        except Exception as e:
            print(f"[learner] LLM 提取失败: {e}")
            results.extend([{}] * len(batch))

    return results


def _merge_into_nodes(store: Store, extraction: dict, tweet_source: str) -> None:
    """把提取结果中的公司信息合并进 industry_nodes。"""
    chain = extraction.get("chain") or ""
    if not chain:
        return
    for comp in extraction.get("companies") or []:
        code = (comp.get("code") or "").strip()
        name = (comp.get("name") or "").strip()
        role = comp.get("role", "core")
        if not name:
            continue
        # 若 code 为空但能从 name 在现有节点里找到 code，则复用
        if not code:
            row = store.conn.execute(
                "SELECT code FROM industry_nodes WHERE name=? LIMIT 1", (name,)
            ).fetchone()
            if row:
                code = row[0]
        if code or name:
            node = IndustryNode(
                code=code or f"__unk_{name[:8]}",
                name=name, role=role, chain=chain,
            )
            store.save_industry_node(node)


def _merge_into_events(store: Store, extraction: dict, tweet_id: str,
                        tweet_source: str) -> None:
    """把提取结果中的事件信息合并进 chain_events。"""
    chain = extraction.get("chain") or ""
    if not chain:
        return
    for ev in extraction.get("events") or []:
        title = (ev.get("title") or "").strip()
        if not title:
            continue
        event = ChainEvent(
            chain=chain,
            title=title,
            content=ev.get("content", ""),
            source=f"学习提取({tweet_source})",
            url=f"tweet://{tweet_id}",
            published_at="",
            relevance_score={"high": 0.9, "medium": 0.6, "low": 0.3}.get(
                ev.get("significance", "medium"), 0.5
            ),
        )
        store.save_chain_event(event)


def run_learning_step(store: Store, cfg: dict, llm_client=None,
                       min_score: int = 4, max_posts: int = 40) -> int:
    """从高分信号帖中学习产业链洞察。

    参数：
      llm_client  — anthropic.Anthropic() 实例；为 None 时跳过 LLM，
                    仅用正则提取 A 股代码做轻量更新
      min_score   — 帖子最低分数阈值（classifier 打的分）
      max_posts   — 本轮最多处理几条

    返回：本轮成功提取洞察的帖子数。
    """
    posts = store.unprocessed_signals(min_score=min_score, limit=max_posts)
    if not posts:
        print("[learner] 无新的待学习帖子")
        return 0

    # 预筛：必须包含产业链相关词
    relevant = [(p, _prefilter(p["text"])) for p in posts]
    relevant = [(p, chain) for p, chain in relevant if chain]

    if not relevant:
        print(f"[learner] {len(posts)} 条帖子中无产业链相关内容")
        # 仍然记录为"已处理"，避免下次重复扫描
        for p, _ in [(p, None) for p in posts]:
            store.save_insight(p["id"], p["source"], "", [], [], [],
                               p["text"][:100], 0.0)
        return 0

    print(f"[learner] {len(posts)} 条帖子中有 {len(relevant)} 条命中产业链关键词")

    processed = 0

    if llm_client:
        # LLM 路径：结构化提取
        batch_posts = [p for p, _ in relevant]
        extractions = _batch_extract(batch_posts, llm_client)

        for (post, hint_chain), extraction in zip(relevant, extractions):
            chain = extraction.get("chain") or hint_chain
            confidence = float(extraction.get("confidence") or 0.5)

            # 低置信度帖子不合并入图谱，但仍记录
            if confidence >= 0.5:
                _merge_into_nodes(store, extraction, post["source"])
                _merge_into_events(store, extraction, post["id"], post["source"])

            store.save_insight(
                tweet_id=post["id"],
                source=post["source"],
                chain=chain or "",
                companies=extraction.get("companies") or [],
                relationships=extraction.get("relationships") or [],
                events=extraction.get("events") or [],
                raw_text=post["text"][:300],
                confidence=confidence,
            )
            processed += 1

        print(f"[learner] LLM 提取完成：{processed} 条，"
              f"合并节点+事件进图谱")
    else:
        # 轻量路径：仅用正则提取 A 股代码，更新节点（无 LLM 时的降级方案）
        for post, hint_chain in relevant:
            codes = _CODE_RE.findall(post["text"])
            companies = [{"code": c, "name": c, "role": "core"} for c in set(codes)]

            # 用 hint_chain 作为默认产业链
            if companies and hint_chain:
                mock_extraction = {"chain": hint_chain, "companies": companies,
                                   "relationships": [], "events": []}
                _merge_into_nodes(store, mock_extraction, post["source"])

            store.save_insight(
                tweet_id=post["id"],
                source=post["source"],
                chain=hint_chain or "",
                companies=companies,
                relationships=[],
                events=[],
                raw_text=post["text"][:300],
                confidence=0.3,   # 无 LLM，置信度低
            )
            processed += 1

        print(f"[learner] 轻量提取（无 LLM）完成：{processed} 条")

    return processed


def learning_summary(store: Store) -> str:
    """生成学习进度摘要字符串。"""
    total = store.conn.execute(
        "SELECT COUNT(*) FROM industry_insights"
    ).fetchone()[0]
    by_chain = store.conn.execute(
        "SELECT chain, COUNT(*), AVG(confidence) "
        "FROM industry_insights WHERE chain != '' GROUP BY chain"
    ).fetchall()
    by_source = store.conn.execute(
        "SELECT source, COUNT(*) FROM industry_insights GROUP BY source"
    ).fetchall()

    lines = [f"学习库共 {total} 条洞察"]
    for chain, cnt, avg_conf in by_chain:
        lines.append(f"  {chain}: {cnt} 条，平均置信度 {avg_conf:.2f}")
    for src, cnt in by_source:
        lines.append(f"  来源 {src}: {cnt} 条")
    return "\n".join(lines)
