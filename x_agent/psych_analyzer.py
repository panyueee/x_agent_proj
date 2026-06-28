"""市场恐慌/贪婪指数分析模块（Panic Index）。

三层架构：
  第一层：关键词情绪扫描（恐慌词 vs 贪婪词）+ 价格动量辅助
  第二层：加权聚合 → panic_score (0–100，越高越恐慌)
  第三层：可选 LLM 心理解读，输出结构化 JSON
"""
from __future__ import annotations

import json
import datetime as dt
import re

# ── 恐慌关键词（权重越高代表情绪强度越大）──
FEAR_KEYWORDS: dict[str, int] = {
    # 中文强情绪
    "暴跌": 3, "崩盘": 4, "爆仓": 4, "踩踏": 3, "恐慌性": 3,
    "割肉": 3, "斩仓": 3, "爆了": 3, "归零": 4, "清仓": 2,
    "恐慌": 2, "跑路": 3, "暴雷": 3, "血洗": 3, "跌穿": 2,
    "崩了": 3, "跌惨": 2, "死了": 2, "套牢": 2, "亏损": 1,
    # 中文弱情绪
    "止损": 1, "下跌": 1, "看空": 1, "做空": 1, "减仓": 1,
    "谨慎": 1, "风险": 1, "利空": 1, "压力": 1,
    # 英文强情绪
    "crash": 3, "rekt": 4, "liquidat": 4, "rug": 4, "panic": 3,
    "dump": 2, "bearish": 2, "collapse": 3, "bleeding": 2, "dead": 2,
    "fear": 2, "scared": 2, "selling": 1, "selloff": 2,
    # 英文弱情绪
    "caution": 1, "risky": 1, "uncertain": 1, "worried": 1,
}

# ── 贪婪关键词 ──
GREED_KEYWORDS: dict[str, int] = {
    # 中文强情绪
    "暴涨": 3, "梭哈": 4, "all in": 4, "all-in": 4, "FOMO": 4,
    "飞了": 3, "冲了": 3, "上车": 2, "加仓": 2, "满仓": 3,
    "无脑买": 3, "疯涨": 3, "无限涨": 2, "全仓": 3,
    # 中文弱情绪
    "涨涨涨": 2, "牛市": 2, "看多": 1, "做多": 1, "入场": 1,
    "买买买": 2, "利好": 1, "机会": 1,
    # 英文强情绪
    "moon": 3, "mooning": 3, "pump": 2, "bullish": 2, "aping": 3,
    "all in": 4, "yolo": 4, "send it": 3, "supercycle": 2,
    "parabolic": 3, "euphoria": 3, "greed": 2, "fomo": 4,
    # 英文弱情绪
    "buy the dip": 2, "opportunity": 1, "upside": 1, "breakout": 1,
}


def _score_text(text: str) -> tuple[int, int]:
    """返回 (fear_score, greed_score)，不区分大小写。"""
    lower = text.lower()
    fear  = sum(w for kw, w in FEAR_KEYWORDS.items()  if kw.lower() in lower)
    greed = sum(w for kw, w in GREED_KEYWORDS.items() if kw.lower() in lower)
    return fear, greed


def _panic_from_ratio(fear_total: int, greed_total: int) -> float:
    """将恐慌/贪婪原始分转为 0–100 的 Panic Index。"""
    total = fear_total + greed_total
    if total == 0:
        return 50.0   # 无信号时居中
    return min(100.0, max(0.0, fear_total / total * 100))


def _price_momentum_adjustment(store) -> float:
    """
    读取加密货币/大盘近期涨跌幅，给 Panic Index 加减分。
    返回调整量（正=加恐慌，负=减恐慌），最大 ±15 分。
    """
    adjustment = 0.0
    try:
        crypto_rows = store.recent_price_bars("crypto", limit=5)
        for row in crypto_rows:
            change_pct = row[9]  # change_pct 字段
            if change_pct is not None:
                if change_pct <= -5:
                    adjustment += 8
                elif change_pct <= -2:
                    adjustment += 4
                elif change_pct >= 5:
                    adjustment -= 6
                elif change_pct >= 2:
                    adjustment -= 3
    except Exception:
        pass
    return max(-15.0, min(15.0, adjustment))


class PsychAnalyzer:
    """
    扫描近 N 小时入库推文，计算市场恐慌指数。
    """

    def __init__(self, store, psych_cfg: dict):
        self.store = store
        self.cfg   = psych_cfg

    def _recent_texts(self, lookback_hours: int) -> list[dict]:
        """从 DB 取近 N 小时的所有帖子文本。"""
        since = (dt.datetime.utcnow() - dt.timedelta(hours=lookback_hours)).isoformat()
        rows = self.store.conn.execute(
            "SELECT id, author, text, source, url FROM tweets "
            "WHERE created_at >= ? ORDER BY created_at DESC LIMIT 2000",
            (since,),
        ).fetchall()
        return [
            {"id": r[0], "author": r[1], "text": r[2], "source": r[3], "url": r[4]}
            for r in rows
        ]

    def compute_panic_index(self, lookback_hours: int = 24) -> dict:
        """
        返回 dict：
          panic_score        0–100（越高越恐慌）
          fear_count         含恐慌词的帖子数
          greed_count        含贪婪词的帖子数
          neutral_count      无关帖子数
          dominant_emotion   'panic' / 'greed' / 'neutral'
          contrarian_signal  'buy' / 'sell' / 'neutral'
          top_fear_posts     情绪最强的恐慌帖（最多 5 条）
          top_greed_posts    情绪最强的贪婪帖（最多 5 条）
          computed_at        ISO8601
          lookback_hours     实际使用的回溯窗口
        """
        posts = self._recent_texts(lookback_hours)
        fear_total = greed_total = 0
        fear_count = greed_count = neutral_count = 0
        fear_posts  = []  # (fear_score, post)
        greed_posts = []  # (greed_score, post)

        for post in posts:
            text = post["text"] or ""
            f, g = _score_text(text)
            fear_total  += f
            greed_total += g
            if f > 0:
                fear_count += 1
                fear_posts.append((f, post))
            if g > 0:
                greed_count += 1
                greed_posts.append((g, post))
            if f == 0 and g == 0:
                neutral_count += 1

        base_score = _panic_from_ratio(fear_total, greed_total)
        price_adj  = _price_momentum_adjustment(self.store)
        panic_score = min(100.0, max(0.0, base_score + price_adj))

        fear_threshold  = float(self.cfg.get("fear_threshold",  70))
        greed_threshold = float(self.cfg.get("greed_threshold", 30))

        if panic_score >= fear_threshold:
            contrarian = "buy"
            dominant   = "panic"
        elif panic_score <= greed_threshold:
            contrarian = "sell"
            dominant   = "greed"
        else:
            contrarian = "neutral"
            dominant   = "neutral"

        top_fear  = [p for _, p in sorted(fear_posts,  reverse=True, key=lambda x: x[0])[:5]]
        top_greed = [p for _, p in sorted(greed_posts, reverse=True, key=lambda x: x[0])[:5]]

        return {
            "panic_score":       round(panic_score, 1),
            "fear_total":        fear_total,
            "greed_total":       greed_total,
            "fear_count":        fear_count,
            "greed_count":       greed_count,
            "neutral_count":     neutral_count,
            "total_posts":       len(posts),
            "dominant_emotion":  dominant,
            "contrarian_signal": contrarian,
            "price_adjustment":  round(price_adj, 1),
            "top_fear_posts":    top_fear,
            "top_greed_posts":   top_greed,
            "computed_at":       dt.datetime.utcnow().isoformat(),
            "lookback_hours":    lookback_hours,
        }

    def run_llm_synthesis(self, panic_data: dict, llm_client, model: str) -> dict:
        """
        将 Panic Index 数据和样本帖子发给 Claude，获取心理解读报告。
        返回合并后的 dict（原 panic_data + llm 字段）。
        """
        sample_fear  = [p["text"][:200] for p in panic_data.get("top_fear_posts",  [])[:3]]
        sample_greed = [p["text"][:200] for p in panic_data.get("top_greed_posts", [])[:3]]

        prompt = f"""你是一位市场心理学专家，专门研究群体行为与市场情绪。

当前市场数据（过去 {panic_data['lookback_hours']} 小时）：
- Panic Index: {panic_data['panic_score']}/100（0=极度贪婪，100=极度恐慌）
- 恐慌信号帖: {panic_data['fear_count']} 条 / 贪婪信号帖: {panic_data['greed_count']} 条
- 价格动量调整: {panic_data['price_adjustment']:+.1f}
- 逆向信号: {panic_data['contrarian_signal']}

典型恐慌帖（摘录）：
{chr(10).join(f'  - {t}' for t in sample_fear) or '  （无）'}

典型贪婪帖（摘录）：
{chr(10).join(f'  - {t}' for t in sample_greed) or '  （无）'}

请用中文输出严格 JSON，格式如下（不要输出任何其他内容）：
{{
  "market_phase": "极度恐慌|恐慌|谨慎|中性|乐观|贪婪|极度贪婪",
  "crowd_psychology": "一段话描述当前群体心理状态（50字以内）",
  "key_drivers": ["驱动情绪的2-3个核心因素"],
  "contrarian_rationale": "逆向操作逻辑（30字以内）",
  "risk_warning": "当前最大风险点（30字以内）",
  "short_term_outlook": "short_term outlook in 24-48h（中文，30字以内）"
}}"""

        try:
            resp = llm_client.messages.create(
                model=model,
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.content[0].text.strip()
            # 提取 JSON（容错：LLM 有时多输出 markdown fence）
            m = re.search(r"\{[\s\S]+\}", raw)
            llm_json = json.loads(m.group(0)) if m else {}
        except Exception as e:
            print(f"[psych] LLM 解读失败: {e}")
            llm_json = {}

        result = dict(panic_data)
        result["llm_report"] = llm_json
        return result
