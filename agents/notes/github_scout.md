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

---

## 第三轮（2026-06-28）— 阿拉丁系统类似项目

### [dcajasn/Riskfolio-Lib](https://github.com/dcajasn/Riskfolio-Lib)
- ⭐ 3.7k | Python | 活跃
- **核心价值**: 最接近 Aladdin 风险引擎的开源实现；VaR、CVaR、最大回撤、因子暴露一体
- **可借鉴点**: 多风险度量约束组合优化；与 CVXPY 集成；可吃进信号分数作为"观点"
- **集成难度**: 中
- **优先级**: 🟡中 — 作为 PyPortfolioOpt 的升级路径，数据足够后引入

### [PyPortfolio/PyPortfolioOpt](https://github.com/PyPortfolio/PyPortfolioOpt)
- ⭐ 5.3k | Python | 活跃
- **核心价值**: 行业标准组合优化库；均值方差、Black-Litterman、HRP 三种路线
- **可借鉴点**: 把 classifier.py 打出的信号分数作为 Black-Litterman "观点" 输入，输出仓位权重建议；pip install 零成本接入
- **集成难度**: 低
- **优先级**: 🔴高 — 立即接入，信号→仓位权重的桥梁

### [goldmansachs/gs-quant](https://github.com/goldmansachs/gs-quant)
- ⭐ 10.9k | Python | 活跃
- **核心价值**: 高盛量化团队开源，OMS/EMS 框架 + 衍生品定价 + 多资产风险管理
- **可借鉴点**: 参考其数据模型和分层架构设计；部分 API 需高盛后台，只借鉴代码结构
- **集成难度**: 高
- **优先级**: 🟢低 — 长期架构参考，暂不集成

### [skfolio/skfolio](https://github.com/skfolio/skfolio)
- ⭐ 2k+ | Python | 活跃
- **核心价值**: scikit-learn 风格的现代组合优化库，支持 shrinkage、压力测试、分层聚类
- **可借鉴点**: 与 ML pipeline 无缝集成；scikit-learn 接口降低学习成本
- **集成难度**: 低
- **优先级**: 🟡中 — PyPortfolioOpt 的替代/补充

### [stefan-jansen/alphalens-reloaded](https://github.com/stefan-jansen/alphalens-reloaded)
- ⭐ 591 | Python | 活跃
- **核心价值**: 因子性能评估框架；IC/IR 分析，量化 alpha 信号预测力
- **可借鉴点**: 把 classifier.py 信号分数作为 alpha 因子跑 IC 分析，验证哪类关键词真正有前瞻性
- **集成难度**: 低
- **优先级**: 🔴高 — 作为 scripts/ 分析工具，评估信号质量

### [0xfdf/toraniko](https://github.com/0xfdf/toraniko)
- ⭐ 新项目 | Python | 活跃
- **核心价值**: 生产级多因子股票风险模型（类 Barra/Axioma）；M1 Mac 跑 10 年数据 < 1 分钟
- **可借鉴点**: A 股多因子风险模型；市场/行业/风格因子分解；因子协方差矩阵构建
- **集成难度**: 中
- **优先级**: 🟡中 — A 股多因子研究时引入

### [stefan-jansen/zipline-reloaded](https://github.com/stefan-jansen/zipline-reloaded)
- ⭐ 1.7k | Python | 活跃
- **核心价值**: 事件驱动回测框架（Quantopian 开源遗产的活跃维护版）
- **可借鉴点**: 信号→回测→绩效评估完整链路；与 pyfolio-reloaded 配套
- **集成难度**: 中
- **优先级**: 🟡中 — 信号有效性验证的回测基础

---

## 阿拉丁类项目优先级矩阵

| 优先级 | 项目 | 行动 |
|--------|------|------|
| 🔴 高 | PyPortfolioOpt | 信号分数 → 仓位权重，接入 portfolio_optimizer.py |
| 🔴 高 | alphalens-reloaded | scripts/analyze_signals.py 评估信号 IC/IR |
| 🟡 中 | Riskfolio-Lib | PyPortfolioOpt 升级路径，加 VaR 约束 |
| 🟡 中 | toraniko | A 股多因子风险模型 |
| 🟡 中 | zipline-reloaded | 信号回测验证 |
| 🟢 低 | gs-quant | 仅架构参考 |

---

## 第二轮（2026-06-28）— 市场情绪/恐慌分析

### [hugo2046/QuantsPlaybook](https://github.com/hugo2046/QuantsPlaybook)
- ⭐ 5.4k | Jupyter Notebook | 活跃
- **核心价值**: 复现100+券商金工研报，含「投资者情绪指数择时模型」（国信证券）
- **可借鉴点**: A股情绪因子体系（北向资金+换手率+融资余额）；「物极必反·龙虎榜机构模型」反向信号实现
- **优先级**: 🔴高 — A股情绪因子权重配置直接可用

### [wwwxmu/Dataset-of-financial-news-sentiment-classification](https://github.com/wwwxmu/Dataset-of-financial-news-sentiment-classification)
- ⭐ 83 | 数据集
- **核心价值**: 17,149条中文财经新闻情绪标注数据集（含雪球数据）
- **可借鉴点**: 4,635条负面新闻可作为恐慌情绪基础训练集；可用于fine-tune中文模型
- **优先级**: 🟡中 — 有GPU时用于训练分类器

### [Paulescu/crypto-sentiment-with-llms](https://github.com/Paulescu/crypto-sentiment-with-llms)
- ⭐ 21 | Python
- **核心价值**: 用LLM将新闻标题转为结构化市场信号（bullish/neutral/bearish + 推理）
- **可借鉴点**: 强制JSON输出提示词设计；与我们的use_llm路径完全一致
- **优先级**: 🔴高 — 直接参考Prompt设计

### [dang-trung/crypto-sentiment-index](https://github.com/dang-trung/crypto-sentiment-index)
- ⭐ 21 | Python
- **核心价值**: PCA聚合9个子指标为单一情绪主成分
- **可借鉴点**: 避免人工设定权重；多信号PCA聚合框架
- **优先级**: 🟡中 — 数据量足够后引入PCA替代等权平均

### [hackingthemarkets/sentiment-fear-and-greed](https://github.com/hackingthemarkets/sentiment-fear-and-greed)
- ⭐ 133 | Python
- **核心价值**: 10年历史Fear&Greed数据集 + Backtrader反向信号回测
- **可借鉴点**: 历史数据集验证阈值（极恐<20买入/极贪>80减仓）；反向信号胜率量化
- **优先级**: 🟡中 — 用于校准Panic Index触发阈值

## 推荐 Panic Index 算法（三层架构）

| 层 | 内容 | 借鉴来源 |
|----|------|---------|
| 第一层 | 多因子子指标：文本情绪+RSI+帖量异动+成交量 | SurfSolana + QuantsPlaybook |
| 第二层 | 等权平均（早期）→ PCA聚合（数据足够后） | dang-trung |
| 第三层 | LLM生成心理解读，输出JSON含panic_score+contrarian_signal | Paulescu |

反向信号：Panic Index > 75 触发买入预警，< 20 触发减仓预警，连续3日 > 70 发送推送
