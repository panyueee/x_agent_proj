# GitHub 侦察笔记

最后更新：2026-06-28

---

## 第一轮（2026-06-28）

### 1. 工商/企业数据

#### [wgpsec/ENScan_GO](https://github.com/wgpsec/ENScan_GO)
- ⭐ 4,500 | Go | 最后更新 2026-03（活跃）
- **核心价值**: 聚合爱企查、天眼查、快查等多数据源，支持 HTTP API 模式
- **可借鉴点**: 爱企查有免费额度；支持多级子公司递归查询；已有 MCP Server 模式可接 Claude；Python 侧通过 subprocess/HTTP 调用
- **集成难度**: 中（Go 二进制，需自备账号 Cookie）
- **优先级**: 🔴高 — 直接替代天眼查

> 国内爬虫类项目（gsxt.gov.cn、企查查逆向）均年久失修，不建议生产使用。

---

### 2. A股免费数据源

#### [akfamily/akshare](https://github.com/akfamily/akshare)
- ⭐ 20,800 | Python | 最后更新 2026-05（每月发版）
- **核心价值**: 最广覆盖的中文开源财经数据库，A股/港股/美股/期货/加密/宏观一库
- **可借鉴点**: 与东财接口互补备份；产业链/概念板块数据可强化产业链模块；龙虎榜/北向资金东财未必覆盖；接口统一 `ak.xxx()` 几乎零改动接入
- **集成难度**: 低（pip install，无需 Token）
- **优先级**: 🔴高 — 立即接入作为东财冗余备份

#### [1nchaos/adata](https://github.com/1nchaos/adata)
- ⭐ 4,800 | Python | 最后更新 2025-04
- **核心价值**: A股轻量数据库，多源动态切换 + 自动 fallback
- **可借鉴点**: 腾讯/新浪/东财/百度多源自动切换，稳定性优先；龙虎榜封单/市场人气数据
- **集成难度**: 低
- **优先级**: 🟡中 — AKShare 的稳定性补充

> tushare（15.2k stars）：2018 年后停止开源维护，需积分门槛，不推荐替换现有接口。

---

### 3. 链上/加密聪明钱

#### [duneanalytics/dune-client](https://github.com/duneanalytics/dune-client)
- ⭐ 134（官方维护）| Python | 最后更新 2026-06
- **核心价值**: Dune Analytics 官方 Python SDK，SQL 查链上任意数据返回 DataFrame
- **可借鉴点**: 社区现成聪明钱/鲸鱼监控 SQL 直接复用；参数化查询 + 缓存免费；覆盖 EVM + Solana + Bitcoin；与 classifier.py + digest.py 管道兼容
- **集成难度**: 低（免费账号有月度 credit 上限，缓存结果免费）
- **优先级**: 🔴高 — 无需自建节点获取链上聪明钱数据

> GitHub 上 500+ stars 的链上聪明钱 Python 项目目前不存在；Bitquery GraphQL 有免费额度但无高质量开源封装。

---

### 4. 多源 LLM 信号提取架构

#### [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)
- ⭐ 89,200 | Python | 最后更新 2026-06（极活跃）
- **核心价值**: 最完整的多智能体 LLM 交易分析框架，模拟真实交易团队分工
- **可借鉴点**: 分层 analyst 架构（基本面/情绪/技术/新闻）与现有多源模块直接对应；LangGraph 编排支持 checkpoint 断点续跑；Trader→RiskManager→PortfolioManager 审核链路可参考"人工确认"机制
- **集成难度**: 中（框架较重，建议只借鉴分层设计和 prompt 模式）
- **优先级**: 🔴高 — 架构设计必读

#### [AI4Finance-Foundation/FinGPT](https://github.com/AI4Finance-Foundation/FinGPT)
- ⭐ 20,700 | Python | 最后更新 2026-04
- **核心价值**: 开源金融 LLM 微调框架，情绪分析 F1=0.903
- **可借鉴点**: prompt 模板和情绪分类数据集可直接复用于 Claude API；FinGPT-RAG 展示研报接入 LLM 的方式；长期有 GPU 时可本地部署做初筛降低 API 成本
- **集成难度**: 高（本地推理需 GPU）；短期只借鉴 prompt 设计
- **优先级**: 🟡中

---

### 5. 小红书/淘股吧爬虫

#### [NanmiCoder/MediaCrawler](https://github.com/NanmiCoder/MediaCrawler)
- ⭐ 53,800 | Python | 活跃维护
- **核心价值**: 中文多平台爬虫，覆盖小红书/抖音/B站/微博/知乎/贴吧
- **可借鉴点**: Playwright 保持登录态 + JS 签名提取，与现有实现同路线但更成熟；内置 SQLite/MySQL/CSV 多存储可对接 storage.py；支持 IP 代理池 + 多账号轮换；社区活跃，X-Sign 更新 72 小时内有 PR 修复
- **集成难度**: 低（pip install + 配置 Cookie）
- **优先级**: 🔴高 — 建议替换现有自研 Playwright 脚本

---

## 优先级矩阵

| 优先级 | 项目 | 行动 |
|--------|------|------|
| 🔴 高 | ENScan_GO | 替代天眼查，跑通爱企查免费额度 |
| 🔴 高 | AKShare | 立即接入，东财备份 + 补龙虎榜/北向资金 |
| 🔴 高 | dune-client | 接链上聪明钱，复用社区 SQL |
| 🔴 高 | TradingAgents | 借鉴分层 analyst 架构和 prompt 设计 |
| 🔴 高 | MediaCrawler | 替换小红书/淘股吧自研爬虫 |
| 🟡 中 | adata | AKShare fallback |
| 🟡 中 | FinGPT | 复用 prompt 模板，长期考虑本地部署 |

---

## AlphaGBM（第一轮，2026-06-28）

| 项目 | 结论 |
|------|------|
| AlphaGBM/skills | Unusual Activity + Market Sentiment 值得集成；期权类跳过 |
| AlphaGBM/investment-masters | 13F 持仓追踪参考价值，中国市场覆盖弱 |

---

## 待侦察方向

- [ ] 研报结构化解析（PDF → 结构化信号）
- [ ] 多平台消息去重/聚合架构
- [ ] 定时任务调度替代方案（APScheduler vs Celery vs 简单 cron）
