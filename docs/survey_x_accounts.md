# X (Twitter) 长期追踪账号调研 — 补缺 curated 名单

> 生成：2026-07-02 ｜ 方法：多来源 web 交叉验证（名单类文章 + SentimenTrader 榜单 + Reuters 能源 follow 名单 + Feedspot DeFi 榜 + 逐个 handle 点名核查）
>
> **⚠️ API 验证受阻**：twitterapi.io 返回 `Credits is not enough. Please recharge` —— 账户余额耗尽，本次未能跑 API 验证（0 次消耗）。
> 粉丝数为调研时点的**估算值**（来源：榜单文章/搜索快照），充值后建议用下面脚本批量复核：
> `GET https://api.twitterapi.io/twitter/user/info?userName=<handle>`（存在性+粉丝）+ `GET /twitter/user/last_tweets`（活跃度），全表约 2×45=90 次调用。
> 所有 handle 均已通过 web 搜索点名确认**账号存在且 2025 年后仍在发帖**（个别标注 ⚠️ 的除外）。

与现有名单的关系：`curated_macro_crypto` 已覆盖 Fed watcher（NickTimiraos）、货币市场（FedGuy12）、宏观（LynAlden/elerianm/RayDalio）、加密宏观（CryptoHayes/cobie/hasufl/Vitalik/lookonchain/EmberCN/WuBlockchain）。本表**只补缺口领域**，不重复。

---

## 一、候选全表（按领域）

### 1. A股 / 中国政策观察

| Handle | 粉丝(估) | 活跃度 | 定位 |
|---|---|---|---|
| `michaelxpettis` | ~340k | 高频原创 | 北大/卡内基，中国经济失衡与贸易再平衡框架，长线思维必读 |
| `Brad_Setser` | ~200k | 高频原创 | CFR，中国贸易顺差/资本流动/汇率数据拆解，可验证的数据派 |
| `niubi` | ~150k | 高频 | Bill Bishop（Sinocism），中国政策动向的西方观察基准 |
| `HAOHONG_CFA` | ~700k+ | 高频，中英双语 | 洪灏，中国市场策略师，A股/港股周期判断，有公开可验证的判断记录 |
| `YuanTalks` | ~60k | 高频快讯 | 中国市场实时资讯（政策/A股/汇率），信号密度高，适合监控管道 |
| `Trinhnomics` | ~90k | 高频原创 | Trinh Nguyen（Natixis），亚洲EM/中国出口链/供应链转移，2025 年持续活跃 |
| `GlennLuk` | ~50k | 中频长文 | 中国经济数据的"反 Pettis"视角，做平衡参照 |
| `prchovanec` | ~40k | 中频 | Patrick Chovanec，中国宏观+美股策略，备选 |

### 2. 半导体 / AI 硬件产业链（补 dnystedt / trendforce / rwang07）

| Handle | 粉丝(估) | 活跃度 | 定位 |
|---|---|---|---|
| `dylan522p` | ~300k | 高频原创 | Dylan Patel（SemiAnalysis），AI芯片/数据中心供应链最强公开分析 |
| `Jukanlosreve` | ~200k | 极高频 | 韩系爆料（Samsung/SK Hynix/HBM/存储涨价），2026 年存储紧缺周期核心信源 |
| `mingchikuo` | ~500k | 中频原创 | 郭明錤（天风国际），苹果/AI硬件供应链预测，判断记录可回溯 |
| `IanCutress` | ~180k | 高频 | Dr. Ian Cutress，芯片工艺/架构技术拆解，偏技术面补充 |
| `SKundojjala` | ~30k | 中频 | Sravan Kundojjala（SemiAnalysis），手机SoC/晶圆代工份额数据 |
| `Cheng_Ting_Fang` | ~30k | 中频 | 郑婷方（Nikkei Asia），台系半导体供应链一线报道 |

### 3. 能源 / 大宗商品

| Handle | 粉丝(估) | 活跃度 | 定位 |
|---|---|---|---|
| `JavierBlas` | ~500k | 高频原创 | Bloomberg 能源专栏，《The World for Sale》作者，油气+大宗定价权叙事 |
| `JKempEnergy` | ~90k | 高频 | John Kemp（ex-Reuters），油气库存/仓位数据流派，2026 年仍活跃（已独立运营） |
| `staunovo` | ~70k | 极高频 | Giovanni Staunovo（UBS），原油/EIA数据即时解读，信号密度高 |
| `Ole_S_Hansen` | ~90k | 高频 | Ole Hansen（Saxo），大宗商品全景周报（金属/农产品/能源） |
| `WarrenPies` | ~200k | 中高频 | 3Fourteen Research，能源×宏观量化框架，判断有记录 |
| `chigrl` | ~350k | 高频 | Tracy Shuchart，能源/大宗交易视角，略杂但覆盖广 |
| `BurggrabenH` | ~150k | 中频长文 | Alexander Stahel，大宗商品基本面长文（欧洲能源危机期间成名） |

### 4. 美股财报 / 期权流

| Handle | 粉丝(估) | 活跃度 | 定位 |
|---|---|---|---|
| `unusual_whales` | ~2.5M | 极高频 | 期权异动+市场新闻聚合，监控管道信号源（噪音需过滤） |
| `SpotGamma` | ~150k | 高频 | Gamma/dealer positioning 分析，期权结构对现货的牵引 |
| `OptionsHawk` | ~150k | 高频 | Joe Kunkle，期权流+基本面结合，比纯 flow bot 信号高 |
| `TheTranscript_` | ~60k | 中高频 | 财报电话会关键引语摘录，财报季信号密度极高 |
| `eWhispers` | ~150k | 高频 | 财报日历/预期，工具型账号 |
| `DeItaone` | ~900k | 极高频 | Walter Bloomberg 头条速递 bot，适合做快讯输入（非分析） |

### 5. 量化 / 因子研究

| Handle | 粉丝(估) | 活跃度 | 定位 |
|---|---|---|---|
| `bennpeifert` | ~150k | 高频 | Benn Eifert（QVR），波动率/衍生品市场结构，quant twitter 公认最高信号之一 |
| `__paleologo` | ~90k | 中高频 | Gappy Paleologo（Balyasny 量化研究主管），因子/风险模型/职业洞见 |
| `CliffordAsness` | ~200k | 高频 | Cliff Asness（AQR），因子投资权威，观点鲜明有论文支撑 |
| `choffstein` | ~100k | 中频 | Corey Hoffstein，Return Stacked/策略研究，"Flirting with Models"播客 |
| `MebFaber` | ~200k | 高频 | Meb Faber（Cambria），资产配置/历史数据回测 |
| `macrocephalopod` | ~70k | 中频 | 匿名从业 quant，实务讨论质量高 |

### 6. 稳定币 / DeFi 协议研究（补 hasufl / cobie / Vitalik）

| Handle | 粉丝(估) | 活跃度 | 定位 |
|---|---|---|---|
| `nic__carter` | ~350k | 高频 | Nic Carter（Castle Island），稳定币/比特币基础设施研究+政策辩论 |
| `CampbellJAustin` | ~60k | 高频 | Austin Campbell（ex-Paxos），稳定币设计/监管第一线，GENIUS 法案时代核心声音 |
| `0xngmi` | ~130k | 高频 | DefiLlama 创始人，DeFi 数据/协议机制，无立场数据派 |
| `DefiIgnas` | ~180k | 高频 | Ignas，DeFi 协议变化/叙事轮动研究 |
| `dcfgod` | ~170k | 中高频 | DeFi 协议估值/现金流视角，交易导向 |
| `tokenterminal` | ~150k | 中频 | 协议基本面数据（收入/费用），工具型账号 |

### 7. 美债 / 利率交易员（补 FedGuy12）

| Handle | 粉丝(估) | 活跃度 | 定位 |
|---|---|---|---|
| `lisaabramowicz1` | ~450k | 极高频 | Lisa Abramowicz（Bloomberg），利率/信用市场即时评论 |
| `biancoresearch` | ~280k | 高频 | Jim Bianco，利率/宏观结构变化（去中介化、收益率曲线） |
| `MacroAlf` | ~450k | 高频 | Alfonso Peccatiello（The Macro Compass），全球宏观/利率教学式拆解 |
| `concodanomics` | ~100k | 中频 ⚠️ | Conks，货币市场 plumbing 长文；发帖频率近年下降，API 复核后再定 |
| `RayDalio` | 已在名单 | — | （重合项，跳过） |

### 8. 地缘政治 × 市场

| Handle | 粉丝(估) | 活跃度 | 定位 |
|---|---|---|---|
| `robin_j_brooks` | ~250k | 极高频 | Robin Brooks（Brookings，ex-高盛首席FX），制裁/油价上限/美元体系，数据驱动 |
| `adam_tooze` | ~230k | 高频 | Adam Tooze（Chartbook），宏观×地缘×历史框架 |
| `vtchakarova` | ~100k | 高频 | Velina Tchakarova，地缘格局（DragonBear）体系化观察 |
| `typesfast` | ~200k | 中高频 | Ryan Petersen（Flexport CEO），关税/物流/供应链一线体感 |
| `ianbremmer` | ~1M | 高频 | Eurasia Group，覆盖广但信号密度偏低，备选 |
| `sentdefender` | ~800k | 极高频 ⚠️ | OSINT 战况快讯，速度快但可靠性参差，只适合做快讯触发不做事实源 |

---

## 二、最终推荐 Top 25（可直接粘进 config.yaml）

```yaml
# —— 调研补缺推荐（2026-07，来源 docs/survey_x_accounts.md）——
# A股/中国政策
- michaelxpettis    # 北大/卡内基，中国经济失衡框架，中国宏观必读
- Brad_Setser       # CFR，中国贸易/资本流动数据拆解
- niubi             # Bill Bishop (Sinocism)，中国政策观察基准
- HAOHONG_CFA       # 洪灏，A股/港股策略，中英双语高频
- YuanTalks         # 中国市场实时快讯，监控管道信号源
- Trinhnomics       # Natixis 亚洲EM经济学家，中国出口链/供应链转移
# 半导体/AI硬件
- dylan522p         # SemiAnalysis 创始人，AI芯片/数据中心供应链最强公开分析
- Jukanlosreve      # 韩系存储爆料（HBM/DRAM涨价周期核心信源）
- mingchikuo        # 郭明錤，苹果/AI硬件供应链预测
- SKundojjala       # SemiAnalysis，SoC/代工份额数据
# 能源/大宗
- JavierBlas        # Bloomberg 能源专栏，油气+大宗叙事
- staunovo          # UBS 原油分析师，EIA/OPEC 数据即时解读
- WarrenPies        # 3Fourteen，能源×宏观量化框架
- Ole_S_Hansen      # Saxo 大宗商品全景
# 美股财报/期权流
- unusual_whales    # 期权异动+市场新闻聚合（高频，需过滤）
- SpotGamma         # Gamma/dealer positioning
- TheTranscript_    # 财报电话会关键引语，财报季高信号
# 量化/因子
- bennpeifert       # QVR，波动率/市场结构，quant twitter 最高信号之一
- __paleologo       # Balyasny 量化研究主管，因子/风险模型
- CliffordAsness    # AQR，因子投资权威
# 稳定币/DeFi
- nic__carter       # Castle Island，稳定币/加密基础设施研究
- CampbellJAustin   # ex-Paxos，稳定币设计与监管第一线
- 0xngmi            # DefiLlama 创始人，DeFi 数据派
# 利率/地缘
- lisaabramowicz1   # Bloomberg 利率/信用即时评论
- robin_j_brooks    # Brookings ex-高盛FX，制裁/美元体系数据驱动
```

次选（名额不够先不进，观察一轮再说）：`GlennLuk`、`JKempEnergy`、`chigrl`、`BurggrabenH`、`OptionsHawk`、`eWhispers`、`DeItaone`、`choffstein`、`MebFaber`、`DefiIgnas`、`dcfgod`、`biancoresearch`、`MacroAlf`、`adam_tooze`、`vtchakarova`、`typesfast`、`IanCutress`、`Cheng_Ting_Fang`。

---

## 三、验证备注 / 刷掉的候选

- **API 验证未执行**：twitterapi.io 余额耗尽（"Credits is not enough"），充值后跑 `user/info` + `last_tweets` 复核全表（~90 次调用）。
- **handle 变更确认**：Robin Brooks 已从 `RobinBrooksIIF` 迁移到 **`robin_j_brooks`**（转投 Brookings 后改名）；Jukan 旧号 `kakashiii111` 已改为 **`Jukanlosreve`**。
- **刷掉/不推荐**：
  - `TimDuy` — Tim Duy 已于 2024 年去世，账号停更（很多旧榜单仍在推荐，注意）。
  - `BaldingsWorld`（Christopher Balding）— 中国数据判断记录争议大，信噪比低。
  - `zerohedge` — 覆盖广但立场先行、可靠性差，不适合做信号源。
  - `christiaandefi`/`defi_dad` 等 Feedspot DeFi 榜头部 — 偏推广/教学，研究密度不足。
  - `ianbremmer`、`sentdefender` — 保留在候选表但不进 Top（信号密度低 / 可靠性参差）。
- **待复核**：`concodanomics` 近期发帖频率疑似下降；`prchovanec`、`macrocephalopod` 粉丝量级较小，API 确认活跃度后再决定。
