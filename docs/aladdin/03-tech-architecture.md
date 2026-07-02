# BlackRock Aladdin 技术架构深挖（工程侧一手资料）

> 调研日期：2026-07-02 ｜ 方式：纯公开网络资料（WebSearch/WebFetch）｜ 定位：对 `docs/survey_aladdin.md` 2.1 节的 3-5 倍深挖
>
> **可信度分级约定**（从高到低）：
> - **[一手]** BlackRock Engineering 博客、专利原文、官方新闻稿、GitHub 源码、厂商官方案例页
> - **[演讲]** 大会演讲（QCon/KubeCon/JuliaCon）本人陈述或权威转录
> - **[JD推断]** 招聘 JD / 面经归纳出的技术栈，反映"在招什么"而非"核心怎么写的"
> - **[媒体]** 媒体报道/维基百科转述
> - **[溯源不明]** 广为流传但找不到原始出处的数字，显式标注
>
> 检索受阻处如实说明：Medium 对 BlackRock Engineering 文章做了自定义域名重定向循环，多篇文章只能拿到搜索摘要级内容；blackrock.com 部分产品页与 patents.justia.com 返回 403。凡未能核实全文的，均在行文中注明。

---

## 1. 总体架构画像：一句话版本

Aladdin 是一个**从 1988 年单台 Sun 工作站长出来的、以自研消息总线（BMS）为中枢神经的服务化单体生态**：核心计算是 C++/Java，分析层引入过 Julia 和 Rust，前端约 100 个 React/TypeScript 应用，数据层从 Sybase ASE 一路演进到 Cassandra/Solr/Hive/Snowflake 多引擎并存，2020-2022 年整体从自建数据中心（东华盛顿 Wenatchee 等）迁上 Azure，2025 年宣布多云扩展到 AWS。工程组织约 7,000 人（其中约 4,000 名工程师），每晚对 1,500 万+ 组合做全量风险计算。

各条断言的出处与可信度在下文逐节展开。

---

## 2. 自研中间件中枢：BlackRock Messaging System（BMS）

这是本次深挖里**最硬的一手资料簇**：官方工程博客专文 + 两项已授权专利互相印证。

### 2.1 工程博客侧（[一手]，Medium 文章 `the-blackrock-messaging-system-aeae461e4211`）

注：该文全文因 Medium↔engineering.blackrock.com 重定向循环无法直接抓取，以下内容来自搜索引擎索引到的文章正文片段与多个转述源交叉，可信度按 [一手/转述混合] 处理。

- Aladdin **从早期就是服务化的**（"comprised of services from its early days"），而非后来才拆的单体。
- 架构设计规则（"One BlackRock" 原则族）：
  1. **访问一份数据、下一笔单，只允许有一条路径**（one way to access a piece of data or book a trade）；
  2. 不重复造轮子；
  3. **前端应用禁止直连数据库或文件系统**，一切经由后端服务；
  4. 服务可以跑在任意主机上、随时可迁移；
  5. 调用方**永远不需要知道服务在哪台主机**、也不负责负载均衡（位置透明 + 中心化 LB，由 BMS 承担服务发现）。
- 消息范式三种：**Request/Response、Conversation（会话）、Broadcast（发布/订阅）**。
- **每条消息都带不可伪造（unspoofable）的用户与客户端标识**——鉴权/审计做在消息层，而不是各服务自扫门前雪。
- BMS 本身**支持热部署**（hot-deployable），降低总线自身变更风险。
- 语言绑定：**Java、C++、Python、JavaScript、Perl、C#、Julia** 七种客户端库——这份名单本身就是 Aladdin 内部语言生态的官方快照（注意有 Perl 和 Julia，没有 Go/Rust）。
- 规模口径：连接**数千个服务，横跨多个数据中心和地域**。

### 2.2 专利侧（[一手]，专利原文）

BMS 的两个关键机制分别落成了专利，assignee 均为 BlackRock Financial Management, Inc.：

**US10341196B2 —— "Reliably updating a messaging system"（可靠地更新消息系统）**
- 发明人：Elliot Hamburger, Jonathan S. Harris, Jeffrey A. Litvin, Sauhard Sahi, John D. Valois, Ara Basil；优先权日 2015-01-29，授权 2019-07-02。
- 核心机制 = 博客里"hot-deployable"的实现细节：
  - 消息系统拆成三个**独立组件进程**：dispatcher（调度器）、connection manager（连接管理器）、message router（消息路由器）；
  - **消息与状态存在独立于进程内存的共享内存**里，组件重启不丢数据；
  - 路由器维护 "touch count"（路由状态），连接管理器维护 "relinquish count"（完成状态），两个计数相等才物理删除消息——**与事件顺序无关的幂等回收**；
  - 结果：可以只升级 router 和 dispatcher，**保持 connection manager 存活，做到不掉线、不丢消息的在线升级**。
- 同族公开版本 US20170257280A1。

**US10263855B2 —— "Authenticating connections and program identity in a messaging system"（消息系统中的连接与程序身份认证）**
- 客户端认证支持证书（公私钥）、从另一台受信消息服务器**转移认证**、或用户名密码；
- 组件程序自身的认证很有意思：dispatcher 把组件实例化时的**操作系统级实例信息（用户身份、进程 ID、创建时间）写入共享内存**，之后比对 OS 报告的实时信息来确认"这个进程还是当初那个进程"——防进程冒充。
- 这正是博客里 "unspoofable user and client identifiers" 的技术底座。

**解读**：BMS ≈ 自研的 "服务发现 + RPC + 消息队列 + 统一鉴权审计 + 热升级" 五合一，时间上早于 Kafka/gRPC/Envoy 的流行；BlackRock 2015 年还在为它申请专利，说明云原生时代它依然是骨干而非遗产。Kubernetes 案例（见 §6）也证实新平台要通过 gateway 接回这条"proprietary message bus"。

---

## 3. 计算架构：风险引擎如何算 1,500 万组合/夜

**本次深挖最大的新增量**：QCon London（2026-03，InfoQ 有完整转录页）BlackRock Director & Technical Fellow **William Chen**（Aladdin Wealth 核心风险计算与优化引擎负责人）的演讲 *"Portfolio Analysis at Scale: Running Risk and Analytics on 15+ Million Portfolios Every Day"*。以下全部为 **[演讲]** 级（本人一手陈述，InfoQ 官方页面）：

### 3.1 规模口径（Aladdin Wealth 一条产品线，非全平台）

- **每晚对 1,500 万+ 组合做全量分析**，风险计算覆盖 **3,000+ 因子**（注意：比官方材料常说的 1,200/2,000+ 又高一档，且这是工程师本人口径）；
- 白天 API 侧每日 **300 万+ 次组合分析**，可持续突发承载 **8,000 组合/分钟**；
- 优化目标：把单次计算从"分钟-小时级压到 1 秒以内"。

### 3.2 三大架构手法（演讲主干）

1. **裁剪计算图（trim the computational graph）**
   - 组合因子风险的核心数学是 **E·C·Eᵀ**（E=敞口矩阵，C=**3,000×3,000 稠密协方差矩阵**，约 900 万元素）；
   - 关键洞察："锁死两个输入节点，下面整棵子图都能锁死"——**收盘后冻结风险参数、因子水平、证券数据，预计算并缓存三个节点：因子协方差、证券敞口、情景模型**，白天交易时段的百万级组合请求全部命中 memoize 的中间结果；
   - 这直接回答了外界一直猜的"Aladdin 怎么算得动"：不是无限堆算力，而是**把与组合无关的昂贵中间量在时间维度上摊销**。
2. **同一份数据按访问模式存多份**（刻意违反"只存一份"的教条）：
   - **Hive**：大规模批处理 join；
   - **Solr**（Lucene 索引）：高并发查询；
   - **Cassandra**：**<100ms 严格 SLA** 的 API 响应；
   - 文件系统：归档与对外 extract。
   - Chen 的原则："选最适合这个查询模式的数据库"，成本是次要的。注意这与 "one source of truth" 不矛盾——主数据仍一份，**派生投影多份**。
3. **三个维度的模块化**：
   - 按"变化"模块化：把数据库选型、框架选型这类难以回头的决策藏进模块边界（经典 Parnas）；
   - 按"能力"模块化：围绕产品积木（组合/证券/组合分析）而非技术分层来切；
   - 按"规模"模块化：读多写少的组合分析（要扩到百万级）与写路径（低量）分开，避免为写路径过度扩容。

### 3.3 落地技术细节（同演讲）

- 技术栈：**Java、Scala、Spring、Spark**；批处理走 **Spark/HDFS**，在线 API 走 **Cassandra/gRPC**，**同一套无状态能力库两处复用**（"如果我换掉 EJML 让它变快，两条部署线一起变快"）；
- 线性代数库演进：**NETLIB BLAS（70 年代 Fortran）→ EJML（Java 矩阵库）**，团队把"稀疏向量×稠密矩阵"优化**贡献回 EJML 上游**，经 maintainer review 后再赚 10% 吞吐；
- 曾把风险引擎**从 Scala 迁回 Java**，为验证迁移前后"每个数字对每家客户完全一致"专门造了数月的数值一致性比对工具；
- SLA 纪律："对 SLA 过度宽容会出大事"——一旦某个超大请求成功过一次，客户就默认它永远能成，所以**限流从第一天就要有**。

**与本项目最相关的迁移点**：收盘后冻结输入→缓存中间结果→白天只做轻量末端计算，这个模式可以直接搬到"每晚预计算因子协方差与个股 beta，白天信号来了秒级算边际风险"。

---

## 4. 数据平台：EDP + Snowflake Aladdin Data Cloud

### 4.1 企业数据平台（EDP）与数据流（[一手]，Snowflake 官方案例页）

- BlackRock 的 **enterprise data platform (EDP)** 集中接入**每晚数百万个文件**（指数供应商、证券主数据供应商等）；
- 数据**落地一次进 Snowflake**，再分发到三类下游：BlackRock 的 **analytics factory**、各客户的本地 Aladdin 环境、以及经 **Aladdin Data Cloud（ADC）** 直达客户——即"落一次、多处派发"的 hub 模式；
- Snowflake 的跨云跨区 **secure data sharing** 是 ADC 的机制基础：客户拿到的不是数据拷贝，而是治理好的共享视图；BlackRock 负责治理与平台运维，客户技术团队只管上层应用；
- 官方规模口径：**116B+ 数据点、150 万+ 按需报表、150+ 机构客户**；
- 引语：Jeff Miller（Global Head of Data Factory & Enterprise Data Platforms）："With Snowflake, we are driving cloud-native data platform transformation across the whole business."
- ADC 于 2021 年与 Snowflake 共同发布（官方新闻稿，[一手]）。

### 4.2 数据工程周边（[一手]，GitHub）

- 开源工具 **`blackrock/ingen`**：YAML 元数据驱动的无代码数据管道工具（pandas + great_expectations），做 CSV 合并、DB 抽取、XML→CSV/Parquet、数据校验——透露其内部大量"低代码配置化 ETL"的工作方式；
- **`blackrock/TopNotch`**（Scala，已归档）：大规模数据质量框架——印证 survey 里 Golub "cleansed data" 哲学有专门的工程投入；
- **`blackrock/xml_to_parquet`**：XML 转 Parquet 工具，暗示大量供应商数据仍是 XML 格式进场。

---

## 5. 云化历程：Azure（主）→ Snowflake（数据）→ AWS（多云）

### 5.1 Azure 迁移（2020-2022）

- 2020-04 官宣战略合作；到 2022-04 约**三分之二客户实例**已迁移，目标 2022-06 完成收尾（[一手] 官方公告 + [媒体] The Stack 财报电话会转述）；
- 迁移工程细节（[一手]，Microsoft 官方客户案例页，2025-09 更新）：
  - Aladdin 心脏是一个"**延迟要求极苛刻的数据库**"（Britt Ewen, MD 原话："We have a very important database at the heart of Aladdin with extremely tight latency requirements"）；
  - 用 **Azure Ultra Disk + M 系列（Mv2，正迁 Mv3）Red Hat Enterprise Linux 虚拟机 + VM Scale Sets + Availability Zones**；
  - 迁移前构建了"最重交易日"的仿真环境，**一年内把生产环境搬进搬出 Azure 十余次**做演练，正式迁移日"安静无聊"；迁移后经历了 Aladdin **史上最大交易量日**，扛住；
  - 正在把数据库向 **Azure SQL Database（hyperscale 层 + elastic pools）** 迁移；Randall Fradin："Now that we're on Azure, we have a springboard to unlock adoption of cloud-managed services."
- 关于那个"心脏数据库"是什么：维基百科技术清单与多个 JD 提到 **Sybase ASE**（[JD推断/媒体]，维基该清单无脚注）；结合"RHEL + Ultra Disk + 正在迁 Azure SQL"的官方描述，"历史核心库为 Sybase ASE、云上逐步换 Azure SQL"是合理拼图，但**无官方直接确认**；
- 成本侧（[媒体]，The Stack 引财报）：迁移期季度费用同比 +12%（$2.3B），Azure 迁移是因素之一；收益侧 Joseph Chalom 原话："**开一个新客户环境从以季度计变成以周计**"。

### 5.2 AWS 合作（2025-12 官宣）

（[一手]，AWS 官方新闻稿 + [媒体] DCD/TradingView 转述）

- Aladdin Enterprise 美国客户的 AWS 版 **GA 预计 2026 下半年**；
- 官方口径三个重点：
  1. Aladdin "**purposefully designed to be cloud-agnostic**"，跨云提供相同功能/性能/安全标准/SLA——多云是产品承诺而非口号；
  2. AWS 客户可以对 Aladdin 托管数据**原生调用 AWS 的 GenAI 与 Analytics 工具**做自助分析（数据引力策略：计算跟着客户数据走）；
  3. 首批标杆客户：**Amazon 自家财资部门（Amazon Treasury）将用 AWS 上的 Aladdin 管理其全球投资组合**（对应 Azure 侧的 Microsoft Treasury 案例——两朵云各"吃自家狗粮"）。
- Sudhir Nair（Aladdin 全球负责人）："The Aladdin platform is built to be multi-cloud, and Aladdin on AWS is a key step…"

### 5.3 自建数据中心的现状

- Uptime Institute 认证记录显示 BlackRock 有**两个自有数据中心：Wenatchee（华盛顿州）与纽约**（[媒体]，DCD 报道引 Uptime）；DCD 明确表示**不确定 Aladdin 目前是否还有负载留在自有机房**——这点公开信息就是模糊的，如实标注。

---

## 6. Kubernetes 与云原生：两个可考的著名案例

### 6.1 "100 天上线"投资者研究平台（2017-2018，[一手] kubernetes.io/CNCF 官方案例）

- 目标：给投资研究人员按需提供 **Python notebooks 和基于 Spark 的 MapReduce 引擎**，不用装桌面软件；
- 20 人跨职能团队（技术/基础设施/生产运维/开发/信息安全），**100 天**从立项到生产（对比语录："光是设备采购有时就要 100 天"）；
- 技术选型：Docker + Kubernetes（经 Red Hat OpenShift）+ Helm；
- **最有价值的架构细节**：K8s 集群不直连内部系统，而是通过一个**带检查与限流的 gateway 程序接入 BlackRock 自研消息总线（即 BMS）**做服务发现——云原生新地盘服从老宪法（§2 的 One BlackRock 规则）；K8s 核心组件也被挂进既有编排/监控框架，不新招运维；
- 踩坑记录：企业防火墙搞坏开源安装脚本（修复回馈社区）、共享集群的"storming herd"资源争抢、多环境消息协议实例不一致；
- Michael Francis 结论语录："你可以把 Kubernetes 集成进既有的、编排良好的机器里，不必推倒重来。"

### 6.2 Argo Events 诞生于 BlackRock（[一手] CNCF 官方博客/报告）

- BlackRock 的 Data Science Platform 原先用原生 K8s CronJob 跑金融研究模型，后来自研了**事件驱动的依赖管理器**，即 **Argo Events**，并**捐给 Argo 项目**（Applatix/Intuit 系）——CNCF 原文："When BlackRock decided to write an event-based system for Kubernetes to fill a gap they saw in the industry, they chose to donate it to the Argo Project"；
- KubeCon NA 2018 有专场演讲：*"Automating Research Workflows at BlackRock"*（Matthew Magaldi & Vaibhav Page，Page 是 **Argo Events 的 co-creator**，录像 https://youtu.be/ZK510prml8o ），内容即从 CronJob 迁到 Argo Events 传感器驱动的研究工作流；
- Argo 整体 2020 年进 CNCF 孵化、**2022-12 毕业**——也就是说，**今天所有用 Argo Events 的公司都在跑一段源自 Aladdin 数据科学平台的代码**。这是"BlackRock 对云原生生态的输出"最过硬的证据。

---

## 7. 语言与技术栈全景

### 7.1 有一手/演讲证据的部分

| 技术 | 用在哪 | 证据与级别 |
|---|---|---|
| **Java** | 风险引擎（Aladdin Wealth）、后端服务主力 | QCon 演讲 [演讲]；BMS 绑定 [一手]；大量 JD [JD推断] |
| **C++** | 性能关键路径：实时风险计算、固收/信用/多资产分析库、合规平台 | BMS 绑定 [一手]；JD（"Security Analytics 团队维护共享分析 C++ 库，覆盖固收分析、风险、定价、组合构建、信用评级、合规"）[JD推断]；NYT 2017 称初始代码 C++/Java/Perl [媒体] |
| **Scala** | Spark 批处理、数据质量框架 TopNotch；风险引擎曾用 Scala 后迁回 Java | QCon 演讲 [演讲]；GitHub [一手] |
| **Python** | 分析层、数据科学平台、对外 SDK 唯一语言 | K8s 案例 [一手]；aladdinsdk [一手]；JD [JD推断] |
| **Julia** | 分析（analytics）模块，2014 起；BMS 有 Julia 绑定 | JuliaHub 案例 [一手]（细节见 §8） |
| **Rust** | 新一代优化算法库（LCSO/HOLA） | 工程博客 + GitHub [一手] |
| **Perl** | 历史代码 + BMS 仍维护 Perl 绑定 | BMS 文章 [一手]；NYT [媒体] |
| **TypeScript + React** | 约 100 个前端应用（Aladdin UI、PM 工具、客户端工具） | 面经指南 [JD推断]；"~100 front-end apps" 出自 2025 会议演讲转述 [演讲] |
| **gRPC** | 在线 API 服务间通信（与 BMS 并存） | QCon 演讲 [演讲] |
| **Spark/HDFS/Hive** | 夜间批量风险计算与大规模 join | QCon 演讲 [演讲]；K8s 案例 [一手] |
| **Cassandra** | <100ms SLA 的在线 API 存储 | QCon 演讲 [演讲]；JD 高频词 [JD推断] |
| **Solr/Lucene、Elasticsearch** | 高并发查询/搜索 | QCon 演讲 [演讲]；JD [JD推断] |
| **Snowflake** | EDP 落地仓 + ADC 对客共享 | 官方案例 [一手] |
| **FDC3** | 前端应用间互操作（金融桌面互操作标准） | 工程博客 2025-10 文章标题《The Interop Advantage: From Friction to Flow with FDC3》[一手]（全文未能抓取，仅标题与摘要） |

### 7.2 JD 高频词归纳（全部 **[JD推断]** 级）

对 careers.blackrock.com 可检索到的 Aladdin Engineering 职位（Java Backend、Application Engineer、Data Platform Cloud/DevOps、C++ Engineer 等，纽约/古尔冈/布达佩斯/普林斯顿）归纳：

- 后端主力画像：**Java + Spring**，"用 Cassandra 等 NoSQL 构建分布式应用"、"接触过 Kafka 等高吞吐分布式技术"、"SQL 与 NoSQL（Cassandra、Elasticsearch、Apache Ignite）"；
- 云：Azure 为主、兼提 AWS/GCP；Docker/Kubernetes 常规要求；
- C++ 岗集中在 Security Analytics / Trade Capture / Investment Compliance——即**定价分析库、交易捕获、合规引擎这些老核心还在 C++ 手里**；
- 面经侧（techinterview.org 汇总）："Heavy C++ and Python; some Scala. 性能关键组件（组合实时风险计算）是 C++，分析层是 Python"；新服务出现 Go；
- 有第三方对 2024-25 实习 JD 的词频分析称 Python/SQL 提及量较 2020 增加 40%（单一来源，[低]）。
- **JD 里没有出现的**：Julia（见 §8）、Perl（存量不招新）。

### 7.3 维基百科技术清单的处理

维基百科列了 Linux、Hadoop、Docker、Kubernetes、Zookeeper、Splunk、ELK、Apache、Nginx、Sybase ASE、Cognos、FIX、Swift 对象存储、REST、AngularJS 等——**该清单在维基条目中无脚注**，多半来自历年 JD 与 stackshare 类站点的汇编，整体按 **[JD推断/溯源不明]** 对待。其中 Linux（Azure 案例证实 RHEL）、Hadoop/K8s/Docker（上文一手证实）、FIX（交易行业必然）可信；AngularJS 应为历史栈（现为 React/TS）；Cognos/Swift 存储无从核实。

---

## 8. Julia 使用范围核实（专项）

任务点：Julia 到底用在哪、现在还在不在用。

**能确证的（[一手]，JuliaHub 官方案例页）**：
- BlackRock 的 quants **2014 年开始用 Julia**；
- 用途表述始终是一句话级别："为 Aladdin 写了 **analytics modules**"、用于 **time series data analytics 和大数据应用**；看中三点：性能、易用、**单语言部署**（原型即生产，不用 Python 原型→C++ 重写）；
- JuliaCon 2015 白金赞助商并做了演讲；2017 年 NYT 报道 Aladdin 的 Julia 使用（JuliaHub 转载页存证）。
- **BMS 官方维护 Julia 客户端绑定**（BMS 博客，[一手]）——说明 Julia 代码真实接入生产消息总线，不是实验室玩具。

**范围限定的证据（本次新发现）**：
- 工程博客《Writing an Optimization Library in Rust》（2021-05，[一手]）披露：税务感知组合构建（tax-aware portfolio construction，BlackRock AI Labs 2020 年与 Boyd 等人的论文）项目**先用 Julia 写了原型**，理由是"表达力 + 接近 C/C++ 的速度"，**但生产实现选择了 Rust**（借用检查带来的内存安全），产出开源库 **`blackrock/lcso`**（ADMM 变体，121 星）；
- 也就是说至少在 2020-2021 的这条产品线上，Julia 的角色是**原型语言**，生产另选。

**"现在是否还在用"**：
- 检索 2024-2026 年 BlackRock JD，**未发现任何提及 Julia 的职位**（多轮检索，Aladdin analytics 岗要求集中在 C++/Python/Java）[JD推断]；
- JuliaHub 案例页仍在线但内容停留在 2015-2017 年的事实（页面甚至还写着"管理近 $5 万亿资产"的旧口径）。

**结论**：Julia 在 Aladdin 中的确证使用 = **2014 年起的部分 analytics 模块 + 时间序列分析**，且深度到有 BMS 官方绑定；但**没有任何近年（2022+）一手证据表明其使用面扩大**，招聘信号为零，且已有"原型 Julia→生产 Rust"的反例。合理判断：**存量维持、增量停滞**（此判断本身为推断级，如实标注）。survey 初版"金融业最著名的 Julia 生产用户"说法成立，但时态应为过去-现在完成时，不宜引申为"核心引擎是 Julia"。

---

## 9. 开放平台：Aladdin Studio / Graph API / AladdinSDK

### 9.1 接口体系（[一手]，GitHub `blackrock/aladdinsdk` 源码 + README + PyPI）

AladdinSDK（Apache 2.0，纯 Python，2024-05 开源，官方博文《Open Sourcing the AladdinSDK》）是目前**唯一能直接读到源码的 Aladdin 官方组件**，从中能反推平台接口设计：

- SDK 统一封装三个入口：
  1. **Aladdin Graph APIs**：REST 风格，`AladdinAPI` 客户端**基于 swagger/OpenAPI 规范自动生成**；API 注册命名规范为 `agraph.<domain>.<segment>.<api group>.<version>.<API Name>`，即**领域→业务段→API 组→版本**的四级命名空间——透露 Graph API 是按业务域联邦治理的大目录，而非单一 graph；
  2. **Aladdin Data Cloud（ADC）**：`ADCClient` 直接是 **Snowflake connector / Snowpark 的包装**（依赖 snowflake-connector-python），证实 ADC="治理过的 Snowflake"；
  3. **S3 兼容对象存储**：`S3Client` 用于项目工作区文件。
- 认证：API 走 Basic token 或 **OAuth（refresh_token / client_credentials）**；ADC 走 OAuth 或 **RSA key（snowflake_jwt）**；本地开发凭据入 OS keyring；
- 配置系统：dynaconf，YAML/JSON + `ASDK_` 前缀环境变量四级覆盖（default→用户文件→环境变量→inline）；
- **插件架构**：领域能力以可安装插件分发（`asdk_plugin_trading`、`asdk_plugin_investment_research`），另有 `aladdinsdk-plugin-builder` 脚手架 repo；还明确支持客户在 SDK 之上再造 **DomainSDK**（配置隔离 + 自定义 metrics）；
- 工程质感：内置 retry（tenacity）、批处理、统一日志/错误处理——是给买方 quant 用的"厚 SDK"，不是裸 REST 包装。

**设计思想提炼**：对外开放 = **OpenAPI 目录（Graph API）+ 托管数仓（ADC/Snowflake）+ 薄封装厚约定的官方 SDK + 插件式领域扩展**。BlackRock 没有开源任何计算引擎，开源的是**接入层**——护城河在数据和引擎，接口越开放粘性越强。

### 9.2 开源足迹总览（[一手]，GitHub org `blackrock`）

| Repo | 语言 | 内容 | 信号 |
|---|---|---|---|
| lcso（121★） | Rust | ADMM 变体的线性约束可分优化 | 优化器技术路线 Rust 化 |
| aladdinsdk（63★） | Python | 官方 SDK | 见上 |
| TopNotch（41★，archived） | Scala | 大数据质量框架 | 数据质量有专门框架 |
| xml_to_parquet（38★） | Python | XML→Parquet | 供应商数据管道日常 |
| HOLA（32★） | Rust+Python | 轻量异步超参优化 | AI Labs 产出 |
| ingen（20★） | Python | YAML 配置化 ETL | 低代码数据管道文化 |
| blowfish（3★） | Python | 语义搜索歧义量化（配套 2024-06 博文） | 内部搜索/RAG 有量化评估意识 |
| （外部）argo-events | Go | 捐给 CNCF Argo | 云原生输出，§6.2 |

### 9.3 工程博客文章目录（截至 2025-10 可见部分，[一手]标题级）

近年可见文章：FDC3 互操作（2025-10）、AI 与工程（2024-10）、慢哈希表之谜（2024-07，性能排障向）、Blowfish 语义搜索（2024-06）、开源 AladdinSDK（2024-05）、Python 并发"像章鱼一样思考"（2024-03）、InGen（2024-01）、相似度学习（2023-11）、Rust 优化库（2021-05）、Application Scaling with Raft（Davis Nguyen，日期不详——**说明内部有服务用 Raft 做一致性/扩展**）、Telemetry and Observability at BlackRock（存在但全文抓取失败，无法给出其可观测性栈细节，如实说明）、resource-oriented design 的边界、BMS（§2）。整体主题分布：**消息/一致性/可观测性等平台工程 + AI/搜索 + 开源发布**，几乎不谈金融模型本身——模型是商业机密，平台工程可以对外。

---

## 10. AI 架构：Aladdin Copilot 的工程细节

（来源：2025 年某会议上 BlackRock AI 工程负责人 Brennan Rosales 与 Principal AI Engineer Pedro Vicente Valdez 的演讲，经 ZenML LLMOps Database 收录转述——**[演讲/转述]** 级，非官方文档）

- 组织背景口径：Aladdin 组织 **7,000 人，其中约 4,000 工程师**，维护**约 100 个前端应用**；
- 架构选择：**中心化 supervisor 式 agent 架构**（而非自治 agent 互联），理由是金融系统的可靠性与可测试性；
- **插件注册表（Plugin Registry）**：50-60 个领域工程团队通过两条路径贡献能力——直接把 API 映射成工具，或注册自定义 agent（联邦式开发模型，和 Graph API 的领域命名空间一脉相承）；
- 查询流水线（基于 **LangGraph**）：上下文收集（应用状态/用户屏幕内容/偏好）→ 输入护栏（内容审核、PII 检测）→ **工具过滤（从 1,000+ 工具筛到 20-30 个）**→ GPT-4 function calling 做规划与执行循环 → 输出护栏（幻觉检测、领域审核）；
- 模型：**OpenAI GPT-4**（Azure OpenAI，与 2023 年官方公告一致 [一手]）；框架 LangChain/LangGraph；评估用 LLM-as-judge，**"evaluation-driven development"：每个 PR 跑评估，开发环境每日全量跑**；
- 官方公告侧（[一手]，Microsoft/BlackRock 2023-2024）：Copilot 2023 年发布、2024-09 向客户开放，基于 Azure OpenAI。

**对本项目的启示**：1,000+ 工具先过滤到 20-30 再交给模型，这个"tool filtering 前置"的做法对我们的多工具 RAG agent 直接适用。

---

## 11. 专利盘点

在 patents.google.com / USPTO 公开检索（patents.justia.com 对抓取返回 403，未能拿到完整清单，以下为逐条核实过原文的部分，**均 [一手] 专利原文**）：

| 专利号 | 标题 | 受让人 | 状态 | 核心内容 |
|---|---|---|---|---|
| **US7403918B2** | Investment portfolio compliance system | BlackRock Inc. | 2008 授权，2025-12 到期 | 合规规则引擎：规则引用**动态列表数据库**（如"烟草公司"名单），改名单不改规则；前端（pre-trade，下单前拦截）+ 后端（post-trade，市况变化后持仓复查）双通道；支持 CUSIP + 布尔条件识别证券。Aladdin 嵌入式合规的专利底座 |
| **US20050267835A1** | System and method for evaluating exposure across a group of investment portfolios by category | BlackRock Inc. | 申请后放弃 | 跨组合按属性（发行人/行业/币种/信用/证券类型）多级分类聚合敞口，绝对+相对基准双视角；架构披露：GUI + 组合/证券/交易三库 + **分析服务器集群 + control/cache server 做负载均衡**（2004 年就有"控制+缓存服务器"分层）；实现语言注明 HTML、C++、Java |
| **US20050273422A1** | System and method for managing credit risk for investment portfolios | BlackRock Inc. | 申请（未确认授权） | 组合信用风险管理（未逐条核实权利要求） |
| **US10341196B2** | Reliably updating a messaging system | BlackRock Financial Management | 2019 授权 | BMS 热升级机制，详见 §2.2 |
| **US10263855B2** | Authenticating connections and program identity in a messaging system | BlackRock Financial Management | 授权 | BMS 连接与进程身份认证，详见 §2.2 |
| **US12067619B1 / US12118614B1 / US12567110** | Systems and methods for electronic trade order routing（同族三件） | BlackRock Inc. | 2024-2025 授权 | **ML 交易路由**：对 IG（投资级）公司债订单，用历史交易成本（以 implementation shortfall 基点衡量）训练的模型，在交易台 dashboard 新增一列**执行方式建议**——自动执行 / RFQ / 人工电话三选一；可全自动亦可仅建议（保留人工否决）。Aladdin 交易台 AI 化的直接证据 |

**勘误与提示**：检索中常被归到 BlackRock 名下的 US20030009408A1（"经网络 API 提供组合风险测算"）实际受让人是个人发明者 Ittai Korin，**并非 BlackRock**，引用时注意。另有 BlackRock Institutional Trust（原 BGI）名下若干 ETF 结构/指数跟踪专利（如 US7689493），属产品结构而非系统架构，本文不展开。

**总体观察**：BlackRock 的专利策略明显**克制**——三十多年的系统只有个位数核心系统专利，且集中在两块：消息中间件（守住 BMS）与合规/路由（守住工作流卖点）。风险模型、定价引擎**零专利**（申请专利=公开方法，宁可当商业秘密）。2004-2005 年的两件架构专利申请后主动放弃，也符合"公开换保护不划算"的判断。

---

## 12. 流传数字逐条溯源

| 流传数字 | 原始出处核实结果 | 判定 |
|---|---|---|
| "6,000 台计算机" | **找到原始出处**：The Economist 2013-12《The monolith and the markets》原文 "a cluster of 6,000 computers"，地点 **East Wenatchee, Washington**（经转载全文核实原句）。维基百科的同款表述**无脚注**，实为转述 Economist | **[媒体]**，2013 年时点数据，今天引用必须带年份；云迁移后此数字已失去现实意义 |
| "30,000 个投资组合" | 同上 Economist 2013 原文 "Aladdin keeps track of 30,000 investment portfolios" | **[媒体]**，2013 时点 |
| "约 2,000 名运维/开发人员" | 同上 Economist 2013 "nearly 2,000 employees who run it"；2025 演讲口径已是 7,000 人 Aladdin 组织 | **[媒体]**，2013 时点 |
| "17,000 名交易员接入" | 同上 Economist 2013 | **[媒体]**，2013 时点 |
| "每天监控 2,000+ 风险因子、每周 5,000 次组合压力测试、1.8 亿次 OAS 计算" | 多个二手源（Queen's Business Review 等）一致引用，指向 **BlackRock 官方 Aladdin Risk 产品页**（blackrock.com/aladdin/benefits/risk-managers）；本次抓取该页返回 403，**未能直接验证页面现文**。未找到更早的独立原始出处 | **[官方营销口径，间接核实]**；且为营销页数字，无统计方法说明；QCon 演讲的一手口径（单产品线 3,000+ 因子、1,500 万组合/夜）实际上已超过它 |
| "每天数十亿次计算" | 未找到任何可署名的原始出处，疑为对上述口径的媒体演绎 | **[溯源不明的流传数字]**，不建议引用 |
| "数千核网格计算" | 未找到原始出处；与 Economist 的"6,000 computers"可能同源讹变 | **[溯源不明]**，不建议引用 |
| "监控资产 $21.6T（2020）/$25T（近年）" | 2020 数字出自 Business Insider（维基脚注），近年 $25T 出自 BlackRock 官方 API 页与新闻稿 | **[媒体/官方]**，注意口径是"平台上处理的资产" |
| "15+ 百万组合/每晚全量风险计算" | QCon London 2026，William Chen 本人演讲（InfoQ 收录） | **[演讲]**，目前公开渠道最硬的计算规模口径，建议以此替代旧流传数字 |

---

## 13. 可靠性与故障记录

- **公开可查的 Aladdin 重大宕机事件：未找到。** 多轮检索（outage/downtime/incident/traders unable 等组合）只命中两类内容：(a) 讨论"如果 Aladdin 挂了会怎样"的系统性风险评论（如"240+ 机构将无法交易/看风险/交监管报表"——均为假设句式）；(b) 监管侧对单点依赖的担忧报道。**没有任何一篇具体事故报道**。可能的解释：确实没出过对外可见的大事故，或客户实例彼此隔离使故障不成新闻；无法区分，如实记录 **[挖不到]**；
- 侧面的可靠性工程证据：BMS 热升级专利（不停机更新消息总线，§2.2）；Azure 迁移前一年内生产环境进出十余次演练（§5.1）；QCon 演讲中的 SLA/限流纪律（§3.3）；工程博客有《Telemetry and Observability at BlackRock》与《Application Scaling with Raft》两文（全文未能抓取，仅存目）；
- 第三方技术报（Quastor）有一篇《Reliability Engineering at BlackRock》，本次三次抓取均失败（socket closed），未能纳入，[挖不到] 如实说明。

---

## 14. 附：几个未归类但值得存档的碎片

- **Aladdin Trader**：WatersTechnology 2023 年报道称 BlackRock 在向客户推介一个名为 "Aladdin Trader" 的固收交易工具（自动执行方向），但功能细节对外始终模糊（标题即 "still a mystery"）。与 §11 的 ML 交易路由专利族（IG 公司债自动执行/RFQ/人工三分路由）时间与方向吻合，可互为佐证但无官方确认。**[媒体+专利旁证]**
- **Microsoft Treasury 反向案例**：微软自家财资部门（管理公司金库）是 Aladdin 客户（BlackRock 官网有专页案例）——与 2025 年 Amazon Treasury 用 AWS 版 Aladdin 形成镜像：两大云厂商既是 Aladdin 的底座供应商又是其客户。**[一手，官网案例页]**
- **API 目录的外部痕迹**：apis.io / API Evangelist 收录了 "Aladdin Studio APIs" 的 schema 目录条目，说明其 OpenAPI 规范曾对外可发现；aladdinsdk 同步发布在 PyPI（`pip install aladdinsdk`），面向买方客户的 quant 而非仅内部。**[一手]**
- **"Never Done" 工程审美**：BlackRock 官网有 Aladdin 工程故事音频栏目（Never Done Audiocast），属雇主品牌内容，技术含量低，仅存目。**[官方，营销级]**
- **2019 校招手册 PDF**（static.wcn.co.uk 的 Technology.pdf）：抓到文件但文本层无法解析（扫描/压缩流），未能利用，[挖不到] 如实说明。

---

## 15. 对本项目可搬运的工程模式（速记）

从本次深挖新增资料中提炼、且初版 survey 未覆盖的工程模式：

1. **收盘冻结 + 计算图缓存**（§3.2）：每晚行情落库后，预计算并固化"因子协方差矩阵 + 个股因子暴露"两个中间量；白天任何信号触发的组合风险测算都只做末端的 E·C·Eᵀ 轻量运算。对应 survey 提案 1 的性能设计——不必等组合变大才做，从第一天就把"慢变量预计算、快变量现算"分开。
2. **主数据一份、投影多份**（§3.2）：SQLite 主库保持唯一事实，但允许为 digest/回测/RAG 各自导出最适合其查询模式的派生形态（如回测用 parquet 宽表、RAG 用向量库），只要派生链路是单向可重建的，就不违反 one source of truth。
3. **消息层统一挂身份**（§2）：BMS 把"谁发起的"做成不可伪造的消息属性。个人系统的对应物：所有信号/调仓建议入库时强制带 `source + generated_by + trace_id`，事后归因（提案 3 的 signal_performance）才有可靠地基。
4. **SLA 从第一天限流**（§3.3）：Chen 的教训直接适用于我们的抓取器——某数据源某次扛住了大批量，调用方就会永远按那个量来；fetcher 的限速不是性能问题而是契约问题。
5. **接口开放、引擎闭源**（§9）：AladdinSDK 的启示是开放层要"厚约定"（统一配置、重试、认证、日志），而把真正值钱的计算留在服务端。若未来把本项目做成对外服务，SDK 形态照抄这个结构即可。
6. **工具先过滤再给模型**（§10）：Copilot 从 1,000+ 工具筛到 20-30 个才进 LLM 上下文。我们的 agent 工具目录增长后应引入同样的 pre-filter 层，而不是把全部工具 schema 塞进 prompt。

---

## 16. 相对初版（survey_aladdin.md 2.1 节）的增量清单

1. BMS 从一句话扩成完整机制：三组件分离 + 共享内存 + 双计数回收的**热升级专利级细节**（US10341196B2），七语言官方绑定名单，消息层统一鉴权（US10263855B2）；
2. 新增**计算架构一手口径**：QCon 2026 演讲——1,500 万组合/夜、3,000+ 因子、E·C·Eᵀ、收盘冻结+计算图缓存、Hive/Solr/Cassandra 多引擎、EJML 上游贡献、Scala→Java 回迁；
3. 云迁移从"迁了"细化到"怎么迁的"：Mv2/Mv3 RHEL + Ultra Disk + 十余次生产演练 + Azure SQL hyperscale 演进 + 开环境从季度到周；AWS 侧确认多云为产品承诺、Amazon Treasury 为首批客户；
4. 确认 **Argo Events 诞生于 BlackRock 并捐给 CNCF**（现已随 Argo 毕业）；
5. Julia 定位修正：确证 2014 年起用于 analytics 模块且有 BMS 绑定，但发现"Julia 原型→Rust 生产"反例，近年 JD 零提及，判定存量维持；
6. AladdinSDK 源码级接口设计解读（Graph API 四级命名空间、ADC=Snowflake 包装、插件/DomainSDK 体系）；
7. 专利盘点 7 件（含勘误一件非 BlackRock 专利），发现 2024-25 年 **ML 交易路由专利族**；
8. "6,000 台计算机"等流传数字全部溯源到 The Economist 2013 原文或标注溯源不明；
9. Aladdin Copilot 补充 supervisor 架构、LangGraph、工具过滤、评估驱动开发等演讲级细节。

---

## 17. 来源与可信度汇总

**一手（工程博客 / 专利 / 官方公告 / 源码 / 厂商案例页）**
- [The BlackRock Messaging System — BlackRock Engineering (Medium)](https://medium.com/blackrock-engineering/the-blackrock-messaging-system-aeae461e4211)（全文受 Medium 重定向限制，正文片段经索引摘要交叉核实）
- [US10341196B2 可靠更新消息系统](https://patents.google.com/patent/US10341196B2/en) ｜ [US10263855B2 消息系统认证](https://patents.google.com/patent/US10263855B2/en) ｜ [US7403918B2 组合合规系统](https://patents.google.com/patent/US7403918B2/en) ｜ [US20050267835A1 跨组合敞口](https://patents.google.com/patent/US20050267835) ｜ [US12067619B1 交易路由](https://patents.google.com/patent/US12067619B1/en)
- [Kubernetes 官方案例：BlackRock 100 天](https://kubernetes.io/case-studies/blackrock/) ｜ [CNCF: Rolling out Kubernetes in 100 days](https://www.cncf.io/blog/2018/01/19/rolling-kubernetes-100-days-blackrock/) ｜ [CNCF Argo 毕业公告（Argo Events 来自 BlackRock）](https://www.cncf.io/announcements/2022/12/06/the-cloud-native-computing-foundation-announces-argo-has-graduated/)
- [Microsoft 客户案例：Aladdin 与 Azure Ultra Disk](https://www.microsoft.com/en/customers/story/25275-blackrock-financial-management-azure-ultra-disk-storage) ｜ [Snowflake-BlackRock 案例（EDP/ADC 架构）](https://www.snowflake.com/en/customers/all-customers/case-study/blackrock/) ｜ [AWS 新闻稿 2025-12](https://press.aboutamazon.com/aws/2025/12/blackrock-partners-with-aws-to-deliver-aladdin-on-secure-scalable-cloud-infrastructure)
- [GitHub blackrock/aladdinsdk](https://github.com/blackrock/aladdinsdk) ｜ [blackrock/lcso](https://github.com/blackrock/lcso) ｜ [blackrock/ingen](https://github.com/blackrock/ingen) ｜ [GitHub org](https://github.com/blackrock)
- [工程博客：Writing an Optimization Library in Rust](https://medium.com/blackrock-engineering/writing-an-optimization-library-in-rust-588628c0e500) ｜ [Open Sourcing the AladdinSDK](https://engineering.blackrock.com/open-sourcing-the-aladdinsdk-empower-python-developers-with-a-quantitative-edge-7f63376061e6) ｜ [博客文章总目录（blackrock-eng.medium.com）](https://blackrock-eng.medium.com/)
- [JuliaHub 官方案例：Analytics for BlackRock](https://juliahub.com/case-studies/blackrock)
- [Microsoft Cloud Blog：BlackRock AI/Copilot 2024](https://www.microsoft.com/en-us/microsoft-cloud/blog/financial-services/2024/09/30/elevating-investment-management-tech-ai-powered-leadership-from-blackrock-and-microsoft/)

**大会演讲**
- [InfoQ/QCon London 2026：Portfolio Analysis at Scale（William Chen）](https://www.infoq.com/presentations/portfolio-analysis-scale/) —— 本文计算架构一节的主要来源
- [KubeCon NA 2018：Automating Research Workflows at BlackRock](https://kccna18.sched.com/event/GrS4/automating-research-workflows-at-blackrock-matthew-magaldi-vaibhav-page-blackrock)（[录像](https://youtu.be/ZK510prml8o)）
- [ZenML LLMOps DB：Aladdin Copilot agentic 架构（2025 演讲转述）](https://www.zenml.io/llmops-database/agentic-ai-architecture-for-investment-management-platform)

**JD / 面经推断**
- careers.blackrock.com 各 Aladdin Engineering 职位（Java/C++/Data Platform 等，页面易失效，正文按语义归纳）
- [techinterview.org BlackRock 面试指南](https://www.techinterview.org/companies/blackrock-interview-guide/)

**媒体 / 转述**
- The Economist 2013-12《The monolith and the markets》（原刊付费，经 [Bamboo Innovator 转载全文](https://bambooinnovator.com/2013/12/08/blackrock-the-monolith-and-the-markets-getting-15-trillion-in-assets-on-to-a-single-risk-management-system-is-a-huge-achievement-is-it-also-a-worrying-one/) 核对原句）—— 6,000 台计算机等数字的原始出处
- [The Stack：Azure 迁移与费用](https://www.thestack.technology/blackrock-aladdin-azure-migration-earnings-call/) ｜ [DCD：Aladdin 上 AWS 与数据中心现状](https://www.datacenterdynamics.com/en/news/blackrock-brings-aladdin-investment-platform-to-aws/) ｜ [Wikipedia: Aladdin (BlackRock)](https://en.wikipedia.org/wiki/Aladdin_(BlackRock))（技术清单无脚注，降级使用）

**明确挖不到 / 未能核实**
- Aladdin 公开宕机事故报道：零命中
- 《Telemetry and Observability at BlackRock》《Application Scaling with Raft》全文（Medium 墙）；Quastor《Reliability Engineering at BlackRock》（连接失败）
- blackrock.com/aladdin/benefits/risk-managers 页面现文（403），"1.8 亿 OAS/周"仅二手交叉
- 风险模型方法论（协方差估计、蒙特卡洛路径数、定价库实现）依旧零公开文献——与初版结论一致
