# Aladdin 竞品对照深挖

> 调研日期：2026-07-02 ｜ 方式：纯公开网络资料（厂商官网/新闻稿、SEC 文件、行业媒体、咨询机构页面）
> 本文是 `docs/survey_aladdin.md` 的姊妹篇：初版只有一行竞品清单（State Street Alpha、SimCorp+Axioma、Bloomberg AIM/PORT、MSCI RiskMetrics/Barra），本文逐家展开。
>
> 可信度标注约定：**[高]** = 官方新闻稿/SEC 文件/政府养老金公开文档；**[中]** = 主流行业媒体（WatersTechnology、Funds Europe、P&I 等）或厂商案例页；**[低]** = 单一二手来源、竞品营销页或聚合站，仅供参考。
> **定价类信息一律单独标注来源**；查不到的直接写"未查到"，不编数。

---

## 0. 一页结论（TL;DR）

- Aladdin 没有"一个"对手，它面对的是**三类不同的战争**：
  1. **front-to-back 全平台战**：State Street Alpha（CRD）、SimCorp One、Bloomberg（AIM+PORT+MARS 组合拳）——这是唯一在"整个平台"层面与 Aladdin 正面对打的阵营；
  2. **风险/因子分析件战**：MSCI（RiskMetrics/BarraOne/Barra）、Axioma（现 SimCorp 旗下）、FactSet——客户往往在 Aladdin 之外**再买一套**做独立验证，所以是"共存型竞争"；
  3. **细分蚕食战**：Clearwater（保险会计）、Enfusion（对冲基金）、Allvue/eFront 对位（私募）、Amundi ALTO（欧洲主权数据叙事）——从 Aladdin 打不深的缝隙切入。
- **规模对比**（平台上资产口径，非严格可比）：Aladdin ~$21-25T / SimCorp One 宣称 $35T / Bloomberg AIM $17T / State Street Alpha 21+ 个 live mandate。收入口径 Aladdin（BlackRock Technology Services ~$1.6B，ACV 逼近 $2B）大约等于 SimCorp（>€700M）+ MSCI Analytics（$675M）+ CRD（front office ARR $356M）三家之和。
- **公开定价挖掘结果**：Aladdin 无公开价目表；最硬的数字是 JP Morgan 案例（接入费 $1.5M，年费涨至 $5.3M，二手转述 [低/中]）与"每客户平均 ACV ≈ $1.5-2M"（用分部收入÷客户数倒推 [中]）。Bloomberg Terminal 是全场唯一"准公开定价"产品（$31,980/年/席位，2025 续约价 [中]）。政府采购文件里能确认 CalSTRS、NEST（英国最大 DC 养老金）**用** Aladdin [高]，但**合同金额未随文件公开**；LACERA 则公开**拒绝**了 Aladdin（"groupthink"理由）[中]。
- **功能矩阵结论**：Aladdin 唯一被所有第三方一致认可的短板集群是——**贵、实施重（12-24 个月）、黑盒、利益冲突顾虑（你在用最大竞争对手的系统）、后台/会计与账务不如 SimCorp、纯股票量化因子生态不如 Barra/Axioma 开放**。

---

## 1. 竞争格局总览

### 1.1 竞品分类地图

| 阵营 | 玩家 | 与 Aladdin 的关系 |
|---|---|---|
| front-to-back 全平台 | State Street Alpha（CRD）、SimCorp One、Bloomberg 买方套件 | 直接替代，抢同一张合同 |
| 风险分析/因子模型 | MSCI（BarraOne/RiskMetrics）、Axioma、FactSet（Cognity）、Bloomberg PORT/MARS | 可替代 Aladdin Risk 单模块，也常作"第二意见"共存 |
| 固收定价库 | Yield Book（LSEG）、Numerix（+FINCAD/PolyPaths）、RiskVal | 组件级，不打平台战 |
| 会计/IBOR/中后台 | Clearwater Analytics、BNY Eagle、SimCorp（此项亦强） | 蚕食 Aladdin 的中后台延伸 |
| 私募/另类 | Allvue、eFront（已被 BlackRock 收编）+ Preqin（同） | Aladdin 靠收购把该战场大半买下了 |
| 云原生新贵 | Enfusion（已并入 Clearwater）、Limina、Two Sigma Venn | 打中小客户与"反黑盒"叙事 |
| 资管自研外卖 | Amundi ALTO、Goldman GS（历史上有 SecDB 外卖尝试） | 复制 BlackRock"自用→外卖"路线的欧洲版 |

### 1.2 头部规模速览（各家自报口径，不严格可比）

| 平台 | 平台资产 | 客户数 | 收入口径 | 来源可信度 |
|---|---|---|---|---|
| BlackRock Aladdin | ~$21-25T | 1000+ 机构（FT 口径 240 家大客户中 1/3 即承载 $21T） | Technology Services ~$1.6B（2024），ACV 逼近 $2B | [高/中] |
| SimCorp One（含 Axioma） | 宣称 $35T | Dimension 190+ 客户 / 合并后宣称 300+ 机构 | 合并 >€700M 毛收入，员工 2800+ | [高]（官方新闻稿） |
| Bloomberg AIM | $17T（AIM 管理资产） | 900+ 家公司、~15000 用户；PORT Enterprise 750+ 客户 | 不单独披露（Bloomberg LP 非上市） | [高]（官方页） |
| State Street Alpha | 未披露总额 | 21 个 live mandate（2024Q1 口径）持续增加 | front office 软件与数据 ARR $356M（3Q24） | [高]（财报/Global Custodian） |
| MSCI Analytics | n/a（分析件） | Barra 模型被 1200+ 机构使用 | Analytics 分部 $675M（2024），EBITDA 利润率近 50% | [高]（10-K） |
| FactSet | n/a | 8000+ 客户（全司口径） | 全司 $2.32B（FY2025），ASV $2.4B | [高]（财报） |
| Clearwater+Enfusion | $7.3T+（Clearwater 报告资产口径，[中]） | 1300+（Clearwater）+ 800+（Enfusion） | 合并 ~$750M（2025 预计） | [高]（8-K/新闻稿） |
| Amundi ALTO | €8T | 80+ 客户 / 15 国 | 外部收入 run-rate ~€100M（2024） | [中]（Investment Officer/Markets Media） |

---

## 2. Bloomberg：PORT / PORT Enterprise + AIM + MARS

### 2.1 定位与历史

Bloomberg 不是以"一个平台"迎战，而是用 **Terminal 生态 + 三件套**围堵：

- **PORT**：Terminal 内置的组合与风险分析（持仓、绩效归因、风险、特征分析），对已付席位费的用户"边际免费"——这是它最锋利的商业武器：中小资管觉得"我都花了 3 万美元买 Terminal 了，PORT 够用就不必再买 Aladdin Risk"。
- **PORT Enterprise**：付费升级版，750+ 客户，批量报告、企业级定制、更深的风险与归因模型；2025 年在 PORT Enterprise 里上线 **AI Portfolio Commentary**（生成式 AI 自动写组合点评）[高]。
- **AIM**（Asset & Investment Manager）：买方 OMS，**900+ 客户公司、近 15,000 用户、管理 $17T 资产** [高]（官方页）。多资产全生命周期：决策支持、盘前合规、订单建模与再平衡。血统上是 Terminal 的买方下单延伸，强在与 Terminal 数据/通讯（IB 聊天）无缝，弱在中后台深度。
- **MARS**（Multi-Asset Risk System）：卖方血统的风险与估值系统（衍生品定价、对手方风险、抵押品、SIMM），覆盖股、汇、固收、通胀、信用、按揭及上市/OTC 衍生品 [高]。买方客户常用 MARS 补 Aladdin/其他 OMS 的衍生品估值短板。

关键历史节点：**2015-12 Bloomberg 以约 $780M 收购 Barclays Risk Analytics and Index Solutions（BRAIS）**，拿到原雷曼（Lehman）固收基准指数与 **POINT** 组合分析工具的知识产权；POINT 在交割后仅继续运营 18 个月（至 2018 年初）即关停，功能并入 PORT [高]。

### 2.2 与 Aladdin 的正面交锋

- **POINT 关停迁移战（2016-2018）**：POINT 曾是固收组合分析的行业标配之一，关停迫使数百家固收资管重新选型——Bloomberg 想把客户导入 PORT，而 Aladdin 与 Yield Book（Citi/后 LSEG）是最大的截流者。Western Asset 当时专门发白皮书《Fixed-Income Managers Miss the POINT》讨论迁移 [中]。这是近十年固收分析领域最大的一次"客户重新洗牌"，Aladdin 是净受益者之一（行业共识，无公开份额数据）。
- **席位经济学之战**：Aladdin 卖企业级合同（百万美元级/年），Bloomberg 卖席位（$31,980/年/席，2+ 席 $28,320 [中]，见 §10）。对 50 人以下投资团队，Bloomberg 组合（Terminal+AIM+PORT）总拥有成本通常低于 Aladdin 一个数量级的入门门槛——这是 Aladdin 在中小客户段最常输掉的原因（行业共识 [中]）。
- Chartis RiskTech100：Bloomberg 连续三年进 top-10（2026 年榜第 9）[高]。

### 2.3 目标客户与商业模式

- Terminal 用户基数（~325k 席位，公开常识 [中]）是漏斗顶；AIM/PORT Enterprise/MARS 是增购项。
- 目标客户：从 2 人家族办公室到大型资管都有，**甜蜜区是已重度依赖 Terminal 的中型多资产/固收资管**。
- 弱点：无真正的会计/IBOR 后台（front-to-back 拼不齐）；AIM 的合规与组合建模深度长期被认为弱于 CRD/Aladdin（行业评论 [低/中]，如 fi-desk 对固收 OMS 的比较讨论）。

---

## 3. MSCI：RiskMetrics + BarraOne / Barra 因子模型

### 3.1 历史谱系（两条血脉汇合）

| 年份 | 事件 | 可信度 |
|---|---|---|
| 1975 | Barr Rosenberg（伯克利教授）创立 BARRA（Barr Rosenberg Associates），把多因子风险模型商业化——学术源头是 Rosenberg & Marathe (1975) 对 CAPM 的检验 | [高] |
| 1994 | JP Morgan 公开发布 RiskMetrics 方法论（VaR 方差-协方差法），免费公开技术文档成为行业标准 | [高] |
| 1998 | RiskMetrics Group 从 JP Morgan 分拆独立 | [高] |
| 2004 | Barra 与 Morgan Stanley Capital International 合并成 MSCI Barra（交易额约 $816M，[中]） | [中] |
| 2007 | RiskMetrics 收购 ISS（代理投票/治理），同年 IPO | [高] |
| 2010 | **MSCI 以 ~$1.55B（现金+股票）收购 RiskMetrics**；合并后公司年收入约 $750M、2000 名员工 | [高]（SEC Form 425） |
| 2011→ | Barra 股票模型代际：USE4（2011，引入 Country 因子、特征值调整协方差）→ GEM2/GEM3 → **GEMLT**（全球长线模型，首次引入 Systematic Equity Strategies 因子） | [高]（MSCI 方法论文档） |

### 3.2 产品与功能范围

- **BarraOne**：多资产组合风险平台——Barra 长线因子模型 + VaR + 全重估压力测试，覆盖公开/私募资产与衍生品 [高]。
- **RiskMetrics**：市场风险/VaR 引擎（现多以 RiskManager 服务形态存在），保险与银行资管条线用户多。
- **Barra 模型授权**：不买平台也能单买模型数据（因子暴露、协方差矩阵），被 1200+ 机构使用 [高]——这是与 Aladdin 最根本的商业模式差异：**MSCI 卖"风险的度量衡"，Aladdin 卖"装着度量衡的整个车间"**。
- 无 OMS、无会计、无 IBOR：MSCI 从不打 front-to-back 战。

### 3.3 与 Aladdin 的交锋形态

- **共存多于替代**：大量 Aladdin 客户同时订阅 Barra 模型做股票组合构建（Aladdin 风险因子体系偏固收血统，股票量化圈的"通用语言"是 Barra/Axioma 因子，学界与卖方研报引用的也是 Barra 口径）。行业共识 [中]。
- 替代场景主要在"只要风险分析、不要平台"的资产所有者（养老金/保险）：BarraOne vs Aladdin Risk 单模块竞标。LACERA 拒绝 Aladdin 的案例（§11.2）里，其风险分析需求即由其他供应商满足 [中]。
- 收入体量对比：MSCI Analytics $675M（2024）[高] vs Aladdin ~$1.6B——Aladdin 单分部约等于 2.4 个 MSCI Analytics。

---

## 4. SimCorp Dimension / SimCorp One（+ Axioma）

### 4.1 定位与历史

- 1971 年创立于哥本哈根（Simulation & Corporation Planning），旗舰产品 **SimCorp Dimension**：欧洲血统的一体化投资管理系统，**190+ 客户** [高]。
- **2023-09 被德意志交易所（Deutsche Börse）以 €3.9B 收购**并退市；随后与德交所 Qontigo 旗下的 **Axioma 合并为一家公司**（Axioma 自 2021 年起已是 SimCorp 战略伙伴）[高]。
- 合并后主推 **SimCorp One**：Dimension（IBOR/会计/中后台）+ Axioma（因子风险/组合构建）+ 数据管理的整合平台；宣称覆盖 **$35T 资产、300+ 机构、2800+ 员工、合并毛收入 >€700M** [高]（官方新闻稿/维基）。
- 2024 年起推 **SimCorp Alternatives**（私募市场模块，德交所新闻稿）——对标 Aladdin 的 eFront 整合。

### 4.2 功能范围与真实强项

- **IBOR 是其教科书级强项**：SimCorp 的统一数据模型以 Investment Book of Record 为地基，实时集中并分发所有资产类别的持仓/日内活动——业内讨论 IBOR 概念时经常直接拿 SimCorp 当参照实现（Cutter Associates 的 IBOR 专题亦然）[中]。
- **会计（ABOR）与多辖区合规**：多会计准则并行（IFRS/本地 GAAP/法定报表）是欧洲保险与养老金选它的核心理由；"保险公司和养老金特别爱 SimCorp"（Limina 比较页转述，[低/中]）。
- 合并 Axioma 后补上了前台短板：可自定义因子的风险模型 + 组合优化器（见 §7）。
- 弱点（第三方比较页归纳 [低/中]）：混合架构仍带 legacy 组件、升级周期慢、集成改动常需厂商咨询介入；实施 12-24 个月与 Aladdin 同级。

### 4.3 与 Aladdin 的正面交锋

- **"欧洲阵营"叙事**：德交所收购后，SimCorp One 被明确包装为欧洲监管者与资产所有者可信赖的"非美"选项；叠加欧洲数据主权情绪（与 Amundi ALTO 同一叙事带）[中]（Datos Insights 有专文《Does Deutsche Börse Have the Pieces to Build a Buy-Side Tech Powerhouse?》）。
- 典型对垒画像：**欧陆保险/养老金选型**——SimCorp 赢在会计与法定报表深度，Aladdin 赢在风险分析与全球网络。双方均无公开的逐单胜负披露；能确认的方向性事实是 Aladdin 拿下了 Nordea（2017 年前后，北欧最大资管之一 [中]）、Santander AM [中]（Funds Europe），而 SimCorp 在北欧/德语区基本盘极稳（190+ 客户中欧洲占绝对多数 [中]）。
- 收入对比：SimCorp 整体 >€700M vs Aladdin ~$1.6B——体量约为 Aladdin 的一半，是收入口径上最接近的独立竞争者。

---

## 5. State Street Alpha（+ Charles River Development）

### 5.1 定位与历史

- **2018-07 State Street 宣布以 $2.6B 全现金收购 Charles River Development（CRD）**，2018Q4 交割 [高]（State Street 官方新闻稿）。CRD 是 1984 年创立的买方前台软件商，OMS/盘前合规/组合建模血统深厚（合规引擎长期被视为业界标杆之一 [中]）。
- 在 CRD 之上叠加 State Street 自家托管、中台外包、数据（Alpha Data Platform，底层用 Snowflake 等）能力，2019 年推出 **State Street Alpha**——**第一个由托管行发起的 front-to-back 平台**，直接对标 Aladdin [高/中]（Institutional Investor：《State Street Ramps Up Competition With BlackRock's Aladdin》）。

### 5.2 托管行打法（与 Aladdin 的模式差异）

- 商业逻辑：**用软件锁托管**。Alpha 合同通常捆绑托管+中台外包+前台软件，State Street 赚的是全链条服务费，软件可以让利——这与 Aladdin"纯技术订阅"的定价逻辑不同，对想"一张合同外包全部运营"的资产所有者格外有吸引力 [中]。
- 进展数据：2024Q1 达到 **21 个 live mandate**；front office 软件与数据 **ARR $356M（3Q24，+19% YoY）**；2025Q2 front office 软件与数据收入 +27% YoY（主要来自 CRD 财富客户续约）[高]（财报/Global Custodian）。

### 5.3 与 Aladdin 的正面交锋案例

- **Invesco 签约 Alpha**（front-to-back 迁移，Funds Europe [中]）——Alpha 早期标志性大单；Invesco 这类体量的资管本是 Aladdin 的典型目标客户画像。
- CRD 存量客户基盘（数百家买方，含大量本可能被 Aladdin 蚕食的前台单模块客户）构成防御纵深 [中]。
- 人才层面的贴身肉搏：Global Custodian 报道 State Street 的亚太 Alpha 负责人被 BlackRock Aladdin 挖走 [中]——双方在同一批客户与同一批销售身上竞争。
- 未查到"从 Aladdin 手中整体撬走客户"的公开实名案例；front-to-back 选型极少公开披露落选方，此处如实说明。

### 5.4 弱点

- 平台整合复杂度：Alpha = CRD + 托管 + 外包中台 + 数据平台的"缝合"，交付一致性长期被咨询圈点名观察（Citisoft 等年度 vendor watch [低/中]）。
- 利益结构不同但同样有"锁定"问题：把前中后台全交给托管行，换供应商的成本比单换软件更高。

---

## 6. FactSet 与 LSEG/Refinitiv

### 6.1 FactSet：工作站+组合分析（PA）路线

- 定位：数据工作站 + 分析套件，不打 front-to-back 战。全司收入 **$2.32B（FY2025，+5.4%）、ASV $2.4B** [高]（财报）。
- **Portfolio Analytics（PA）**：FactSet 工作站里最成功的买方模块之一，绩效归因/组合对比/客户报告，中型资管与投顾渗透率高。
- **2017-03 以 $205.2M 收购 BISAM**，获得 **B-One**（跨资产绩效衡量，与 PA 互补）与 **Cognity**（多资产市场风险、衍生品风险与量化组合构建；BISAM 2016 年收入 >$28M）[高]（FactSet 新闻稿/SEC）。
- 此外历史上还收购了 Portware（EMS，2015）——所以 FactSet 有 EMS 但无完整 OMS/合规/会计。
- 与 Aladdin 关系：**共存为主**。典型形态是"Aladdin 做风险与交易，FactSet 做研究/归因/客户报告"。在中型资管的组合分析单项选型里，PA 是 Aladdin Risk 的低成本替代 [中]。

### 6.2 LSEG/Refinitiv：数据+固收分析件

- **Workspace/Eikon**：Terminal 的直接对手（数据终端层面），组合分析能力弱于 PORT/PA，买方渗透靠价格（席位价约为 Bloomberg 的 1/2-2/3，公开常识 [低/中]，无官方价目表）。
- **Yield Book**：1989 年诞生于所罗门兄弟的固收分析引擎（按揭/政府债/公司债/衍生品定价与情景分析），1998 年归 Citi，**2017 年 LSEG（FTSE Russell）以 $685M 现金从 Citi 购得**（连同 Citi 固收指数）[高]。固收组合分析上与 Aladdin 的固收引擎、Bloomberg PORT 三足鼎立（POINT 关停后行业格局，[中]）。
- LSEG 无买方 OMS/会计野心，主战场是数据与指数；对 Aladdin 是**组件级竞争 + 数据供应商**双重角色。

---

## 7. Axioma（现 SimCorp 旗下）：开放因子模型路线

### 7.1 历史

- 1998 年由 Sebastian Ceria（哥伦比亚商学院教授，优化算法背景）创立；2019-04 **Deutsche Börse 以 ~$850M 收购**（General Atlantic 注资 $715M 协助），与 STOXX/DAX 指数业务合并为 **Qontigo**，Ceria 任 CEO [高]（P&I/Cravath/PRNewswire）。2023 年 Qontigo 分析业务（Axioma）并入 SimCorp（§4）。

### 7.2 与 Barra 的方法论差异（两大因子体系之争）

| 维度 | Barra（MSCI） | Axioma | 来源 |
|---|---|---|---|
| 因子体系 | 预设基本面因子（风格/行业/国家），几十年行业标准 | 基本面模型 + **统计模型（PCA）双轨**并行出货 | [中]（Medium 比较文/FactSet Insight） |
| 回归方法 | 加权横截面回归 | WLS（按市值平方根加权）+ **Huber M 稳健回归**抗离群点 | [中] |
| 更新频率 | 传统上月度为主（新模型有短线版本） | 日度更新起家，短线模型是卖点 | [中] |
| **自定义因子** | 封闭为主（模型即产品） | **开放：客户可在 Axioma Risk Model Machine 中自建/混入自有因子重估整个模型** | [中]（厂商材料，行业共识） |
| 与优化器耦合 | 需配 Barra Optimizer | Axioma Portfolio Optimizer 与风险模型同源，量化基金常单独买优化器 | [中] |

- **对量化自营/对冲基金**：Axioma 的"可定制因子 + 稳健回归 + 日度更新"使它成为 Barra 之外的标准第二选择；很多 alpha 团队用 Axioma 定制模型、对外报告用 Barra 口径（行业惯例 [低/中]）。
- **对 Aladdin**：Axioma 单卖时是 Aladdin Risk 的组件级替代；并入 SimCorp 后变成平台战武器——SimCorp One 用 Axioma 补齐了此前对 Aladdin 最明显的前台风险短板。

---

## 8. 简要带过的其他玩家

| 玩家 | 一句话定位 | 关键事实 | 与 Aladdin 关系 |
|---|---|---|---|
| **Clearwater Analytics** | 云原生投资会计/报告 SaaS（保险资金起家，Boise 出身） | **2025-01 宣布以 $1.5B 收购 Enfusion**（4 月完成），加上 Beacon/Bistro，2025 合并收入预计 ~$750M [高]（新闻稿/8-K） | 从"会计+报告"切入向前台延伸，宣称要做"第一个云原生 front-to-back"——瞄准的正是 Aladdin 最重的软肋（实施重、非云原生出身） |
| **Enfusion** | 云原生对冲基金 PMS/OMS/IBOR 一体（2006 创立，芝加哥） | 被 Clearwater 收购前 ~800 客户，年收入 ~$200M [中] | 服务 Aladdin 不屑于下沉的中小对冲基金段 |
| **BNY Eagle**（Eagle Investment Systems） | 数据管理+投资会计+绩效衡量（1989 创立，2001 起属 BNY Mellon） | 托管行旗下软件，现融入 BNY 数据与平台条线 [中]（维基/Tracxn） | 中后台组件级竞争；BNY 同时是 Aladdin Provider 模式的合作托管方之一——竞合关系 |
| **Allvue Systems** | 私募信贷/PE 前中后台（2019 年由 AltaReturn + Black Mountain 合并，Vista Equity 旗下 [中]） | 6sense 口径：eFront 类别市占 ~8.8%、Allvue ~1.1%（网站技术侦测口径，仅供参考 [低]） | 对标 eFront（现 BlackRock 旗下）——即在私募管理软件上与 Aladdin 家族直接竞争 |
| **Numerix** | 跨资产衍生品定价/风险库 | 2022 被 Genstar 收购；2023 连购 FINCAD、PolyPaths（结构化/固收分析）[高]（新闻稿） | 组件级：卖定价引擎给买方/卖方系统（包括 Aladdin 的竞品们）嵌入 |
| **RiskVal** | 固收相对价值分析（利率交易台工具，纽约） | 私人公司，规模未披露 [低] | 交易台级工具，不构成平台竞争 |
| **Amundi ALTO** | 欧洲版"自用→外卖"：Amundi 的 PMS SaaS（2017 年起外卖） | **80+ 客户/15 国、€8T 资产、外部收入 run-rate ~€100M（2024）**，目标 2028 收入翻倍 [中]（Investment Officer/Markets Media）；被媒体直接称为"European spell on Aladdin" | 唯一复制了 Aladdin"大资管自用背书"模式的欧洲玩家；主打欧洲数据治理叙事，规模 ≈ Aladdin 的 1/16 |
| **Two Sigma Venn** | 因子透镜风格的轻量组合分析（2019 上线） | 面向资产所有者的低门槛 SaaS [中]（维基/媒体） | 蚕食"只想要个风险透视、不想买平台"的长尾 |

---

## 9. 功能矩阵总表

打分：✓ = 该维度是其公认强项/完整能力；部分 = 有但深度/覆盖有限；✗ = 无或可忽略。
依据：厂商官方产品页与新闻稿 [高]、行业媒体与咨询比较 [中]、竞品比较页 [低]。**这是定性汇总，不是测评实测。**

| 能力维度 | Aladdin | Bloomberg (AIM+PORT+MARS) | MSCI (BarraOne/RM) | SimCorp One (+Axioma) | SS Alpha (CRD) | FactSet | Axioma(单看) | Clearwater(+Enfusion) |
|---|---|---|---|---|---|---|---|---|
| 多资产覆盖 | ✓ | ✓ | ✓（分析口径） | ✓ | ✓ | 部分（衍生品浅） | 部分（股票为主+多资产风险） | 部分 |
| 固收深度（OAS/按揭/结构化） | ✓（起家本行） | ✓（MARS+原雷曼指数血统） | 部分 | 部分 | 部分 | 部分 | ✗ | 部分（会计层面强） |
| 股票因子模型 | 部分（自有因子，非行业口径） | 部分（PORT 风险模型） | ✓（Barra=行业标准） | ✓（Axioma） | ✗（外接） | 部分（可接 Barra/Axioma 模型） | ✓（可自定义因子） | ✗ |
| OMS/EMS | ✓ | ✓（AIM；EMS 靠 EMSX/TSOX） | ✗ | ✓（Dimension 前台） | ✓（CRD=合规/OMS 血统） | 部分（Portware EMS，无 OMS） | ✗ | ✓（Enfusion，HF 段） |
| 盘前合规 | ✓ | ✓ | ✗ | ✓ | ✓（业界标杆之一） | ✗ | ✗ | 部分 |
| IBOR/会计（ABOR） | 部分（IBOR 强；基金会计多靠 Provider 伙伴） | ✗ | ✗ | ✓（**最强项**，多准则并行） | ✓（托管行原生） | ✗ | ✗ | ✓（会计起家） |
| 私募/另类 | ✓（eFront+Preqin） | 部分 | 部分（私募风险穿透） | 部分（SimCorp Alternatives，新） | 部分 | 部分（Cobalt） | ✗ | 部分 |
| 气候/ESG 分析 | ✓（Aladdin Climate，证券级） | 部分 | ✓（MSCI ESG 是独立强业务） | 部分（EBOR 概念） | 部分 | 部分 | 部分 | 部分 |
| 数据云/API 开放度 | ✓（Studio/Data Cloud/Snowflake/开源 SDK） | 部分（B-PIPE/SAPI 收费墙厚） | 部分 | 部分（SaaS 化进行中） | ✓（Alpha Data Platform+Snowflake） | ✓（Cornerstone/API 丰富） | ✓（模型可导出定制） | ✓（云原生） |
| AI 助手 | ✓（Copilot，2023） | ✓（PORT AI 点评 2025 + BloombergGPT） | 部分（AI@MSCI 起步） | 部分 | 部分 | ✓（FactSet Mercury/Transcript AI） | ✗ | 部分 |
| 实施轻重（轻=好） | ✗（12-24 个月 [中]） | 部分（模块化、随 Terminal 装） | ✓（分析件接入快） | ✗（12-24 个月 [中]） | ✗（front-to-back 更重） | ✓ | ✓ | ✓（SaaS） |

**矩阵读法（Aladdin 视角）**：
- Aladdin 横排几乎全绿，唯三的结构性缺口：**股票因子模型不是行业通用口径**（量化圈通用语言在 Barra/Axioma 手里）、**基金会计/法定报表不如 SimCorp/托管行原生**、**实施重且贵**。
- 反过来，没有任何单一对手能在 ≥8 个维度上同时打绿——这就是 front-to-back 完整度作为护城河的量化呈现。

---

## 10. 定价对比（公开可查部分）

> 原则重申：以下每个数字都标来源；标 [低] 的仅供量级参考。**查不到的明确写"未公开"。**

### 10.1 Aladdin

| 数据点 | 数字 | 来源与可信度 |
|---|---|---|
| JP Morgan 接入 Aladdin | 接入/整合费 $1.5M；年费随使用增长至 **$5.3M/年** | cognitivefinance.ai 文章转述（原始出处未注明，量级与 FT 历年报道一致）[低/中] |
| 每客户平均 ACV（倒推） | Technology Services ~$1.6B（2024）÷ 1000+ 客户 ≈ **$1.5-2M/客户/年**；大客户（$100B+ AUM 全平台）业内普遍认为在 **$5-10M+/年** | 分部收入为 [高]（BlackRock 财报）；倒推与区间为 [中/低] 推算 |
| 聚合站口径 | "机构客户 $500k 至数百万美元/年，取决于 AUM、模块数、用户数" | inspicker.com 聚合站 [低] |
| 公共养老金采购 | **CalSTRS**：投资委员会风险报告明确写明"Risk 团队使用 BlackRock Aladdin 风险系统管理总组合"（2024-2026 各期报告），**但金额未在这些文件中披露**；**NEST（英国）**：官方新闻稿宣布选用 Aladdin 作为全策略风险平台，金额未披露；**LACERA**（$58B）：公开**拒绝**采用，理由含"groupthink" | CalSTRS PDF、NEST 新闻稿 [高]；LACERA 为媒体转述 [中] |
| 结论 | **无公开价目表**；本次检索未能找到任何一份带金额的 Aladdin 政府采购合同（TED/usaspending/州养老金会议纪要均未命中带价条目） | 如实说明 |

### 10.2 竞品

| 产品 | 公开定价 | 来源与可信度 |
|---|---|---|
| Bloomberg Terminal | **$31,980/年/席**（单席，2025 年起续约价，2025-01 起涨 6.5%）；2 席以上 $28,320/席；10-24 席约 $25,490/席；企业级大单可谈至 $18k-22k/席 | godeldiscount/costbench/NeuGroup 等多个价格追踪站交叉 [中]（Bloomberg 官方不公示价格） |
| Bloomberg AIM | 未公开；行业惯例按用户数+资产规模谈判 | 未查到硬数字，如实说明 |
| Bloomberg PORT | Terminal 内含（席位费覆盖）；**PORT Enterprise** 为额外企业订阅，价格未公开 | 官方页结构 [高]，价格未公开 |
| B-PIPE/SAPI 数据馈送 | ~$50k-200k+/年（按量） | 价格追踪站 [低] |
| MSCI BarraOne/RiskMetrics | 未公开；Analytics 分部收入 $675M ÷ 客户群 → 单客户从几万（单模型订阅）到数百万美元（全平台）不等 | 分部收入 [高]，分布为推测 [低] |
| SimCorp Dimension | 未公开；行业共识为"license + 大额实施服务"模式，实施常达 license 的 1-2 倍、总周期 12-24 个月 | 实施周期 [中]（Limina 等比较页与咨询圈共识）；费用比例 [低] |
| State Street Alpha/CRD | 未公开；front office ARR $356M ÷ 数百 CRD 客户 → 平均每客户数十万至数百万美元/年 | ARR [高]（财报），倒推 [低] |
| FactSet 工作站 | 官方不公示；市场通行参考价 ~$12k/席/年（标准工作站，谈判空间大） | 公开常识/业内口径 [低] |
| LSEG Workspace | 未公开；业内普遍认为显著低于 Bloomberg 席位价 | [低] |
| Clearwater | SaaS，按管理资产规模计价（basis-point-on-assets 模式，招股书披露定价模式但无单价表） | 定价模式 [高]（S-1），单价未公开 |

### 10.3 定价格局小结

- **全行业只有 Bloomberg Terminal 存在"准公开市场价"**；所有平台级产品（Aladdin/SimCorp/Alpha/BarraOne）都是谈判定价、保密条款覆盖，公共养老金的董事会文件也只披露"用了谁"而极少披露"付了多少"。
- 量级排序（每年，中大型客户，综合上表 [中/低]）：**Aladdin 全平台（$1M-$10M+）≈ SimCorp 全套 ≈ Alpha 全套 > BarraOne/PORT Enterprise/FactSet PA 组合（$100k-$1M）> Terminal 单席（$32k）**。
- 隐性成本共识：平台级产品的**实施费与内部人力成本常与首年 license 同量级或更高**，且 12-24 个月的实施期本身就是最大的转换壁垒（Limina/Cutter 圈层共识 [中]）。

---

## 11. 结论：Aladdin 的真实差异化与真实弱点

### 11.1 真实差异化（为什么它还是老大）

1. **front-to-back 完整度无人全绿**（§9 矩阵）：单挑任何一个维度都有更强的专家（会计不如 SimCorp、股票因子不如 Barra/Axioma、席位性价比不如 Bloomberg），但"一张合同、一份数据、一条链路"只有 Aladdin 和（更年轻的）Alpha/SimCorp One 能讲，且 Aladdin 讲了 20 年。
2. **网络效应与数据规模**：~$21-25T 平台资产、1000+ 机构在同一套证券主数据和风险口径上对话；托管行、券商、指数商都优先适配 Aladdin 接口。FT 口径：仅 240 家大客户中的三分之一就承载了 $21T [中]。
3. **BlackRock 自用背书**：全球最大资管自己每天在上面跑 $10T+——这是任何纯软件商（SimCorp/SS&C/Bloomberg）给不出的"我们与你同吃一锅饭"证明；2008 年美国政府用它拆弹的历史仍是最强销售故事 [高]。
4. **收购补完私募盲区**：eFront（$1.3B, 2019）+ Preqin（£2.55B, 2025）让"公开+私募全组合视图"成为当前主叙事，竞品中只有 SimCorp Alternatives 与 Allvue 在追，且数据资产（Preqin）短期无可替代 [高]。
5. **开放化先手**：Studio/Data Cloud（Snowflake）/开源 SDK/Copilot——在"平台+生态"的转型上比 SimCorp、CRD 快半代 [中]。

### 11.2 真实弱点（竞品和客户实际在打的点）

1. **贵且不透明**：$1M-$10M+/年的量级把 90% 的市场排除在外；中小资管的现实选择是 Bloomberg 组合或云原生 SaaS（Enfusion/Limina/Venn）[中]。
2. **实施重**：12-24 个月全量上线是第三方比较的一致口径 [中]；Cutter Associates 甚至存在专门的"Aladdin 实施"咨询产品线和 Aladdin 客户互助社群（peer networking event）——一个需要专职咨询生态才能装好的系统，本身就说明了复杂度 [中]。
3. **利益冲突顾虑（"用最大竞争对手的系统"）**：
   - American Economic Liberties Project 公开主张把 Aladdin 从 BlackRock 拆分、指定为系统重要性市场设施 [中]（FT 转述）；
   - **LACERA（$58B）明确以"groupthink"风险为由拒绝 Aladdin** [中]；
   - 前 Principal Global Investors CEO Jim McCaughan："风险管理领域接近寡头的任何格局，一旦系统有弱点就格外危险" [中]；
   - FT 专题：$20T+ 资产共用同一套风险信号可能同质化市场行为、放大踩踏（crowding risk）[中]。
4. **股票/量化因子生态相对弱**：Aladdin 因子体系是自有口径、偏固收血统；股票量化研究的行业通用语言（论文、卖方、绩效归因对话）掌握在 Barra/Axioma 手里，导致大量 Aladdin 客户仍需另购 MSCI/Axioma 模型 [中]。这也解释了为什么 MSCI Analytics 能维持 $675M 收入与 ~50% EBITDA 利润率——Aladdin 没能杀死它。
5. **会计/法定报表非原生强项**：欧洲保险/养老金的多准则并行需求上，SimCorp 仍是默认答案；Aladdin 的会计多靠 Provider 模式与托管行合作交付 [中]。
6. **黑盒批评**：模型方法论（协方差估计、蒙特卡洛细节）不公开，学术界与监管的可验证性质疑长期存在；竞品（Axioma 的可定制模型、云原生厂商的"无利益冲突"）都在营销上直接攻击这一点 [中/低]。
7. **权威排名的微妙信号**：Chartis RiskTech100 近四年（2023-2026）整体第一是 **Moody's**，Bloomberg 稳居 top-10（2026 年第 9）[高]；BlackRock Solutions 未出现在本次检索到的 2025/2026 获奖新闻头部——不能据此断言排名下滑（Chartis 完整榜单在付费墙后），但至少说明在"风险技术"这个评价框架里 Aladdin 并非无可争议的第一名 [中/低，如实说明证据边界]。

### 11.3 一句话收束

> Aladdin 的护城河不在任何单项功能——每个单项都有人做得更好——而在于**它让"换掉 Aladdin"约等于"给飞行中的飞机换全套航电"**：数据、流程、人员技能、外部对手方接口全部长在上面。竞品的可行打法因此只有三种：托管行捆绑（Alpha）、欧洲主权+会计纵深（SimCorp One）、以及从 Aladdin 不屑于服务的下沉市场往上长（Clearwater/Enfusion 们）。这三种打法目前都活得不错，但没有一种在正面战场上撼动过它。

---

## 12. 来源与可信度

### 一手来源（最高可信度：官方新闻稿 / SEC / 政府养老金公开文件）

- [State Street 官方：以 $2.6B 收购 Charles River Development（2018）](https://investors.statestreet.com/investor-news-events/press-releases/news-details/2018/State-Street-to-Acquire-Charles-River-Development-for-2.6-Billion-07-20-2018/default.aspx)
- [SimCorp 官方：SimCorp 与 Axioma 合并（2023）](https://www.simcorp.com/about-us/news/2023/SimCorp-to-merge-with-Axioma)；[PRNewswire 版新闻稿（含 2800 员工、>€700M 收入）](https://www.prnewswire.com/news-releases/simcorp-to-merge-with-axioma-combining-best-in-class-risk-analytics-and-portfolio-construction-with-its-industry-leading-investment-management-platform-301979993.html)；[德交所：SimCorp Alternatives](https://www.deutsche-boerse.com/dbg-en/media/news-stories/press-releases/SimCorp-to-transform-private-market-investing-with-SimCorp-Alternatives-4654920)
- [MSCI/RiskMetrics 合并文件（SEC Form 425，$1.55B、合并后 ~$750M 收入）](https://www.sec.gov/Archives/edgar/data/0001295172/000110465910011008/a10-4856_1ex99d1.htm)
- [MSCI USE4 方法论（2011）](https://www.msci.com/documents/10199/242721/Barra_US_Equity_Model_USE4.pdf)；[GEMLT Factsheet](https://www.msci.com/documents/10199/242721/GEMLT_FactSheet.pdf)；[BarraOne 产品页](https://www.msci.com/data-and-analytics/portfolio-management/barra-one)
- [Bloomberg 官方：AIM（900+ 客户、15,000 用户、$17T）](https://professional.bloomberg.com/solutions/buy-side/)；[PORT 产品页](https://professional.bloomberg.com/products/bloomberg-terminal/portfolio-analytics/)；[MARS 产品页](https://professional.bloomberg.com/products/risk/mars/)；[PORT Enterprise AI Portfolio Commentary 新闻稿（750+ 客户）](https://www.bloomberg.com/company/press/bloomberg-advances-portfolio-analytics-with-launch-of-ai-portfolio-commentary-in-port-enterprise/)
- [Bloomberg 收购 BRAIS 新闻稿（~$780M，含 POINT IP）](https://www.prnewswire.com/news-releases/bloomberg-to-acquire-barclays-risk-analytics-and-index-solutions-business-300193748.html)；[Barclays 官方交割公告](https://home.barclays/news/2016/08/non-core-run-down/)
- [FactSet FY2025 财报（$2.32B 收入、ASV $2.4B）](https://investor.factset.com/news-releases/news-release-details/factset-reports-results-fourth-quarter-and-fiscal-2025)；[FactSet 收购 BISAM 新闻稿（$205.2M、B-One/Cognity、BISAM 收入 >$28M）](https://www.globenewswire.com/news-release/2017/03/20/942106/7768/en/FactSet-Acquires-BISAM-Leading-Performance-Measurement-Provider-and-Risk-Management-Thought-Leader.html)
- [Clearwater 官方：以 $1.5B 收购 Enfusion](https://cwan.com/press-releases/clearwater-analytics-to-acquire-enfusion/)；[完成公告](https://cwan.com/press-releases/clearwater-analytics-finalizes-acquisition-of-enfusion/)；[Clearwater 8-K（2025Q2 合并指引）](https://www.sec.gov/Archives/edgar/data/0001866368/000162828025021074/cwan-20250331xexx991.htm)
- [Numerix 收购 PolyPaths 新闻稿](https://www.prnewswire.com/news-releases/numerix-acquires-polypaths-expanding-market-expertise-in-structured-finance--fixed-income-301891606.html)
- **政府养老金文件**：[CalSTRS 投资委员会组合风险报告（明确使用 BlackRock Aladdin，2025-07 期）](http://www.calstrs.com/files/94f57db93/TRB+2025-07+Item+08.01+-+Portfolio+Risk+Report.pdf)（2024-2026 各期同口径）；[NEST 官方：选用 BlackRock Aladdin 作为投资风险平台](https://www.nestpensions.org.uk/schemeweb/nest/nestcorporation/news-press-and-policy/press-releases/Nest-selects-BlackRock-s-Aladdin-as-its-investment-risk-platform.html)
- [State Street 季度财报（front office ARR、Alpha mandate 数据）](https://www.sec.gov/Archives/edgar/data/0000093751/000009375124000722/stt3q24earningspresentat.htm)
- [Moody's 官方：Chartis RiskTech100 2026 连续第四年第一](https://www.moodys.com/web/en/us/insights/structured-finance/moodys-named-1-overall-in-chartis-risktech100-2026-a-landmark-year-for-asset-management-excellence.html)；[Bloomberg：连续三年 top-10（第 9）](https://www.bloomberg.com/company/press/bloomberg-earns-top-10-chartis-risktech100-ranking-for-third-consecutive-year/)

### 二手来源（行业媒体 / 咨询机构，可信度中）

- [Institutional Investor：The Relentless Ambition of BlackRock's Aladdin](https://www.institutionalinvestor.com/article/2bsxcauvaxemssuog7zls/corner-office/the-relentless-ambition-of-blackrocks-aladdin)；[State Street Ramps Up Competition With BlackRock's Aladdin](https://www.institutionalinvestor.com/article/2bswekxa78s7hfwc3t1j4/corner-office/state-street-ramps-up-competition-with-blackrocks-aladdin)
- [Finadium 转述 FT：Aladdin $20T 资产的 crowding 质疑](https://finadium.com/ft-blackrocks-aladdin-under-scrutiny-for-crowding-risk-as-assets-pass-20tr/)；[Economic Liberties：主张拆分 Aladdin（FT 转述）](https://www.economicliberties.us/media/financial-times-blackrock-should-split-off-its-aladdin-tech-platform-says-think-tank/)
- [P&I：Deutsche Börse 以 $850M 收购 Axioma](https://www.pionline.com/article/20190409/ONLINE/190409814/deutsche-boerse-to-acquire-axioma-for-850-million/)；[Cravath 交易公告（GA 注资 $715M）](https://www.cravath.com/news-insights/deutsche-b-rse-s-acquisition-of-axioma-and-partnership-with-general-atlantic.html)
- [Funds Europe：Santander AM 采用 Aladdin](https://funds-europe.com/santander-am-becomes-latest-customer-for-blackrocks-aladdin/)；[Invesco 签约 State Street Alpha](https://funds-europe.com/invesco-signs-up-to-state-street-s-alpha-platform/)
- [Global Custodian：Alpha 亚太负责人跳槽 BlackRock Aladdin](https://www.globalcustodian.com/state-street-loses-its-head-of-alpha-in-asia-pacific-to-blackrock/)；[Alpha 新 mandate 与收入](https://www.globalcustodian.com/state-street-confirms-two-new-alpha-mandates-as-revenue-rises-3-7-in-q1/)
- [WatersTechnology：Bloomberg 完成 BRAIS 收购](https://www.waterstechnology.com/emerging-technologies/2468841/bloomberg-closes-acquisition-of-barclays-risk-analytics-and-index-solutions)；[LSEG 收购 Yield Book/Citi 指数（$685M）](https://www.thetradenews.com/lse-completes-685-million-bond-indices-deal-with-citi/)
- [Western Asset 白皮书：Fixed-Income Managers Miss the POINT（POINT 迁移）](https://www.westernasset.com/us/en/pdfs/whitepapers/fixed-income-managers-miss-the-point-2016-12.pdf)
- [Investment Officer：Alto! Amundi casts a European spell on Aladdin（€100M run-rate、80+ 客户、€8T）](https://www.investmentofficer.lu/en/news/alto-amundi-casts-european-spell-aladdin)；[Markets Media：Amundi Technology 增长与并购意向](https://www.marketsmedia.com/amundi-technology-eyes-acquisitions-for-further-growth/)
- [Datos Insights：Does Deutsche Börse Have the Pieces to Build a Buy-Side Tech Powerhouse?](https://datos-insights.com/blog/does-deutsche-borse-have-the-pieces-to-build-a-buy-side-tech-powerhouse/)
- [Cutter Associates：Aladdin Implementation 案例](https://www.cutterassociates.com/insights/aladdin-implementation)；[Cutter：IBOR 专题](https://www.cutterassociates.com/insights/investment-book-of-record)；[Cutter 首届 Aladdin 客户互助活动](https://www.prweb.com/releases/cutter_associates_holds_inaugural_brs_aladdin_peer_networking_event/prweb11593683.htm)
- Bloomberg Terminal 定价追踪：[godeldiscount（$31,980/年）](https://godeldiscount.com/blog/bloomberg-terminal-cost-2026)、[costbench](https://costbench.com/software/financial-data-terminals/bloomberg-terminal/)、[NeuGroup（6.5% 涨价）](https://www.neugroup.com/bloomberg-terminals-how-much-more-youll-pay-next-year/)——多个独立追踪站交叉，Bloomberg 官方不公示，故整体标 [中]

### 三手/推测（低可信度，仅量级参考）

- [cognitivefinance.ai：JP Morgan 接入费 $1.5M、年费 $5.3M；LACERA 拒绝 Aladdin](https://www.cognitivefinance.ai/single-post/aladdin-and-the-genius-that-is-larry-fink) —— 单一来源转述，未注明原始出处（疑似 FT/II 报道），量级与其他报道一致，标 [低/中]
- [inspicker：Aladdin 定价 $500k-数百万美元/年](https://inspicker.com/blackrock-aladdin-ai-pricing-critical-2026-cost-roi/) —— SEO 聚合站，标 [低]
- [Limina 竞品比较页（Aladdin vs SimCorp、实施 12-24 个月）](https://www.limina.com/blackrock-aladdin-vs-simcorp) —— 竞争厂商内容营销，事实性描述可用、评价性结论需打折，标 [低/中]
- [Medium：Comparing Risk Models: MSCI Barra vs. Axioma（WLS/Huber 回归等方法论对比）](https://medium.com/@tzjy/comparing-risk-models-msci-barra-vs-axioma-6213bb507954)、[FactSet Insight: Choosing a Risk Model](https://insight.factset.com/choosing-a-risk-model) —— 方法论细节与厂商文档一致，标 [中/低]
- [6sense：eFront/Allvue 市占（网站技术侦测口径）](https://6sense.com/tech/investment-portfolio-management/efront-vs-allvue) —— 方法论粗糙，仅供参考，标 [低]

### 明确未挖到的数据（不编造）

1. **任何带金额的 Aladdin 政府采购合同**——CalSTRS/NEST 只公开"用了"，未公开"付多少"；TED、usaspending、州养老金会议纪要检索均未命中带价条目。
2. Bloomberg AIM、PORT Enterprise、BarraOne、SimCorp Dimension、CRD/Alpha 的**任何官方单价**。
3. front-to-back 选型的**逐单胜负份额数据**（各家均只公布赢单，从不公布输单）。
4. BlackRock Solutions/Aladdin 在 Chartis RiskTech100 2025/2026 的**具体名次**（完整榜单在付费墙后）。
