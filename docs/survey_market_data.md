# 行情/基本面数据源调研与选型建议

最后更新：2026-06-30

本文调研 A股/美股/指数/加密 行情、历史 K 线、基本面与财务数据的开源 Python 方案，
评估本项目当前「直连新浪/东方财富裸接口 + akshare 兜底」的做法，并给出迁移建议。

> 评估对象：`x_agent/finance_fetcher.py`、`x_agent/factor_model.py`，依赖见 `requirements.txt`。

---

## 一、当前做法快照

`finance_fetcher.py` 的设计原则写在模块 docstring 里：**所有数据源都必须「国内可访问、免鉴权、免费」**。
这是它一系列「奇怪」选择的根因（用东方财富取**美股**、用 gate.io 取加密），评估时必须把这条放在第一位——
一个 star 再高但在国内被墙的库，对本项目等于不可用。

| 数据 | 当前来源 | 实现方式 | 对应函数 |
|------|---------|---------|---------|
| A股实时行情 | 新浪 `hq.sinajs.cn` | 裸 `requests` + 正则解析 GBK 文本 | `fetch_a_shares` / `get_a_share_quote` |
| A股日 K 线 | baostock | SDK 登录后查询 | `_kline_a_shares` |
| 美股实时/ K 线 | 东方财富 `push2/push2his` | 裸 `requests` + 猜 secid 市场号(105/106/107) | `fetch_us_stocks` / `_fetch_us_one` / `_kline_us_stocks` |
| 指数实时/ K 线 | 东方财富 `push2` | 裸 `requests`，secid 写在 `config.yaml` | `fetch_indices` / `_kline_indices` |
| 加密实时/ K 线 | gate.io API v4 | 裸 `requests` | `fetch_crypto` / `_kline_crypto` |
| A股基本面(市值/PB/PE) | akshare `stock_zh_a_spot_em` | 已用 akshare | `AKShareClient.fetch_fundamentals` |
| 季报营收/现金流 | akshare `stock_financial_analysis_indicator_em` | 已用 akshare | `AKShareClient.fetch_quarterly_financials` |
| 龙虎榜/北向资金 | akshare | 已用 akshare | `get_dragon_tiger` / `get_north_flow` |

也就是说：**项目已经一脚踏进 akshare**（基本面、财务、龙虎榜、北向全靠它，并作为 A股实时的 fallback），
只有「A股实时、美股、指数」三条线还在裸调 Sina/EM。

### 现状的两个事实性问题（与库选型无关，先记录）

1. **运行环境是 Python 3.14.5**（`.venv` 由 uv 创建），并非常被误传的 3.8。`requirements.txt` 自己也佐证：
   它钉了 `scikit-learn==1.9.0`、`toraniko==1.1.1`(Polars)、`cvxpy==1.9.2`，这些在 3.8 上根本装不上，
   注释里还写「torch/easyocr 暂无 Python 3.14 支持」。**结论：不存在 3.8 兼容性约束，akshare 可自由升级。**
2. **`requirements.txt` 第 40 行裸写 `akshare`、第 47 行又钉 `akshare==1.18.64`**，重复且自相矛盾，应合并。
   顺带说明：`1.18.64` 其实就是当前 PyPI 最新版（`requires-python>=3.9`），所以这个「钉版本」并没有锁在旧版本上。

---

## 二、候选库横评

GitHub 数据采集于 2026-06-30（GitHub API）。「国内可用」指无需翻墙、免 token 即可拉到数据。

| 库 | star / 维护 | 覆盖 | 免费? | 国内可用 | 稳定性 | License |
|----|-----------|------|-------|---------|--------|---------|
| **AKShare** (akfamily) | 20.8k ★ / 活跃(2026-05) | A股+美股+指数+期货+基金+财务，最全 | 免费免 token | 是 | 中：封装 Sina/EM/同花顺，字段变了社区跟着修 | MIT |
| **Tushare Pro** (waditu) | 15.2k ★ / 服务在线* | A股行情+财务，质量高、规整 | token+积分制 | 是 | 高：服务端自产数据 | BSD-3 |
| **adata** (1nchaos) | 4.9k ★ / 活跃(2025-12) | 专注 A股：行情/K线/概念/资金 | 免费免 token | 是 | 中高：多源融合+自动代理切换，抗封锁设计 | Apache-2.0 |
| **efinance** (Micro-sheep) | 3.8k ★ / 活跃(2026-03) | A股+基金+债券+期货(封装 EM) | 免费免 token | 是 | 中：单一封装 EM | MIT |
| **easyquotation** (shidenggui) | 5.3k ★ / 活跃(2026-02) | 仅实时行情(Sina/腾讯/集思录) | 免费免 token | 是 | 中：仅快照，无历史/基本面 | MIT |
| **qstock** (tkfy920) | 1.9k ★ / 半活跃(2025-03) | A股行情+选股+回测(封装 EM/同花顺) | 免费免 token | 是 | 中 | MIT |
| **Ashare** (mpquant) | 3.6k ★ / 活跃(2025-12) | 仅 A股 K线/分时(Sina+腾讯双源切换) | 免费免 token | 是 | 中：极简单文件，无基本面 | 无 license |
| **baostock**(现用) | 官网服务/无活跃 repo | A股日/分钟 K线+部分财务，无实时 | 免费免 token | 是 | 偏低：偶有更新延迟、无实时、复权口径有限 | — |
| **pytdx** (rainx) | 1.5k ★ / **已归档(2020)** | 通达信协议：行情/K线 | 免费 | 是 | 低：已停更（继任者 eltdx/rustdx） | 无 |
| **yfinance** (现用) | 24.5k ★ / 非常活跃 | 美股/全球，无 A股 | 免费免 token | **否(国内不稳/易被限)** | 中，但对本项目网络不可靠 | Apache-2.0 |

\* `waditu/tushare` 这个 GitHub 仓库最后提交是 2024-03，但那是**本地客户端 SDK**；Tushare Pro 是在线托管服务，
数据仍在持续更新，不能据 repo 提交时间判定其「停更」。

### 关键差异点

- **AKShare 本质上封装的就是项目现在裸调的那批 Sina/EM 接口**。所以迁移它的价值**不是**「协议层更稳」，
  而是「把接口字段变更、市场号猜测、反爬 header 这些维护成本外包给一个 2 万星的社区」——EM 改字段时，是 akshare 替你修，而不是你半夜改正则。这一点要说清楚，否则懂行的人会觉得在夸大其词。
- **Tushare Pro 的 A股日线(`daily`)只要 120 积分**（注册即得，免费），但**财务指标 `fina_indicator` 要 2000 积分**。
  本项目 `factor_model.py` 的 Value 因子恰恰依赖营收/现金流/PB/市值这些财务数据——
  Tushare 把它们卡在 2000 积分门槛后，而 **akshare 的同类财务接口完全免费免门槛**。这是偏向 akshare 的一个硬理由。
- **yfinance 已在 `requirements.txt`(line 20)**，但美股路径却用东方财富——几乎可以肯定是因为 yfinance 在国内不稳/被限。
  因此**不建议**把美股切到 yfinance。
- pytdx 已归档(2020)，不予考虑。

来源：
- AKShare: <https://github.com/akfamily/akshare> ・ <https://pypi.org/project/akshare/>
- Tushare 积分/频次: <https://tushare.pro/document/1?doc_id=290> ・ 财务指标 2000 积分 <https://tushare.pro/document/2?doc_id=79> ・ daily <https://tushare.pro/document/2?doc_id=27>
- adata: <https://github.com/1nchaos/adata>
- efinance: <https://github.com/Micro-sheep/efinance>
- easyquotation: <https://github.com/shidenggui/easyquotation>
- qstock: <https://github.com/tkfy920/qstock> ・ Ashare: <https://github.com/mpquant/Ashare>
- baostock: <https://www.baostock.com/> ・ yfinance: <https://github.com/ranaroussi/yfinance>

---

## 三、对当前裸接口做法的评估

### 优点
- **零依赖、零鉴权、低延迟**：直连 Sina/EM 比走 akshare 少一层封装，实时快照更快、依赖更少。
- **完全符合「国内可访问、免费」原则**，且每个数据源独立 try/except，单源失败不阻塞整体。
- 项目已经做了正确的分层：实时走裸接口、失败 fallback 到 akshare。

### 缺点 / 风险
- **脆弱**：裸接口靠硬编码字段位置和正则解析非公开接口（`hq_str_*` 文本、EM 的 `f43/f44...` 数字字段、价格 ×1000 的隐式约定）。EM/Sina 一旦调整字段顺序、编号或反爬策略，解析**静默出错或返回脏数据**，且只有运行时才暴露。
- **美股市场号靠猜**：`_fetch_us_one` 顺序试 105/106/107，既慢又可能误配。
- **baostock 偏弱**：无实时、更新偶有延迟、复权口径有限，作为唯一 A股 K线源不够稳。
- **维护成本压在自己身上**：接口变更没有社区缓冲，全靠本仓库自己跟修。

---

## 四、建议

**结论：保留现有多源架构与「国内可访问」原则，不引入 Tushare Pro / yfinance；
把 A股实时、A股 K线、美股、指数 四条裸接口线统一收敛到 akshare 作为「主源」，
保留现有裸 `requests` 路径作为「快速 fallback」。加密继续走 gate.io。**

理由：
1. **项目已大量依赖 akshare**（基本面/财务/龙虎榜/北向），再收编这几条线只是把数据层统一，不新增重型依赖、不破坏架构。
2. **维护成本外包**：Sina/EM 接口变更交给 2 万星社区跟修，消除「半夜改正则」风险。
3. **财务数据免费**：相比 Tushare 把财务卡在 2000 积分，akshare 让 `factor_model.py` 的 Value 因子数据零门槛获取。
4. **环境无障碍**：运行在 Python 3.14，akshare 最新版 `requires>=3.9`，无兼容问题；只需清理 `requirements.txt` 的重复声明。

不建议的选项：Tushare Pro（token+积分，财务门槛高）、yfinance 取美股（国内网络不稳）、pytdx（已归档）。
adata 是有吸引力的 A股专用备选（自带代理切换、抗封锁），可作为 akshare 之外的**第二 fallback**纳入观察，但暂不必首发引入。

### 迁移工作量（中等，按函数）

| 函数 | 改法 | 工作量 |
|------|------|-------|
| `_kline_a_shares` | baostock → `ak.stock_zh_a_hist`（前复权日线），干掉 baostock 登录/登出 | 低，收益最大（去掉一个弱依赖） |
| `fetch_a_shares` / `get_a_share_quote` | 裸 Sina 设为优先、akshare 兜底的逻辑已存在，整理即可；或反过来以 `stock_zh_a_spot_em` 为主 | 低（fallback 已写好） |
| `fetch_us_stocks` / `_fetch_us_one` / `_kline_us_stocks` | EM 裸接口 → `ak.stock_us_spot_em` / `ak.stock_us_hist`，消除 105/106/107 猜测 | 中（字段映射 + PriceBar 适配） |
| `fetch_indices` / `_kline_indices` | EM 裸接口 → akshare 指数接口（如 `ak.index_global_spot_em` / `stock_zh_index_daily_em`）；`config.yaml` 里的 secid 可保留给裸 fallback | 中 |
| `fetch_crypto` / `_kline_crypto` | **不动**，继续 gate.io（akshare 加密覆盖弱、国内访问差） | 无 |
| `AKShareClient.*`（基本面/财务/龙虎榜/北向） | **不动**，已是 akshare | 无 |

配套：合并 `requirements.txt` 第 40/47 行的重复 `akshare`，保留 `akshare==1.18.64`（=当前最新）。
保留所有裸 `requests` 函数作为降级路径——akshare 偶尔单接口失效时仍能兜底，这正是项目「多源容错」设计的延续。

**统一对外契约不变**：所有方法继续返回 `PriceBar`，主程序与 `factor_model.py` 无需改动。
