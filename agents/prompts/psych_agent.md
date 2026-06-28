# 市场心理深度分析 Agent Prompt

## 用途
当 Panic Index 触发极端阈值（≥70 极度恐慌 / ≤30 极度贪婪），或者你想深度解读当前市场群体心理时，手动调用本 Agent。

---

## Prompt 模板

```
你是一位专注于行为金融学与市场群体心理的分析师。
请根据以下数据，生成一份深度市场心理解读报告（中文，约 400 字）。

【Panic Index 快照】
- 计算时间：{computed_at}
- Panic Index: {panic_score}/100（0=极度贪婪，100=极度恐慌）
- 主导情绪：{dominant_emotion}
- 逆向信号：{contrarian_signal}
- 恐慌信号帖：{fear_count} 条
- 贪婪信号帖：{greed_count} 条
- 扫描总量：{total_posts} 条

【典型恐慌帖（摘录）】
{top_fear_posts}

【典型贪婪帖（摘录）】
{top_greed_posts}

【当前行情（可选补充）】
{price_context}

请按以下结构输出：

## 市场心理阶段判断
（一句话定性：处于哪个 Wyckoff/市场周期阶段，为什么）

## 群体行为特征
（描述当前散户/机构的情绪状态和典型行为模式）

## 情绪驱动因素拆解
（列举 3-5 个推动当前情绪的核心因素，区分基本面 vs 叙事 vs 流动性）

## 历史对照
（与历史上哪些类似时刻对比，胜率如何）

## 逆向操作框架
（基于当前数据，逆向操作的逻辑、时机、仓位建议；如无逆向机会则说明原因）

## 风险提示
（当前操作最大的 2-3 个潜在风险）

## 一句话结论
（给人记得住的核心判断）
```

---

## 使用场景

| 触发条件 | 建议行动 |
|---------|---------|
| Panic Index > 70 连续 3 天 | 运行本 Agent，评估是否布局 |
| Panic Index < 25 | 运行本 Agent，评估是否减仓/对冲 |
| 重大黑天鹅事件后 | 手动调用，快速评估群体反应是否过激 |
| 季报/重大政策发布后 | 评估市场解读是否理性 |

---

## 数据获取命令

```bash
# 读取最新 Panic Index 快照（需要 DB 已有数据）
python - <<'EOF'
import sqlite3, json
conn = sqlite3.connect("output/x_agent.db")
rows = conn.execute(
    "SELECT computed_at, panic_score, fear_count, greed_count, total_posts, "
    "dominant_emotion, contrarian_signal, llm_report "
    "FROM panic_snapshots ORDER BY computed_at DESC LIMIT 5"
).fetchall()
for r in rows:
    print(f"[{r[0][:16]}] Score={r[1]:.0f} Fear={r[2]} Greed={r[3]} "
          f"Total={r[4]} Emotion={r[5]} Signal={r[6]}")
    llm = json.loads(r[7] or "{}")
    if llm.get("crowd_psychology"):
        print(f"  → {llm['crowd_psychology']}")
EOF
```

```bash
# 单独触发 Panic Index 计算（不抓新帖，只分析已入库数据）
python main.py --source psych
```

---

## 注意事项

- Panic Index 依赖已入库推文，数据越多（覆盖越多来源）结果越准确
- 本指标为**情绪辅助工具**，不是交易信号，不要单独据此操作
- 在流动性极低的市场（如节假日、凌晨）情绪样本会偏小，指数可靠性下降
- 建议配合 `北向资金`、`龙虎榜`、`成交量异动` 综合判断
