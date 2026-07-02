# 人物建模（Persona）系统设计

> 目标：对追踪的首席分析师逐人建模——用其历史语料构建"分析框架 + 世界观 + 观点时间线"画像，
> 并基于画像预测其下一步观点；预测必须通过时间切分的训练/测试集评估（防未来函数）。

## 1. 数据基础

- 语料库：`output/rag.db` 的 `chunks` 表（**只读**，有入库进程持续写入，查询需容忍库增长）。
  - 连接方式一律 `sqlite3.connect("file:...?mode=ro", uri=True)`，代码层面禁止写。
- 人物 ↔ 语料映射（`x_agent/persona/corpus.py` 中 `PERSON_SOURCES` 配置）：

| 人物 | wechat feed（author 字段） | 研报 author（LIKE 匹配） |
|---|---|---|
| 张瑜 | 一瑜中的 | 张瑜 |
| 李迅雷 | 李迅雷金融与投资 | 李迅雷 |
| 罗志恒 | 粤开志恒宏观 | 罗志恒 |
| 牟一凌 | 一凌策略研究 | 牟一凌 |
| 芦哲 | — | 芦哲 |

- 日期来源：
  - `wechat`：`extra_meta.date`（ISO 8601，UTC）。
  - `research`：标题前缀 `YYYY-MM-DD `（东财研报入库时写入标题）；`extra_meta` 无日期。
  - **无法解析日期的文章一律排除**（无法证明不泄漏未来信息，宁缺毋滥）。
- 文章粒度还原：同一 `source_id` 的 chunks 按 `chunk_idx` 排序拼接；
  研报按标题分组（同一报告可能被切成多个 page-range source_id），组内按 `page_start, chunk_idx` 排序。

## 2. 时间约定（未来函数防线）

统一使用 **UTC naive datetime** 比较：

- 画像训练集：`date < until_date`（严格小于）。
- 评估测试集：`train_until <= date < train_until + test_window`（左闭右开）。
- `build_corpus(person, until_date)` 在 SQL 之后、返回之前强制过滤，是唯一取语料入口；
  `evaluate.py` 在跑评估前再 assert 一次训练语料最大日期 < train_until（双保险）。
- 该过滤逻辑有单元测试覆盖（`tests/test_persona_corpus.py`）。

## 3. 模块划分

```
x_agent/persona/
├── __init__.py
├── corpus.py     # build_corpus(person, until_date) -> [Article]（时间排序）
├── llm.py        # ClaudeLLM 薄封装（claude-sonnet-5，token 统计→成本估算；可注入 mock）
├── profile.py    # map-reduce 构建画像 → output/personas/<person>/profile_until_<date>.{json,md}
├── predict.py    # 画像 + 问题 → 结构化预测 → predictions.jsonl
└── evaluate.py   # 时间切分评估：盲预测→LLM-judge 对答案 → eval_report.md

agents/prompts/
├── persona_profile.md   # 画像提示词（## MAP / ## REDUCE 两段）
├── persona_predict.md   # 预测提示词
└── persona_judge.md     # 评审提示词（独立于预测，避免自证）

scripts/run_persona.py   # CLI
tests/test_persona_*.py  # mock LLM 单测
```

### 3.1 语料抽取 corpus.py

- `Article` dataclass：`article_id / title / date / source_type / url / text`。
- `build_corpus(person, until_date=None, db_path=..., max_chars_per_article=None)`。
- 失败容忍：单篇解析失败跳过并记 warning，不炸整体。

### 3.2 画像构建 profile.py（map-reduce）

- **成本控制**：每篇正文截断 ~2000 字；每批 10 篇 → 1 次 map 调用；语料 >100 篇只取最近 100 篇。
- MAP：每批文章 → 批级笔记（观点、逻辑链、预测、方法论线索）。
- REDUCE：全部批级笔记 → 结构化画像 JSON：
  - `analytical_framework` 分析框架与方法论
  - `worldview` 核心世界观假设
  - `timeline` 观点时间线（重大立场与转变点）
  - `explicit_predictions` 显式预测清单（内容/日期/可验证性）
  - `style` 写作与推理模式
- 落盘：`profile_until_<date>.json`（结构化）+ 同名 `.md`（人读）。
- 模型：`claude-sonnet-5`（用户指定，控制成本）。

### 3.3 预测引擎 predict.py

- 输入：画像 JSON（截止 T）+ 预测问题（具体问题或开放式"下一篇核心观点"）。
- 输出结构化预测：`{proposition, direction(看多/看空/中性/结构性…), confidence(0-1), rationale}`，
  带时间戳追加写 `output/personas/<person>/predictions.jsonl`。
- 评估用的问题从真实文章标题**去结论化**生成：中文研报标题多为"结论——主题"
  （如"出口强劲增长—6月PMI数据点评"），取破折号后的主题段作为预测题目，
  避免把结论泄漏给预测器；无破折号则用全标题（在报告中注明此局限）。

### 3.4 评估 evaluate.py

- 流程：`profile(T)` → 对 `[T, T+ΔT)` 内每篇真实文章：
  1. 由标题生成去结论化问题；
  2. 预测器（只见画像+问题）输出预测；
  3. LLM-judge（独立 prompt，见到预测 + 真实文章全文截断 3000 字）打分：
     `hit / partial / miss` + 理由。
- 输出：`eval_results.jsonl`（逐条）+ `eval_report.md`（命中率汇总 + 逐条明细）。
- 命中率口径：hit=1，partial=0.5，miss=0；同时报告严格命中率（仅 hit）。

### 3.5 CLI scripts/run_persona.py

```bash
# 构建画像
.venv/bin/python scripts/run_persona.py --person 张瑜 --build-profile --until 2026-06-19
# 单次预测
.venv/bin/python scripts/run_persona.py --person 张瑜 --predict "下月CPI公布后她对货币政策的判断方向" --until 2026-06-19
# 端到端评估
.venv/bin/python scripts/run_persona.py --person 张瑜 --evaluate --train-until 2026-06-19 --test-window 30
```

需要 `ANTHROPIC_API_KEY`（`set -a; source .env; set +a`）。

## 4. 测试策略

- `FakeLLM`（返回预置 JSON）注入所有模块，单测零 API 成本。
- 覆盖：until_date 严格过滤（含边界=当天 0 点）、chunk 拼接顺序、研报标题日期解析、
  标题去结论化、predictions.jsonl 记录字段、评估切分不重叠（训练∩测试=∅）、命中率计算。

## 5. 已知局限 / TODO

- 标题去结论化只能部分防泄漏（主题本身可能暗示方向）。
- 公众号语料仍在入库，画像基于当前快照；全量入库后应重建。
- 研报 author 是"券商·作者串"，合著报告会归入第一人物名下（LIKE 匹配）。
- judge 与预测器同用 claude-sonnet-5（不同 prompt）；预算允许时 judge 可换更强模型。
