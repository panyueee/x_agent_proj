# BlackRock Aladdin 深挖 01：历史与商业

> 调研日期：2026-07-02 ｜ 定位：对 `docs/survey_aladdin.md` 第 1 节（历史与商业）的 3-5 倍深挖
> 方法：英文公开源为主 + **SEC EDGAR 10-K 原文逐年核对**（收入数字全部从 10-K HTML 原文 grep 提取，非二手转述）
> 可信度约定：**[一手]** = SEC 文件/官方新闻稿/当事人访谈；**[二手]** = 主流媒体；**[低]** = 单一来源/二手转述，仅供参考

---

## 0. 相对初版 survey 的增量（TL;DR）

1. **Technology Services 收入 2007-2025 完整逐年序列已从 10-K 原文确认**（初版只有 2024 约 $16 亿一个模糊数字），并厘清了三次口径改名与重述。
2. 创立史补全：Fink 1986 年 First Boston 亏损 $1 亿的细节、8 人创始团队的 MBS 血统、Hallac 生平（1988 年第一名员工，Sun 工作站"放在冰箱和咖啡机之间"）、Rob Goldstein 与 Green Package 的手工时代。
3. Kidder Peabody 之战补全：Jett 假利润丑闻背景、约 $70 亿组合、"Halloween 1994" 轶事、Fink 亲口确认这是技术第一次对外使用。
4. 2008 危机角色量化：三个 Maiden Lane 的规模、费用（首年 $45.3M + $25.5M）、最终纳税人净收益（合计约 $119 亿）、Grassley 质询；2020 疫情重演的合同细节与终止日期（2022-05-02）。
5. 客户版图与平台规模的**口径考古**：$7T(2008) → $15T(2013) → $20T(2017，最后一次官方披露) → $21.6T(2020，FT 从 1/3 客户拼出的下限)。
6. 监管争议三条线完整化：FSOC/SIFI 辩论（含 2014-07-31 转向）、欧盟 Ombudsman 裁决原文要点、Economist 模型单一文化论的原文引语。

---

## 1. 1988 创立：人物、动机与第一台工作站

### 1.1 创伤驱动的公司：Fink 的 $1 亿教训

- Larry Fink 1976 年入职 First Boston，是华尔街抵押贷款证券化（MBS/CMO）的开创者之一（与所罗门兄弟的 Lewis Ranieri 齐名）。**1986 年二季度，他领导的部门因利率意外下行、对冲头寸失效，亏损约 $1 亿美元**。Fink 后来回忆自己"没有留下来的选项"，1988 年初离开 First Boston。**[二手，多源一致]**
- 关键归因：亏损的直接原因不是方向判断本身，而是**风险计量与后台计算能力缺失**——他的团队看不清自己组合在利率情景下的真实敞口。这成为 BlackRock "先建风险系统、再做资管" 的创业母题，也是 Aladdin 存在的第一性原因。**[二手]**
- 1988 年 Fink 与 7 位联合创始人（Robert Kapito、Susan Wagner、Barbara Novick、Ben Golub、Hugh Frater、Ralph Schlosstein、Keith Anderson）在黑石集团（Blackstone）支持下创立 Blackstone Financial Management（1992 年改名 BlackRock），核心成员多数来自 First Boston 的固收/抵押贷款业务。**[二手，多源一致]**

### 1.2 Charles Hallac：Aladdin 的初代架构师

| 项目 | 内容 |
|---|---|
| 生平 | 1964 年生于特拉维夫，在马尼拉长大，1986 年 Brandeis 大学经济学+计算机双学位 |
| BlackRock 之前 | 1986-1988 在 **First Boston 抵押贷款产品组（Mortgage Products Group）任 associate**——与 Fink 同源 |
| 1988 | 以 **BlackRock 第一名员工** 身份加入；**买下了公司的第一台电脑——一台 Sun Microsystems 工作站，摆在一间单间办公室里"冰箱和咖啡机之间"** |
| 贡献 | 与 Bennett Golub 一起写出第一批数学模型（针对当时的新产品 CMO 的定价与风险模型）；被公认为 Aladdin 初代架构师，Aladdin 被称为 BlackRock 的"中枢神经系统" |
| 后续 | 联合创办并执掌 BlackRock Solutions 至 2009；2009-2014 任 COO；2014 年起任联席总裁 |
| 结局 | 2015-09-09 因结直肠癌去世，年仅 50 岁 |

来源：Wikipedia (Charles Hallac / Aladdin 词条)、BlackRock 官方悼念页。**[一手（官方页）+ 二手交叉]**

### 1.3 Bennett Golub 与 Rob Goldstein

- **Bennett Golub**：联合创始人、长期 CRO，与 Hallac 共同完成 Aladdin 最初的 CMO 数学模型；其"风险透明"哲学（risk transparency）见初版 survey 第 2.2 节，此处不重复。**[一手（Risk.net 访谈/著作）]**
- **Rob Goldstein**：1994 年以 20 岁之龄、Binghamton 大学经济学毕业生身份加入（时年 BlackRock 约 55 人），入职组合分析组（Portfolio Analytics Group）。早期工作是**用一把尺子逐行手工比对每日风险报告找不一致**——分析师把摘要打印在绿色纸上送给组合经理，这就是 **Green Package（绿皮报告）名字的由来**。Goldstein 从风险分析员一路做到 BlackRock Solutions 负责人、2014 年起任全公司 COO，被 Fortune 称为 BlackRock 的 "boy wonder"。**[二手（Fortune 2012/2024 专访）]**
- 现任 Aladdin 全球负责人为 **Sudhir Nair**。**[一手（BlackRock 官网）]**

### 1.4 为什么起点是债券风险工具

- 1988 年的 BlackRock 是一家固收资管公司，客户资产集中在 MBS/CMO——当时华尔街最复杂、凸性最扭曲的品种。提前偿付（prepayment）行为使得这些证券**必须逐路径模拟**才能算清风险，这决定了 Aladdin 从第一天起就是"模拟数千种利率/利差/提前偿付情景"的计算引擎，而不是记账系统。**[二手，多源一致]**
- "Aladdin" = **A**sset, **L**iability, **D**ebt & **D**erivative **I**nvestment **N**etwork。命名时点没有官方一手披露；有单一来源称软件是在 **1997 年承接 Freddie Mac 项目时才正式改名为 Aladdin**（此前是无名的内部系统 + 对外的 Green Package 报告服务）。**[低：cognitivefinance.ai 单一来源，未见官方确认]**

---

## 2. 1994 成名战：GE / Kidder Peabody 烂账

### 2.1 背景：GE 手里的烫手山芋

- GE 1986 年收购老牌投行 Kidder, Peabody & Co.。1994 年 4 月，Kidder 爆出**政府债交易员 Joseph Jett 虚报约 $3.5 亿假利润**的丑闻，GE 被迫减记；同期 1994 年美联储意外连续加息引发"债券大屠杀"（Great Bond Massacre），Kidder 持有的巨额按揭组合深度受伤。**[二手，多源一致]**
- Kidder 的按揭组合当时被认为是**"世界上最复杂的组合之一"**，规模约 **$70 亿**（Institutional Investor 口径）。GE 需要有人算清楚这堆 CMO 到底值多少钱、怎么卖掉。**[二手]**

### 2.2 BlackRock 的介入

- BlackRock 受托分析并协助处置该组合。一则流传的细节：**1994 年万圣节前后，Kidder 方面打来电话，发来一份含 1000 多笔交易的持仓表（内部戏称 "Michelle Spreadsheet"）**，BlackRock 团队用自家系统逐笔重定价。**[低：单一来源（cognitivefinance.ai），细节未见其他印证]**
- 结果：分析顺利完成，Kidder 当年即被 GE 作价出售给 PaineWebber（1994-10 宣布，1995 年完成，Kidder 品牌消亡）。**[二手 + SEC 1994 年 8-K 佐证交易本身]**
- **Fink 本人确认：这是 BlackRock 的技术第一次"对外使用"——为客户的需求而非自己的组合服务**。这句定位是 Aladdin 商业化叙事的起点。**[二手（Fink 访谈转述）]**
- 商业意义：这一战让"BlackRock = 能拆别人拆不动的固收炸弹"的声誉在机构圈立住，直接埋下 2008 年美联储找上门的伏笔。

---

## 3. 对外商业化时间线：从卖报告到卖操作系统

| 年份 | 事件 | 可信度 |
|---|---|---|
| 1988 | 内部系统在一台 Sun 工作站上启动（无产品名） | 一手/二手交叉 |
| 1994 | **Green Package 作为服务开始对外销售**（风险报告订阅，最早的变现形态）；同年 GE/Kidder 咨询项目 | 二手（Fortune） |
| 1997 | 承接 Freddie Mac 项目；一说系统在此时得名 Aladdin | 低（单一来源） |
| 1999 | BlackRock 纽交所 IPO；**开始对外授权 Aladdin 软件**（Britannica 口径 "By 1999… licensing"） | 二手 |
| 2000 | **成立 BlackRock Solutions（BRS）事业部**，Aladdin 正式作为产品对外销售（Wikipedia/公司口径） | 二手，多源 |
| 2008-2012 | 金融危机咨询业务（FMA，Financial Markets Advisory）爆发：BRS 收入 2007 $190M → 2008 $393M（**+107%**） | **一手（10-K）** |
| 2015 | 收购 FutureAdvisor（数字财富）；2017 收购 Cachematrix（现金管理门户）——外围补件 | 一手（新闻稿） |
| 2019 | **$13 亿现金收购 eFront**（详见第 7 节） | 一手（新闻稿/8-K） |
| 2019-2020 | Aladdin Wealth、Aladdin Climate 相继推出；宣布 Azure 迁移 | 一手（新闻稿） |
| 2024-2025 | **£25.5 亿收购 Preqin**（2024-06-30 宣布，2025-03-03 完成） | 一手（新闻稿/8-K） |

结构要点：对外卖的东西经历了三级跳——**卖报告（Green Package 订阅）→ 卖咨询（危机拆弹，FMA）→ 卖平台（Aladdin 整套授权，SaaS 化订阅费）**。10-K 的收入列报变化（见第 5 节）忠实记录了这个迁移：2017 年起把"技术收入"从"咨询收入"里拆出来单列，本质是向资本市场宣告自己有一块可以按 SaaS 估值的业务。

---

## 4. 2008 金融危机：政府的"拆弹专家"，及 2020 年重演

### 4.1 三个 Maiden Lane（数字全部来自纽约联储官方公告）

2008 年 3 月贝尔斯登濒临破产时，纽约联储先找 BlackRock 协助评估其资产以促成 JPMorgan 收购（Vanity Fair：财政部与纽约联储就 $300 亿贝尔斯登出售融资向 Fink 寻求建议）；随后成立特殊目的载体（SPV）持有这些资产，并**以无投标（no-bid）方式**聘 BlackRock Solutions 为资产管理人：

| 载体 | 承接资产 | 成立 | 纽约联储贷款偿还 | 纳税人最终净收益 | 来源 |
|---|---|---|---|---|---|
| Maiden Lane I | 贝尔斯登约 $30B 按揭资产（促成 JPM 收购） | 2008-06 | 2012-06-14 全额+利息 | **约 $2.5B**（含付给纽约联储的 $765M 利息；2018-09 最后一批证券售罄） | 纽约联储公告 2018-09-18 **[一手]** |
| Maiden Lane II | AIG 证券借贷组合的 RMBS（面值 $39.3B，联储支付 $20.5B） | 2008-12 | 2012-02-28（$19.4B 义务清偿） | **约 $2.8B** | 纽约联储公告 2012-02-28 **[一手]** |
| Maiden Lane III | AIG CDS 对手方的 CDO | 2008-11 | 2012-06-14 全额+利息 | **约 $6.6B** | 纽约联储公告 2012-08-23 **[一手]** |

- 三个载体合计为公众产生**约 $119 亿净收益**，全部处置由 BlackRock Solutions 执行。**[一手（纽约联储）]**
- **费用**：BlackRock 第一年就 Maiden Lane I 收取 **$45.3M**，Maiden Lane II+III 合计 **$25.5M**；合同至少三年，后续还可再赚 $50M 以上（American Banker 当时估算合计约 $71M+）。**[二手（American Banker）]**
- Maiden Lane III 的投资管理协议（2008-11-25 签署，纽约联储 × BlackRock Financial Management × ML III LLC）后被公开。**[一手（公开合同文本）]**

### 4.2 危机中的其他委托与"影子央行"争议

- 除 Maiden Lane 外，BlackRock 同期还监控/分析 **Fannie Mae 与 Freddie Mac 的资产负债表、AIG 与贝尔斯登的有毒资产**，并为美联储 **$1.25 万亿 agency MBS 购买计划**提供分析支持；危机顶点时 JPMorgan 的 Dimon、Morgan Stanley 的 Mack、AIG 的 Willumstad 都私下向 Fink 求助。（Vanity Fair 2010 名篇 *Larry Fink's $12 Trillion Shadow*：BlackRock 管理或监控超 **$12 万亿** 资产。）**[二手（Vanity Fair）]**
- **收入印证（10-K 原文）**：BlackRock Solutions and advisory 收入 2007 年 $190M → 2008 年 $393M（**同比 +107%**，FY2009 10-K 明言）→ 2009 年 $477M。危机是 BRS 商业化的火箭燃料。**[一手]**
- **争议**：参议员 Chuck Grassley 在参议院金融委员会质询中点名 BlackRock——一边为纽约联储做有毒资产分析（Maiden Lane），另一边关联实体又参与财政部购买有毒资产的项目（PPIP），要求审计潜在利益冲突；"无投标合同"是国会批评的核心词。**[一手（参议院金融委员会新闻稿 2010-07-23）+ 二手]**

### 4.3 2020 疫情：历史重演，争议升级

| 项目 | 内容 | 来源 |
|---|---|---|
| 聘用 | **2020-03-24**，纽约联储再次（仍是免投标、事后才公开合同）聘 BlackRock **FMA** 部门管理三块：PMCCF（一级市场公司债）、SMCCF（二级市场公司债+ETF）、agency CMBS 购买 | 纽约联储公告 **[一手]** |
| 费用 | 彭博按合同条款估算 BlackRock 每年最多约 **$40M**；BlackRock 同意就 SMCCF 持有的 iShares ETF **返还/豁免管理费** | 二手（Bloomberg） |
| 冲突焦点 | SMCCF 买入的 ETF 中 **iShares 占近一半**；宣布计划后投资者抢跑，单周 $10 亿涌入 iShares LQD；30 个进步派团体联名警告"BlackRock 是它将要决定买谁的那些公司的最大股东" | 二手（Common Dreams / American Prospect / Forbes） |
| 国会 | 众议员 Pressley、García、Tlaib 联名要求对 BlackRock 合同实施更严格监督、披露对其自家 ETF 的影响 | 一手（议员办公室新闻稿） |
| 结束 | 纽约联储与 BlackRock 的该投资管理合同于 **2022-05-02 终止** | 一手（纽约联储 agency CMBS 页面） |

- 深层批评（Forbes/da Costa, American Prospect）：问题不在费用（金额不大），而在**信息与地位**——同时作为美联储的执行代理人、全市场最大资管与最大 ETF 供应商，BlackRock 获得了"看到央行手牌"的结构性优势；"Fed 把 BlackRock 变成了事实上的第四家政府分支"是 2020 年舆论的典型修辞。**[二手]**

### 4.4 危机的商业遗产：从拆弹队到行业标准

- 危机对 Aladdin 商业化的作用不只是 BRS 收入翻倍（4.2），更重要的是两条：
  1. **需求端范式转移**：2008 后"风险透明"从卖点变成买方机构的监管义务与董事会要求，Aladdin 恰好是市面上唯一经过"官方拆弹"验证的整合平台——政府委托本身成了最贵的营销案例。**[作者归纳]**
  2. **供给端规模跃迁**：2009-06 BlackRock 以 **$13.5B 收购 Barclays Global Investors（含 iShares）**，AUM 一夜翻倍至约 $2.7T、跃居全球第一大资管；BGI 全部组合并入 Aladdin，使平台自营侧的资产与数据量级同步翻倍——外部客户买到的是"管理着全球最大资管"的同一套系统。**[二手，多源]**
- 一个常被引用的对照：危机前（2007）BRS 收入 $190M、危机后十年（2018）$785M——**金融危机前后，"卖风险管理"业务的规模扩大了约 4 倍**，而同期 BlackRock 总 AUM 扩大约 5 倍，两者互为飞轮。**[一手数字 + 作者归纳]**

---

## 5. 收入规模：10-K 原文逐年核对（本文最硬的一节）

### 5.1 口径演变（重要，读表前必看）

| 期间 | 10-K 收入行名称 | 含义 |
|---|---|---|
| ~2016 | **BlackRock Solutions and advisory** | Aladdin + FMA 咨询 + 其他打包；FY2016 10-K 起在正文里单独披露其中的 "Aladdin revenue" |
| FY2017 | **Technology and risk management revenue** | 首次把技术收入单列，咨询（FMA 等）挪去 "Advisory and other revenue"；旧口径下 2017 年 BRS and advisory 本应为 $805M |
| FY2018-2024 | **Technology services revenue** | 仅改名，数字未重述（FY2018 10-K 明言 "Prior period amounts have not changed"，但 2017 从 $677M 微调重列为 $657M，系进一步重分类） |
| FY2025 | **Technology services and subscription revenue** | 并入 Preqin 后再次改名，含数据订阅 |

### 5.2 逐年收入表（单位：百万美元；全部出自 10-K 原文）

| 财年 | 收入 | 同比 | 口径 | 数字出处（哪一年 10-K） |
|---|---|---|---|---|
| 2007 | 190 | — | BRS and advisory | FY2009 10-K（2010-03-10 提交） |
| 2008 | 393 | **+107%** | BRS and advisory | FY2009 10-K |
| 2009 | 477 | +21% | BRS and advisory | FY2009 10-K |
| 2010 | 460 | -4% | BRS and advisory | FY2011 10-K（2012-02-28 提交） |
| 2011 | 510 | +11% | BRS and advisory | FY2011 / FY2013 10-K |
| 2012 | 518 | +2% | BRS and advisory | FY2013 10-K（2014-02-28 提交） |
| 2013 | 577 | +11% | BRS and advisory | FY2013 10-K |
| 2014 | 635（其中 Aladdin 474） | — | BRS and advisory | FY2016 10-K（2017-02-28 提交） |
| 2015 | 646（其中 Aladdin 528） | +2% | BRS and advisory | FY2016 10-K |
| 2016 | 714（其中 Aladdin 595） | +11% | BRS and advisory | FY2016 10-K |
| 2017 | **677**（重列后 657；旧 BRS 口径 805） | +14% | Technology and risk management | FY2017 10-K（2018-02-28 提交）；重列见 FY2018 10-K |
| 2018 | **785** | +19% | Technology services | FY2018 10-K（2019-02-28 提交） |
| 2019 | **974** | +24%（eFront 并表） | Technology services | FY2019 10-K（2020-02-28 提交） |
| 2020 | **1,139** | +17% | Technology services | FY2020 10-K（2021-02-25 提交） |
| 2021 | **1,281** | +12% | Technology services | FY2021 10-K（2022-02-25 提交） |
| 2022 | **1,364** | +6% | Technology services | FY2022 10-K（2023-02-24 提交） |
| 2023 | **1,485** | +9% | Technology services | FY2023 10-K（2024-02-23 提交） |
| 2024 | **1,603** | +8% | Technology services | FY2024 10-K（2025-02-25 提交，新主体 CIK 2012383） |
| 2025 | **1,981**（其中 Preqin 贡献约 210） | +24% | Technology services and subscription | FY2025 10-K（2026-02-25 提交） |

阅读要点：

1. **18 年 ~10 倍**（2007 $190M → 2025 $1,981M），但期间有三次口径变化与两次并购（eFront、Preqin）注水，内生年化增速大约低两位数到 15% 之间。
2. 增长的两个台阶都靠外力：2008（危机咨询 +107%）与 2019/2025（并购 +24%）。**平台本体（organic Aladdin）是一门稳定 8-13% 增长的生意，不是超高速 SaaS**。
3. 占比：2025 年技术与订阅收入 $1,981M / 总收入 $24,216M ≈ **8.2%**（10-K 原文总收入行）。Aladdin 对 BlackRock 的意义远大于收入占比——它是资管主业的成本引擎与获客钩子。
4. FY2022 10-K 披露了汇率敞口：**约 25% 的 Aladdin 收入以非美元计价**，且平台费部分与固收平台资产规模挂钩（2022 年债市大跌因此拖累增速至 +6%）。**[一手]**
5. 收费模式（10-K Revenue Recognition 节，历年一致）：**(i) 按平台上头寸价值的比例 (ii) 固定费率 (iii) 按工时计费** 三种方式组合——注意第 (i) 条：Aladdin 收入与客户资产规模部分联动，牛市自动涨价。**[一手]**

### 5.3 ACV（Annual Contract Value）披露口径

- 10-K 定义（FY2022 起正式作为 Non-GAAP 指标披露）：**"客户合同项下经常性订阅费的前瞻年化价值"**，假设到期合同全部续约（除非已收到终止通知）；签约即计入，即使收费尚未生效。**[一手（FY2022-2025 10-K Non-GAAP 节）]**
- 但 **10-K 只披露 ACV 增速，从不披露绝对金额**：

| 财年 | ACV 同比（10-K 原文） |
|---|---|
| 2021 | +13% |
| 2022 | +8%（当年 Aladdin 净销售创纪录，约一半新客户签多产品） |
| 2023 | +10%（超半数新客户签多产品） |
| 2024 | +12% |
| 2025 | **+31%（含 Preqin）/ +16%（剔除 Preqin，有机）** |

- 绝对值只能靠外部拼接：2025 年投资者日材料与三方统计称 ACV 进入 2026 年时**接近 $20 亿**。**[二手/低（businessstats 等三方汇编 + 投资者日演示）]**
- 初版 survey 说"2025 年增速 24%、ACV 接近 $20 亿"——收入 +24% 与 ACV 口径此处已厘清：+24% 是收入（含 Preqin 并表 $210M），+16% 才是有机 ACV 增速。

### 5.4 一个容易被忽略的 10-K 细节：收入几乎全是"随用随收"，不是长积压

- FY2019/FY2020 10-K 披露的技术服务收入**剩余履约义务（remaining performance obligation）仅 $221M / $231M**（相对当年 $974M / $1,139M 收入），且注明主要是客户预付款；BlackRock 选择了会计豁免，不把"一年以内合同"和"按用量计费部分"计入。**[一手]**
- 解读：Aladdin 合同虽然名义上是多年期，但收入确认高度贴近当期服务量（头寸价值比例 + 固定费 + 工时），**账面几乎没有 SaaS 公司那种巨额递延收入池**。评估其收入质量应看 ACV 增速与净留存，而非 backlog——这也解释了 BlackRock 为何自造 ACV 这个 Non-GAAP 口径来讲订阅故事。**[一手数字 + 作者归纳]**

### 5.5 竞争格局：都是"事后追赶者"

| 竞品 | 归属 | 关键动作 | 与 Aladdin 的相对位置 |
|---|---|---|---|
| **Charles River (CRD) / State Street Alpha** | State Street 2018 年以 **$2.6B** 收购 CRD，2019 推出 Alpha | 前台 OMS 起家 + State Street 托管/中后台捆绑 | 唯一敢打"前中后台一体 + 托管"牌的对手；靠托管客户存量导流 |
| **SimCorp Dimension** | 2023 年被德意志交易所集团收购（约 €3.9B），并与 Axioma 整合 | 2018 起补另类资产工具 | 欧洲机构的"非美国供应商"选项；IBOR/会计见长 |
| **Bloomberg AIM / PORT** | Bloomberg | 依托终端网络 | 分析强、工作流轻，多作为并用而非替代 |
| **MSCI RiskMetrics/Barra** | MSCI | 传统风险模型商 | 只卖模型不卖工作流，正是 Aladdin 1990s 拉开差距的对照组 |
| **Two Sigma Venn** | Two Sigma | 2019 年末推出的数据+风险工具 | 轻量因子透视，瞄准 Aladdin 覆盖不经济的中小机构 |

- 格局判断（作者归纳）：竞品全部是在 State Street 2018 年买 CRD 之后才形成"整合平台"叙事，落后 Aladdin 约 20 年的数据清洗积累与网络效应；但 2019-2025 的 eFront/Preqin 收购说明 BlackRock 认为**下一个战场在私募市场数据**，那里 Aladdin 的先发优势并不存在（Preqin 之前，PitchBook/Burgiss 等各占山头）。**[二手（II 文章）+ 作者归纳]**

---

## 6. 客户版图与平台资产规模的口径考古

### 6.1 具名客户表（仅列公开可查者）

| 客户 | 类型 | 规模/内容 | 时间 | 可信度 |
|---|---|---|---|---|
| General Electric | 企业 | Kidder $7B 按揭组合分析处置 | 1994 | 二手，多源 |
| Freddie Mac | GSE | 咨询项目（一说 Aladdin 得名于此） | 1997 | 低（单一来源） |
| 美联储/纽约联储 | 央行 | Maiden Lane I/II/III；2020 PMCCF/SMCCF/CMBS | 2008-2012 / 2020-2022 | 一手 |
| 美国财政部/两房 | 政府 | Fannie/Freddie 资产负债表监控等危机委托 | 2008-2010 | 二手（Vanity Fair） |
| CalPERS | 养老金 | 用 Aladdin 跟踪 $260B 加州公务员养老资产 | 2013（Economist 时点） | 二手 |
| CalSTRS | 养老金 | 平台用户（与 CalPERS 并列被报道） | 2020 | 二手 |
| Deutsche Bank（资管部门，今 DWS） | 银行系资管 | **€934B 于 2013-11 迁移上线**（初版的 "€900B" 即此） | 2013 | 二手（Economist） |
| Prudential plc | 保险 | 约 $700B | — | 二手（Wikipedia 汇总） |
| Bank of Israel | 央行 | 2019 年起使用 | 2019- | 二手 |
| UBS、Credit Suisse | 银行 | FT 报道的 240 家机构客户举例 | 2020 | 二手（FT） |
| **Vanguard** | 竞争对手资管 | **用 Aladdin 补充自有风险监控工具**（不是全套替代） | 2020（FT 报道口径） | 二手（FT 转述，多源复述） |
| 韩国 NPS（国民年金） | 主权养老金 | 全面上线 Aladdin（State Street/BNY 提供中后台配套），全球最大养老金之一 | 2025（官方公告） | 一手（BlackRock 官网公告） |
| Microsoft、Google 财资部门 | 科技公司金库 | 据报道用 Aladdin 管理公司现金/投资 | 2013 前后报道 | 低/二手（初版已收录，未见一手） |
| 55,000 名投资专业人员 / 30,000 用户、50 国 | — | 用户数两个流传口径 | 2020 前后 | 低（口径混杂） |

- 客户总数官方口径：eFront 收购新闻稿（2019）称 Aladdin 服务 **"225+ 机构"**；FT（2020）称 **240 家**；现官网口径 "1,000+ 客户"（含 Aladdin Wealth 等全线产品，口径已变宽）。**[一手/二手混合，注意口径]**

### 6.2 平台监控资产规模：五个数字，四种口径

| 数字 | 时点 | 口径与出处 | 可信度 |
|---|---|---|---|
| $7T | 2008 | 危机时平台客户资产（含政府委托），cognitivefinance 汇编 | 低 |
| **$15T（$4.1T 自营 + $11T 外部）** | 2013-12 | The Economist《The monolith and the markets》：约占全球 $225T 金融资产的 **7%**，30,000 个组合。初版的 "$11T/2013" 是**外部客户部分** | 二手（Economist 实地报道） |
| **$20T** | 2017-02 | **BlackRock 最后一次官方披露平台资产**，此后停更 | 二手（FT 引官方） |
| **$21.6T** | 2020-02 | **FT 调查**：仅从 240 家客户中约 **1/3** 的公开文件（年报、监管披露）+ 当事方确认拼出的**下限**，≈ 全球股债的 10% | 二手（FT，方法论透明） |
| "$25T+" | 2024-2026 | 三方统计站/媒体外推，无官方确认 | 低 |

**口径警告**（写作/引用时务必区分）：
1. "平台上处理/监控的资产"（assets on platform）≠ BlackRock 自己的 AUM（2025 年约 $14T）；
2. Aladdin 客户可能只用部分模块（只买风险分析 vs 全套 OMS），"在平台上"不代表"由 Aladdin 全流程管理"；
3. BlackRock 2017 年后故意停止披露该数字——合理推测是想淡化"单点故障/垄断"叙事（FSOC/FCA 关注升温的时段吻合）。**[推测，标注为作者判断]**

---

## 7. 收购扩张：eFront、Preqin 与 Aladdin Climate

### 7.1 eFront（2019）：补私募市场的"工作流"

| 项目 | 内容 |
|---|---|
| 交易 | 2019-03-22 宣布独家协议，**$1.3B 全现金**，卖方为 PE 机构 Bridgepoint 及 eFront 员工；**2019-05-10 完成交割**；资金 = 自有流动性 + 债务 |
| 标的 | 法国公司，另类资产（PE/RE/基建/私债）前中后台软件，**700+ 客户、48 国** |
| 逻辑 | 当时 Aladdin 只覆盖公开市场；eFront 提供另类资产的基金会计、投资组合监控、LP/GP 工作流 → "whole portfolio view"（公私一体视图）叙事的起点 |
| 整合 | eFront 前 CEO Tarek Chouman 转任 Aladdin 商业拓展负责人；FY2019 技术收入 +24% 主要由并表贡献；FY2023 10-K 提到 eFront **on-premise license 续约收入在续约时点一次性确认**——说明其 SaaS 化转型多年后仍未完成 |

来源：BlackRock 新闻稿/8-K、Bridgepoint 新闻稿、P&I。**[一手为主]**

### 7.2 Preqin（2024-2025）：补私募市场的"数据"

| 项目 | 内容 |
|---|---|
| 交易 | **2024-06-30 宣布，£2.55B（约 $3.2B）全现金；2025-03-03 完成** |
| 标的 | 英国私募市场数据商（基金业绩、募资、GP/LP 数据库），创始人 **Mark O'Hare 出任 BlackRock 副主席（Vice Chair）**；4,000+ 客户关系 |
| 逻辑 | BlackRock 测算私募市场数据 TAM 为 **$8B，年增 12%，2030 年达 $18B**；战略 = eFront（工作流）+ Preqin（数据）+ Aladdin（风险/组合）三件套，把私募市场"做成像公开市场一样可分析、可指数化"（Fink 公开谈论未来推出私募市场指数） |
| 财务印证 | FY2025 10-K：技术与订阅收入 +$378M 中**约 $210M 来自 Preqin**；ACV +31% 含 Preqin、+16% 有机（见 5.3） |
| 监管插曲 | 英国 FCA 对交易做过审查问询（媒体报道其数据市场影响），未阻止交割 | 

来源：BlackRock 新闻稿/8-K、FY2025 10-K、Markets Media 等。**[一手为主；FCA 审查细节为二手]**

### 7.3 Aladdin Climate（2020）：自建新风险维度

- 2020-12 发布，定位"首个在证券级别同时计量气候物理风险与转型风险"的产品（方法论细节初版 survey 1.3 已覆盖，不重复）。商业逻辑与收购不同：气候风险没有成熟标的可买，且与监管披露（TCFD/CSRD）绑定，自建即可把**监管合规需求转化为订阅收入**，同时服务 BlackRock 自身 2020 年起的净零叙事。**[一手（新闻稿）+ 作者归纳]**
- 与第 8 节的欧盟争议直接相关：BlackRock 一边卖气候风险分析工具，一边中标欧盟 ESG 银行监管规则研究——批评者认为这构成"既当裁判顾问又当卖水人"的闭环。

### 7.4 外围小收购（时间线补全用）

| 年份 | 标的 | 金额 | 去向 |
|---|---|---|---|
| 2015 | FutureAdvisor | 未公开（媒体估 $150-200M） | 数字投顾能力 → Aladdin Wealth 生态 |
| 2017 | Cachematrix | 未公开 | 银行现金管理门户 |
| 2021 | Baringa 气候模型授权（非收购） | — | Aladdin Climate 转型情景模型 |

**[二手]**

---

## 8. 监管与舆论争议：四条战线

### 8.1 SIFI 之辩："大到不能倒"的科技侧

- **2013-09**：美财政部金融研究办公室（OFR）发布报告 *Asset Management and Financial Stability*，点出大型资管的系统性风险渠道（赎回挤兑、证券借贷、杠杆、羊群行为），被视为把 BlackRock 等推向 SIFI（系统重要性金融机构）指定的前奏。**[一手（OFR 报告）]**
- 若 FSOC 指定三大独立资管（BlackRock、Fidelity、Vanguard）为 SIFI，将覆盖美国资管 AUM 的 **63.9%**，并使其接受美联储审慎监管（资本金、压力测试）。**[二手（Committee on Capital Markets Regulation）]**
- **BlackRock 与 Fidelity 领衔行业游说反对**；BlackRock 向 FSB/FSOC 提交多轮意见书，核心论点："资管是代理人模式（agency model），资产在客户资产负债表上，倒闭不产生银行式传染；应监管**活动**而非**机构**"。**[一手（BlackRock 提交 FSB 的意见书 PDF）]**
- **2014-05-19** FSOC 专门开会讨论；**2014-07-31 FSOC 决定不将资管公司列为 SIFI**，转向 activities-based approach——行业游说的标志性胜利。**[二手（Financial Planning 等）]**
- 后续变体：2021 年英国 **FCA 警告 Aladdin 若故障"可能造成严重消费者伤害或损害市场完整性"**；美国智库 American Economic Liberties Project 主张把 Aladdin 指定为**系统重要性金融市场公用事业（SIFMU）**——即"不指定 BlackRock，指定 Aladdin"，这是"too big to fail 科技侧"讨论的最新形态。**[二手（Wikipedia 汇总，FCA 原文可查）]**

### 8.2 模型单一文化（monoculture）：The Economist 的经典批评

《The monolith and the markets》（The Economist, 2013-12-07）要点，比初版更细：

- 事实底座：$15T 上平台 ≈ 全球金融资产 7%，30,000 个组合，East Wenatchee 6,000 台计算机、近 2,000 名员工运维；**当时对外授权费收入约 $400M/年**（与本文 5.2 表 2013 年 $577M 的 BRS 口径可互相印证：$400M 是纯 Aladdin 授权部分）。**[二手（Economist）]**
- 核心论证：当足够多的资金用**同一套模型看世界**，模型的盲点变成市场的盲点——类比 2008 年前的评级机构（所有人外包信任给同一个有缺陷的模型）。匿名组合经理引语："**Nobody understands some of this stuff without going through BlackRock**"。
- Fink 的回应（原话）："**If you believe models are going to be right, you're going to be wrong.**" 并称 Aladdin 只是教你"道路的路缘（kerbs of the road）"；Economist 自己访谈的 Aladdin 用户也称仅将其作为补充而非替代。
- 该批评在 2020 FT 报道（$21.6T）与学术圈（算法羊群/性能预测文献）中被反复引用，已成 Aladdin 叙事的固定组成部分。**[二手]**

### 8.3 与美联储的利益冲突（2008 与 2020，详见第 4 节）

浓缩版：两轮都是**免投标 + 事后公开合同**；2008 年焦点是"既当联储的分析师又参与财政部购毒资产项目"（Grassley 质询），2020 年焦点是"用联储的钱买自家 ETF"（iShares 占 SMCCF ETF 持仓近半，尽管 BlackRock 返还了相关管理费）。两轮均无监管处罚，争议停留在国会信函与舆论层面。**[一手（国会文件）+ 二手]**

### 8.4 欧盟：Ombudsman 裁定"委员会本应更警惕"

- **2020-04**：欧盟委员会（European Commission，注意：授标方是委员会，抗议方包括欧洲议会议员）把"将 ESG 目标整合进欧盟银行规则"的研究合同授予 BlackRock Investment Management，合同额仅 **€280,000**。
- 抗议：数十名 MEP 联署质询；**92 个民间组织**公开信要求终止合同；Change Finance 联盟向欧洲监察使（European Ombudsman）正式投诉。
- **2020-11-23 Ombudsman 裁决**（Emily O'Reilly）：委员会**"本应更加警惕"**，授标决定"未能提供足够保证以排除对利益冲突风险的合理怀疑"；现行采购规则"不够健全和清晰"；并点出 BlackRock 报价"**异常之低……可被视为试图对与其客户利益相关的投资领域施加影响**"。但因规则所限未认定形式违规，合同未被撤销——裁决的实际效果是推动欧盟修订财务条例中的利益冲突条款。**[一手（Ombudsman 案件 57060 决定书）+ 二手（Responsible Investor / CEO 组织）]**

### 8.5 争议的商业解读（作者归纳）

四条战线共享同一个结构性事实：**Aladdin 的商业价值（标准化、网络效应、数据回流）与其系统性风险（单点故障、模型同质化、信息优势）是同一枚硬币的两面**。BlackRock 的一贯防御策略是：(1) 强调代理人模式与"工具非决策者"；(2) 主动停止披露平台规模数字；(3) 用返费/防火墙姿态化解个案。至今没有任何辖区对 Aladdin 施加过实质性结构监管——这本身是其政府关系资产的证明，也是尾部风险所在。**[推测/归纳]**

---

## 附录：大事年表（1986-2026）

| 时间 | 事件 | 可信度 |
|---|---|---|
| 1986 | Fink 在 First Boston 因利率对冲失效亏损约 $1 亿，埋下"风险计量优先"母题 | 二手多源 |
| 1988 | 8 人创立 Blackstone Financial Management；Hallac 作为第一名员工买下第一台 Sun 工作站，与 Golub 写出首批 CMO 模型 | 一手/二手交叉 |
| 1992 | 更名 BlackRock | 二手 |
| 1994 | Goldstein 入职（55 人时代）；Green Package 作为服务对外销售；**GE/Kidder Peabody $7B 组合分析——技术首次对外使用** | 二手 |
| 1997 | Freddie Mac 项目；一说系统得名 Aladdin | 低（单一来源） |
| 1999 | IPO；开始对外授权 Aladdin | 二手 |
| 2000 | BlackRock Solutions 事业部成立 | 二手多源 |
| 2008-03 | 协助纽约联储评估贝尔斯登资产；随后免投标受托 Maiden Lane I | 一手（纽约联储）/二手 |
| 2008-11/12 | Maiden Lane II/III 成立，BlackRock 管理 AIG 相关资产；BRS 收入当年 +107% | 一手 |
| 2009-06 | $13.5B 收购 BGI/iShares，AUM 翻倍全球第一，组合全量入 Aladdin | 二手多源 |
| 2010-07 | Grassley 就 BlackRock 双重角色提交质询 | 一手 |
| 2012 | 三个 Maiden Lane 贷款全部还清（最终合计净收益约 $119 亿，尾款 2018 年清算完毕） | 一手 |
| 2013-09 | OFR 报告引发资管 SIFI 之辩 | 一手 |
| 2013-11/12 | Deutsche Bank €934B 迁移上线；Economist 发表 "The monolith and the markets"（$15T/7%） | 二手 |
| 2014-07-31 | FSOC 放弃将资管机构指定为 SIFI，转向活动监管 | 二手 |
| 2015-09 | Hallac 去世（50 岁） | 一手 |
| 2017-02 | 最后一次官方披露平台资产：$20T | 二手（FT 引官方） |
| 2018 | 收入行更名 "Technology services"；State Street $2.6B 买 CRD 开启追赶 | 一手/二手 |
| 2019-03/05 | $1.3B 收购 eFront（宣布/交割） | 一手 |
| 2020-02 | FT 调查：$21.6T（240 客户之 1/3 的公开文件） | 二手 |
| 2020-03-24 | 纽约联储再次免投标聘 BlackRock FMA 管理疫情信贷工具 | 一手 |
| 2020-04~11 | 欧盟委员会 €280k ESG 合同 → 92 组织 + MEP 抗议 → Ombudsman 裁定"本应更警惕" | 一手（裁决书） |
| 2020-12 | 发布 Aladdin Climate | 一手 |
| 2021 | 英国 FCA 警告 Aladdin 故障可能损害市场完整性 | 二手 |
| 2022-05-02 | 纽约联储终止与 BlackRock 的疫情工具管理合同 | 一手 |
| 2024-06-30 | 宣布 £2.55B 收购 Preqin；同年 BlackRock 控股重组（EDGAR 新 CIK 2012383） | 一手 |
| 2025-03-03 | Preqin 交割，O'Hare 任 BlackRock 副主席 | 一手 |
| 2025 | 技术与订阅收入 $1,981M（+24%，含 Preqin $210M）；有机 ACV +16% | 一手（FY2025 10-K） |
| 2025-12 | 宣布 AWS 合作（多云，GA 预计 2026 下半年） | 一手 |

---

## 9. 来源与可信度

### 一手（SEC 文件 / 官方新闻稿 / 监管机构 / 当事人）

| 事实 | 来源 |
|---|---|
| 逐年收入 2007-2025（5.2 表全部数字）、口径改名、ACV 定义与增速、25% 非美元收入、收费三模式 | BlackRock 10-K 原文：CIK 1364742（FY2007-2023，[EDGAR](https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001364742&type=10-K)）与 CIK 2012383（FY2024-2025，2024 年控股重组后新主体 "BlackRock, Inc."；旧 CIK 更名为 "BlackRock Finance, Inc."）。本文数字由 10-K HTML 原文程序化提取核对 |
| Maiden Lane I/II/III 净收益 $2.5B/$2.8B/$6.6B、偿还日期 | 纽约联储公告：[2018-09-18 (ML I)](https://www.newyorkfed.org/newsevents/news/markets/2018/an180918)、[2012-02-28 (ML II)](https://www.newyorkfed.org/newsevents/news/markets/2012/an120228)、[2012-08-23 (ML III)](https://www.newyorkfed.org/newsevents/news/markets/2012/an120823) |
| ML III 投资管理协议文本（2008-11-25） | [Public Intelligence 公开的合同](https://publicintelligence.net/investment-management-agreement-for-maiden-lane-iii-llc/) |
| 2020-03-24 聘用、2022-05-02 合同终止 | [纽约联储 agency CMBS 页面](https://www.newyorkfed.org/markets/domestic-market-operations/monetary-policy-implementation/agency-commercial-mortgage-backed-securities) |
| Grassley 质询 BlackRock 利益冲突（2010-07-23） | [参议院金融委员会新闻稿](https://www.finance.senate.gov/news/press-releases/grassley-submits-questions-for-committee-record-about-taxpayer-dollars-for-aig-goldman-sachs-counterparties) |
| Pressley/García/Tlaib 要求监督 BlackRock 合同 | [Pressley 办公室新闻稿 2020-04-22](https://pressley.house.gov/2020/04/22/reps-pressley-garc-tlaib-call-stricter-oversight-over-blackrock-contracts/) |
| eFront：$1.3B、2019-03-22 宣布、700+ 客户、Aladdin 225 机构 | [BlackRock 新闻稿](https://ir.blackrock.com/news-and-events/press-releases/press-releases-details/2019/BlackRock-to-Acquire-eFront----Industry-Leading-Alternatives-Investment-Software-Provider/default.aspx)、[8-K Ex-99.1](https://www.sec.gov/Archives/edgar/data/0001364742/000119312519083451/d686182dex991.htm)、[Bridgepoint 新闻稿](https://www.bridgepointgroup.com/about-us/news-and-insights/press-releases/2019/Bridgepoint-sells-eFront-to-BlackRock)（交割 2019-05-10 为 MarketScreener 转述，二手） |
| Preqin：2025-03-03 完成、O'Hare 任副主席、TAM $8B→$18B | [BlackRock 完成公告](https://www.blackrock.com/corporate/newsroom/press-releases/article/corporate-one/press-releases/blackrock-completes-preqin-acquisition)、[8-K](https://www.sec.gov/Archives/edgar/data/0002012383/000119312525044184/d919939dex991.htm)；Preqin 收入贡献 ~$210M 出自 FY2025 10-K |
| 欧盟 Ombudsman 裁决（2020-11-23，案件 57060） | [Ombudsman 决定书](https://www.ombudsman.europa.eu/en/case/en/57060)；[欧洲议会质询文件 E-006993/2020](https://www.europarl.europa.eu/doceo/document/E-9-2020-006993_EN.html) |
| OFR 报告与 BlackRock 反 SIFI 意见书 | OFR *Asset Management and Financial Stability* (2013-09)；[BlackRock 提交 FSB 的意见书](https://www.fsb.org/uploads/BlackRock.pdf) |
| Hallac 生平、悼念 | [BlackRock 官方悼念页](https://www.blackrock.com/corporate/about-us/leadership/charlie-hallac) |
| NPS 上线 Aladdin | [BlackRock Aladdin 官网公告](https://www.blackrock.com/aladdin/discover/nps-unveils-technology-platform-unified-by-blackrocks-aladdin-with-state-street-and-bny-specialized-investment-operations-services) |
| Kidder 出售 PaineWebber（1994） | [PaineWebber 8-K (1994)](https://www.sec.gov/Archives/edgar/data/75754/000095012394001705/0000950123-94-001705.txt) |

### 二手（主流媒体，多源交叉）

| 事实 | 来源 |
|---|---|
| Economist 2013：$15T/7%/30,000 组合、6,000 台计算机、~$400M 授权费、Fink 引语、monoculture 论证 | The Economist *The monolith and the markets* (2013-12-07)，[全文转载](https://bambooinnovator.com/2013/12/08/blackrock-the-monolith-and-the-markets-getting-15-trillion-in-assets-on-to-a-single-risk-management-system-is-a-huge-achievement-is-it-also-a-worrying-one/) |
| FT 2020：$21.6T（240 客户之 1/3 的公开文件）、2017-02 最后一次官方披露 $20T、UBS/Credit Suisse、Vanguard 补充使用 | FT *BlackRock's black box*（2020-02），经 [Finadium 摘要](https://finadium.com/ft-blackrocks-aladdin-under-scrutiny-for-crowding-risk-as-assets-pass-20tr/) 等多源转述 |
| Maiden Lane 费用 $45.3M/$25.5M/$71M+ | [American Banker](https://www.americanbanker.com/news/blackrock-to-get-71m-on-maiden-lane) |
| 2020 联储合同 ~$40M/年、"clout 大于 fees" | [Bloomberg (2020-05-14)](https://www.bloomberg.com/news/articles/2020-05-14/blackrock-s-role-as-fed-adviser-confers-more-clout-than-fees) |
| 2020 冲突批评（iShares 近半、LQD $1B 抢跑、30 团体联名） | [Forbes/da Costa](https://www.forbes.com/sites/pedrodacosta/2020/04/20/a-glaring-new-conflict-of-interest-undermines-public-trust-in-federal-reserve/)、[Common Dreams](https://www.commondreams.org/news/2020/05/11/conflicts-interest-abound-progressives-sound-alarm-blackrock-prepares-lead-feds)、[American Prospect](https://prospect.org/coronavirus/unsanitized-blackrock-buyer-and-seller-federal-reserve-bailout/) |
| Fink $100M 亏损与离开 First Boston | [Euromoney](https://www.euromoney.com/article/27bjsstsqxhkmh1qi81a0/capital-markets/larry-fink-the-pioneer-who-fought-his-way-to-the-top/)、[Britannica](https://www.britannica.com/money/Larry-Fink-businessman)、Wikipedia 交叉 |
| Vanity Fair 2010：$12T、贝尔斯登 $30B 融资咨询、CEO 们求助 Fink | Suzanna Andrews, *Larry Fink's $12 Trillion Shadow*（Vanity Fair, 2010-03），[RealClearPolitics 转载](https://www.realclearpolitics.com/2010/03/02/larry_fink039s_12_trillion_shadow_230276.html) |
| Goldstein 生平、Green Package 得名、1994 年 55 人 | [Fortune *BlackRock's boy wonder* (2012)](https://fortune.com/2012/10/11/blackrocks-boy-wonder/)、[Fortune 2024 COO 专访](https://fortune.com/2024/05/16/blackrock-coo-robert-goldstein-ai-whole-new-world-30-year-career/) |
| SIFI 辩论过程与 2014-07-31 结果、63.9% AUM 测算 | [Committee on Capital Markets Regulation](https://capmktsreg.org/why-sifi-designation-is-not-the-answer-to-possible-herding-behavior-by-asset-managers/)、[Financial Planning](https://www.financial-planning.com/news/asset-managers-win-reprieve-from-sifi-designation)、[ThinkAdvisor](https://thinkadvisor.com/2013/09/30/fsoc-targets-asset-management-firms-as-systemicall) |
| 欧盟争议过程（92 组织、MEP 投诉） | [Corporate Europe Observatory](https://corporateeurope.org/en/2020/11/ombudsmans-decision-blackrock-must-force-european-commission-u-turn-climate-finance-rules)、[Responsible Investor](https://www.responsible-investor.com/meps-turn-to-ombudsman-as-eu-commission-refuses-to-terminate-blackrock-esg-banking-contract/) |
| 1988 Sun 工作站、CMO 模型、1994 GE 首次外用、2000 BRS 成立 | [Wikipedia: Aladdin (BlackRock)](https://en.wikipedia.org/wiki/Aladdin_(BlackRock))、[Britannica: BlackRock](https://www.britannica.com/money/BlackRock-Inc)、[Wikipedia: Charles Hallac](https://en.wikipedia.org/wiki/Charles_Hallac) |
| Kidder $7B 组合、"advised GE on Kidder portfolio" | [Institutional Investor: Institutional Investor of the Year](https://www.institutionalinvestor.com/article/2bsx24j345pixvso5z37k/culture/institutional-investor-of-the-year-blackrocks-larry-fink) |
| FCA 2021 警告、AELP 公用事业主张 | Wikipedia 汇总（FCA 原始讲话可进一步溯源） |

### 推测 / 单一来源（使用时须谨慎）

| 事实 | 说明 |
|---|---|
| 1997 Freddie Mac 项目使系统得名 "Aladdin" | 仅 [cognitivefinance.ai](https://www.cognitivefinance.ai/single-post/aladdin-and-the-genius-that-is-larry-fink) 一处；该文另处有明显错误（称 Hallac 1994 年加入，与官方 1988 冲突），整体可信度打折 |
| "Michelle Spreadsheet"、Halloween 1994 电话 | 同上，单一来源轶事 |
| 2008 平台资产 $7T、55,000 用户 | 单一来源汇编数字 |
| Microsoft/Google 金库使用 Aladdin | 早年媒体转述，未见一手确认 |
| ACV 绝对值 ~$2B（2026 初） | 三方统计站/投资者日拼接，10-K 无绝对值 |
| "2017 年后停止披露平台规模是为淡化垄断叙事" | 本文作者推断，非任何来源明言 |
| FCA 对 Preqin 交易的审查细节 | 媒体简报级，未核对 FCA 原始文件 |

### 未找到公开披露（明确说明，避免误引）

- Aladdin **分产品线**（Risk / Wealth / eFront / Climate）的收入拆分：10-K 从未披露。
- ACV 的官方绝对金额：仅披露增速。
- 2008 年 Maiden Lane 合同的完整费率表（ML III 协议已公开，ML I/II 细节靠媒体）。
- "Aladdin" 命名的确切年份与命名人：无一手来源。
- Vanguard 使用 Aladdin 的合同规模与模块范围：仅 FT 一句"补充自有工具"。

---

*本文件为 docs/aladdin 系列第 1 篇（历史与商业）。技术架构、方法论与对本项目的借鉴见 `docs/survey_aladdin.md` 第 2-5 节。*
