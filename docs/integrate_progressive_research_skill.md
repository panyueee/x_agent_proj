# 集成 progressive-investment-research skill

来源：https://github.com/AlphaMao1/progressive-investment-research （MIT）
安装位置：`.claude/skills/progressive-investment-research/`（已提交）

## 它是什么

一个 Claude Code **Skill**（不是可 import 的库），把长期研究维护成可恢复、可审计的
`dossier`：一组轻量 Markdown——`Current Model`（当前怎么看/知道什么/不知道什么/哪里
冲突/下一步验证什么）、带 row_id 的证据行、P0 信号与核心假设注册表（含反证条件）、
公司观察卡（holding/watchlist/candidate + 入场纪律 + 财报监控清单）。自带 8 个专用
子 agent（魔鬼代言人/来源审计/一致性检查等）和 scaffold/校验脚本。

## 为什么和本项目互补（无重叠）

skill 的 `references/data-acquisition-layer.md` 明确写：**不重复造搜索/浏览器/PDF/RSS/
RAG，复用"已有能力"**。本项目正是那层已有能力：

| skill 需要的能力 | 本项目已有 |
| --- | --- |
| 通用发现 | web search / 各调研 agent |
| 已知 URL 正文 | ingest_wechat_rss / ingest_global_gurus 等 |
| 本地 PDF 读取 | rag.ingest_pdf（含扫描件 OCR + WAF 破解） |
| 信息流增量 | WeWe RSS 公众号管道 |
| 大类免费 API | finance_fetcher / akshare / 问财 / tvDatafeed |
| **结构化证据 + 检索** | **RAG 40 万+ 分块（研报/公众号/书籍/大牛/星球）** |

skill 提供的是我们缺的**认知层**：证据分层、口径归一、冲突检测、人类可恢复的
Current Model。两者拼起来 = 数据管道（本项目）+ 研究操作系统（skill）。

## 已落地的桥：`scripts/dossier_evidence.py`

dossier 做研究时第一步不该从 web 零开始——先问我们自己的库。该脚本把 RAG 命中转成
skill 的 `Source Card` + 候选证据行格式（review_status=raw，带 citation_anchor 可回溯）：

```bash
.venv/bin/python scripts/dossier_evidence.py "碳酸锂 价格 产能" --top-k 8
.venv/bin/python scripts/dossier_evidence.py "NVDA capex" --source-type guru -o output/dossier/_ev.md
```

无需 API key（retrieve 无向量时降级 BM25+FTS）。source_type→source_tier 已保守映射
（研报=licensed_secondary、公众号/专栏=press_source_lead、大牛=expert_interview…）。

## 与其他在建模块的接缝

- **persona 系统**：一个人就是一个 dossier 主题；persona 画像 JSON = 机器层，dossier
  Current Model = 人类可恢复层。张瑜/李迅雷可直接建人物 dossier。
- **Aladdin 批次一（securities/portfolios/positions 主表 + 风险日报）**：skill 的公司
  观察卡管定性判断与反证条件，主表管定量持仓与风险分解——卡片引用主表 row 做数字锚点。
- **signals 库**：值得长期跟踪的 P0 信号从抓取流沉淀进 skill 的
  `important-signals-assumptions.md` 注册表。

## 使用方式（下个会话生效）

skill 在会话启动时加载，本会话装的需**新开会话**才触发。届时说：
「研究一下 <行业/公司>」「继续这个 dossier」「现在这个研究对象是什么状态」即可。
dossier 默认落在项目下，promote 前可用 `scripts/validate_dossier.py` 校验。
