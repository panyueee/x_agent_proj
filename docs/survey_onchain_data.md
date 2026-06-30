# 链上聪明钱 / 鲸鱼异动 数据源调研

> 对应模块：`x_agent/dune_fetcher.py`（feature/dune 分支：「接入 Dune Analytics SDK，新增链上聪明钱/鲸鱼异动模块」）
> 调研时间：2026-06 ｜ 目标：评估现有 Dune-SDK 方案，并对比主流链上数据 API，给出取舍建议。

本项目的链上模块目标只有两个：
1. **聪明钱（Smart Money）异动**——识别历史盈利能力强的钱包在买/卖什么；
2. **鲸鱼异动（Whale Movement）**——监控大额转账 / 大户持仓变化。

调研下来，最关键的一条判断线是：**数据源是否自带「聪明钱 / 实体」标签**，还是只给原始/可查询数据、需要自己写启发式规则去定义「聪明钱」。这条线直接决定选型。

---

## 一、现有方案（Dune-SDK）做了什么

`x_agent/dune_fetcher.py` 使用官方 `dune-client` SDK，封装了一个 `DuneFetcher`，对外暴露三个方法，全部把行数据转成项目统一的 `Tweet` dataclass（`source_label="onchain"`，`group_tag="onchain"`）后入库：

| 方法 | 写死的社区查询 ID | 含义 |
|------|------------------|------|
| `fetch_smart_money()` | `2537251` | ETH 聪明钱钱包动向 |
| `fetch_whale_alerts(min_usd=1e6)` | `1329533` | 大额 ETH/USDT 转账 |
| `fetch_btc_holders()` | `3324963` | BTC 大户持仓变化 |

核心调用是 `self._client.get_latest_result(query_id)`——**读取查询的最新缓存结果，不消耗 execution credit**。

> 关于「分支版本 +287 行」：实际 main 版 154 行、feature-dune 分支版 219 行，两者逻辑高度一致。分支版只是多了 `_fmt_usd()` / `_fmt_token()`（把金额格式化成 `32.5M`/`1.2B`）以及更啰嗦的字段容错解析，并把 `min_usd` 过滤、BTC 增减持文案做得更细。没有实质性的新数据源或新查询。

---

## 二、主流方案对比

| 方案 | 数据类型 | 免费额度 | 聪明钱标签? | Python 支持 | 备注 |
|------|----------|----------|-------------|-------------|------|
| **Dune Analytics** | SQL 查询社区/自建 dashboard（聚合为主） | 2,500 credits/月，超出 $5/100 credits；`get_latest_result` 读缓存不耗 credit | ❌ 无官方标签，靠社区查询的自定义口径 | ✅ 官方 `dune-client`，成熟（Py 3.11–3.13） | 现方案。灵活但依赖他人查询、刷新即耗 credit |
| **Nansen** | 钱包/代币级实体 + Smart Money 行为标签 | 仅试用：100 credits + 每日 10 credits 回补（≈每天 2 次 Smart Money 调用） | ✅✅ 行业标杆，十余种标签（Smart Trader/LP/Fund/Smart Stablecoin…） | REST（`requests`），有 MCP/CLI；无重量级官方 Py SDK | Smart Money 端点 5 credits/次；Standard $99/月，API credit 包 $500/50万 起 |
| **Arkham Intelligence** | 实体标签 + 资金流（fund-flow）追踪 | Freemium 有免费层，Pro/Enterprise 解锁历史与高频 | ✅ 3 亿+ 标签、15 万实体页，强在「资金从哪来到哪去」 | REST（新版 Intel API，仍在 pilot），`requests` | 偏「给地址/实体打标签 + 追踪流向」，非「现成聪明钱榜单」 |
| **Flipside Crypto** | SQL 查询多链精选数据集（类 Dune） | Community 免费 500 query-seconds，可升级 Builder/Pro | ❌ 原始/聚合数据，自定义口径 | ✅ 官方 `flipside` PyPI 包，成熟 | 免费额度比 Dune 友好，适合自建聪明钱启发式 |
| **Bitquery** | GraphQL 细粒度交易/转账，40+ 链，可流式订阅 | 免费 community 层（每日有限额度） | ❌ 原始数据，自己写过滤/聚合 | GraphQL（`requests`/gql），SDK 一般 | 鲸鱼大额转账检测的最佳「自定义查询」选择；实时性强（~1s 订阅） |
| **Covalent / GoldRush** | 统一 REST：余额/转账/交易历史，100–200+ 链 | 14 天试用 25,000 credits | ❌ 余额/交易为主，无聪明钱标签 | ✅ 官方 SDK（TS/Py/Go） | 强在「一套 schema 查所有链」，适合做钱包持仓快照 |
| **Moralis** | 钱包/代币/NFT REST API | 免费 Starter，CU 有限 | ❌ | ✅ 有 SDK | 偏 dApp 后端，链上分析不如上面专业 |
| **Whale Alert** | 大额转账实时告警（turnkey） | 免费仅网页看部分最大转账；API 无免费层 | ❌（只管鲸鱼，不管聪明） | REST（`requests`），1000 calls/min | $29.95/月即得开箱即用的大额转账告警，正中「鲸鱼异动」需求 |
| **Etherscan** | 原始 txn / token transfer（单链系列） | 5 calls/s、100k calls/天；2026 免费层覆盖链收窄 | ❌ | REST（`requests`） | 几乎零成本拿原始转账，但要自己定义鲸鱼/聪明钱 |
| **The Graph** | 子图（subgraph）GraphQL 查询 | 10 万 query/月免费，超出 $2/10万 | ❌ | GraphQL | 需有对应子图；协议级数据查询好用，标签需自建 |
| **Footprint Analytics** | REST + SQL，NFT/GameFi/DeFi 跨链 | 有免费层，付费 $20/月起 | ❌ | REST/SQL | 类 Dune+Covalent，标签需自建 |
| **Allium** | 企业级链上数据仓库 | 无自助免费层（>$20K/月起谈） | 部分实体标签（企业向） | REST/数仓导出 | 机构级（a16z/Coinbase 在用），个人项目不合适 |
| **Blockscout** | 开源浏览器 REST/PRO API，多链 | PRO 免费层 100k credits/天、5 RPS | ❌ | REST，有 MCP | Etherscan 的开源替代，原始数据、无标签 |

来源见文末。

---

## 三、对现有 Dune-SDK 方案的评估

结合 `dune_fetcher.py` 的实际代码：

**优点**
- `get_latest_result` 读缓存**不耗 credit**，免费层即可长期低成本运行；
- `dune-client` 是官方成熟 SDK，与项目「接新数据源走统一 SDK」的约定一致；
- SQL/dashboard 生态极广，几乎任何聪明钱/鲸鱼口径都能在社区找到现成查询。

**缺点 / 风险**（三条核心轴）
1. **依赖社区查询，不可控**：三个查询 ID（`2537251` / `1329533` / `3324963`）都是**别人的查询**。一旦原作者改字段、改口径或删除，fetcher 会静默失效或返回错行。代码里大量 `row.get("wallet") or row.get("address") or row.get("trader")` 的字段猜测，正说明**返回 schema 本就不在我们掌控之中**。
2. **新鲜度不可靠**：`get_latest_result` 拿到的是「上次执行的缓存」，可能是几小时甚至几天前的，也可能为空。更糟的是代码把 `created_at` 兜底为 `now`（main 版 BTC 持仓直接用 `now`），**陈旧数据会被当成最新数据**呈现，掩盖了新鲜度问题。
3. **要新鲜数据就得花钱花时间**：真正拿到实时结果需要 `execute_query`（**消耗 credit + 数秒到数分钟延迟**），而免费层每月仅 2,500 credits，频繁刷新很快见底。
4. **本质短板：Dune 没有「聪明钱」官方定义**——所谓聪明钱完全取决于那条社区查询的口径，无法溯源、无法保证质量。

---

## 四、建议

**先回答一个分叉问题：你需要「厂商定义好的聪明钱标签」，还是可以「自己定义聪明钱」？**
这条线决定一切，因为调研结论很直白——**便宜又现成的聪明钱标签并不存在**，要么花钱买 Nansen，要么自己在 Dune/Flipside 上写启发式。

### 方案 A（推荐，预算约束下的默认）：保留 Dune 为骨架 + 补强，自定义聪明钱
适合本项目这种免费/低成本的个人 Agent：

1. **把三条社区查询 fork 进自己的 Dune 账户**，schema 和刷新节奏自己掌控，彻底消除「依赖别人查询」和字段猜测风险。对刷新有要求的查询用 `max_age_hours` 参数让 SDK 按需重跑（可控地花 credit）。
2. **鲸鱼异动改用更对口的源**：
   - 想要**开箱即用**的大额转账告警 → **Whale Alert（$29.95/月）**，1000 calls/min，正中需求，省去自建；
   - 想**零成本 + 自定义阈值/链** → **Bitquery 免费 GraphQL**（实时性强）或 **Etherscan/Blockscout** 原始 transfer，自己设 `min_usd` 门槛（与现有代码的过滤逻辑天然契合）。
3. **聪明钱标签自建**：在 Dune 或 **Flipside（免费额度更友好 + 官方 `flipside` SDK）** 上写「近 N 天已实现盈利 Top 钱包 → 近期买卖」的查询，作为自有口径，可溯源、可调参。

### 方案 B（需要权威标签、可付费）：引入 Nansen
如果聪明钱信号是核心卖点、且愿意付费——**Nansen 是唯一真正成体系的 Smart Money 产品**（Smart Trader/LP/Fund 等十余种标签）。注意其「免费层」实为试用（100 credits + 每天 10，≈每天 2 次 Smart Money 调用），不能当免费方案；落地需 Standard $99/月或 API credit 包。**Arkham** 作为补充，强在「实体打标签 + 资金流向追踪」，适合给鲸鱼地址加可读身份。

### 一句话结论
**短期保留 Dune（但把查询 fork 到自己账户、并正视新鲜度问题），鲸鱼异动用 Whale Alert 或 Bitquery 补强；若日后要把「聪明钱」做成可信卖点，再上 Nansen（付费）。** Python 集成层面，Dune/Flipside/Covalent 有成熟官方 SDK，Nansen/Arkham/Whale Alert 走 REST + `requests`，Bitquery 走 GraphQL。

---

## 来源

- Dune 定价：https://dune.com/pricing ；FAQ：https://docs.dune.com/learning/how-tos/pricing-faqs ；SDK：https://github.com/duneanalytics/dune-client
- Nansen API：https://nansen.ai/api ；Credits 文档：https://docs.nansen.ai/getting-started/credits ；Smart Money：https://docs.nansen.ai/api/smart-money
- Arkham：https://arkm.com/api ；新版 API 公告：https://info.arkm.com/announcements/the-new-arkham-api ；文档：https://docs.intel.arkm.com/
- Flipside：https://github.com/FlipsideCrypto/sdk ；`flipside` PyPI：https://pypi.org/project/flipside/ ；文档：https://docs.flipsidecrypto.xyz/shroomdk-sdk/get-started
- Bitquery 定价：https://bitquery.io/pricing
- Covalent / GoldRush：https://goldrush.dev/
- Whale Alert 定价：https://developer.whale-alert.io/pricing.html ；文档：https://developer.whale-alert.io/documentation/
- Etherscan 速率限制：https://docs.etherscan.io/etherscan-v2/rate-limits ；免费层变更：https://info.etherscan.com/whats-changing-in-the-free-api-tier-coverage-and-why/
- The Graph 定价：https://thegraph.com/studio-pricing/
- Footprint Analytics：https://www.footprint.network/data-api ；文档：https://docs.footprint.network/docs/get-started
- Allium：https://www.allium.so/
- Blockscout PRO API：https://www.blog.blockscout.com/going-pro-api/
- 综述参考：https://medium.com/coinmonks/5-best-onchain-data-apis-for-developers-in-2026-1cf68e1c4920
