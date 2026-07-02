# 个人版 Aladdin 落地路线图

> 版本：v1（2026-07-02）
> 前置阅读：`docs/survey_aladdin.md`（尤其第 4 节 5 个 feature 提案）
> 性质：纯设计文档，"下个迭代直接开工"粒度；所有"已核实"结论见文末《事实核对说明》。

---

## 0. 结论速览（先说重点）

调研提案给出的顺序是 **3（地基）→ 1 → 2 → 4 → 5**。读完代码后**修正为：1（含 3 的瘦身切片）→ 2 → 3 余下部分 → 4 → 5**，理由：

1. **提案 1 比调研预想的容易得多**。调研假设"因子库要从零手搓"，实际上：
   - `x_agent/factor_model.py` 已用 toraniko 跑通了 market + 11 GICS 行业 + mom/sze/val 的截面因子模型，`factor_returns` 表已有 **85 个交易日**的因子收益（2026-02-13 ~ 2026-06-26）；
   - `sw_sector_cache` 表已有 **5609 只**股票的 GICS 行业分类；
   - `price_bars` 表已回填 **194 万行**（A 股 5570 只，2025-01-02 起）；
   - `backtest/data.py` 的 `load_market_data()` 是一个非常干净的 parquet→宽表加载层，直接可复用。
   缺的只是三块纯数学代码：滚动回归因子暴露、EWMA 因子协方差、风险贡献分解——几百行 pandas。
2. **提案 3 全量版当前杠杆不高**。signals 表只有 **260 行**，"信号绩效回填驱动打分权重迭代"要等信号积累；但提案 1 依赖"有一个持仓组合可分析"，而库里**没有任何持仓表**（`portfolio_weights` 只存优化器输出的权重 JSON，仅 1 行）。所以把提案 3 拆两半：`securities` 主表 + `portfolios/positions` 表这个**瘦身切片提前**并入第一批（约 1 天工作量），信号绩效跟踪**延后**到第二批。
3. **提案 2 是本地数据的独有优势**，且 90% 复用提案 1 的因子引擎与 `backtest/data.py`，紧跟其后。
4. 提案 4 是提案 1/2 产物（每日因子收益向量）之上的几十行 KNN，顺势而为。
5. 提案 5 需要新抓数据（ETF 持仓），是唯一有外部数据依赖的，放最后。

---

## 1. 现有资产盘点（读代码/查库核实）

### 1.1 本地行情数据湖 `data/`

| 目录 | 数量 | 覆盖 | schema 要点 |
|---|---|---|---|
| `stock_history/a/` | 5535 个 parquet | 2000 年起（样例 sh.600000：2000-01-04 ~ 2026-06-30，6417 行） | `date(str), open, high, low, close, volume, amount, adj_close, symbol, market`；**A 股 adj_close 整列 None，close 已是 baostock 前复权价** |
| `stock_history/us/` | 12899 | 同 schema | adj_close 有值时 loader 会做复权 |
| `stock_history/hk/` | 2788 | 文件名如 `0001.HK.parquet` | 同 schema |
| `crypto_history/` | 20 | BTC-USD 等 | 列名是 **ticker** 非 symbol，loader 已兼容 |
| `index_history/` | 17 | `000001_SS` 从 2000 年起（6411 行）；**`000300_SS` 仅 2021-03-11 起** | ticker 列 schema |
| `etf_history/` | 53 | 文件名 `159915_创业板ETF.parquet` | 多 `prevclose`/`name` 列，**无 adj_close**（分红除权未复权，注意） |
| `futures_history/cn|global/` | 数十 | 主力连续 A0/AG0/AU0… | — |
| `fx_history/` | 11+ | `DX_Y_NYB`（美元指数）、EURCNY、CNYJPY、EURUSD…（**无直接 USDCNY**，可用 EURCNY/EURUSD 推导） | — |
| `bond_history/` | 6 | `cn_yield_curve`、`us_yield_us10y/5y/30y/13w`、中美利差 | 宏观因子的现成来源 |
| `cb_history/` | 若干 | 可转债个券 + 集思录等权指数 | — |
| `a_share_names.json` | 8812 条 | `{"sh.600519": "贵州茅台"}` | 含 ST 前缀，供涨跌停判定 |

合计约 2.1 万标的日线，与调研说法一致。**这是提案 2（危机重放）的核心筹码：2008/2015/2016/2018/2020/2022 全部窗口都有全市场数据。**

### 1.2 SQLite `output/x_agent.db`（只读核查过行数）

| 表 | 行数 | 与提案的关系 |
|---|---|---|
| `tweets` / `signals` | 各 260 | 提案 3 信号绩效的输入（量还少） |
| `price_bars` | 1,941,924（A 股 5570 只，2025-01-02 ~ 2026-06-26） | factor_model 当前的数据源；**历史深度不如 parquet（仅 1.5 年）** |
| `fundamentals` | 5867 行但**只有 2026-06-28 一天**（market_cap/pb/book_price/pe_ttm） | Size/Value 因子的静态快照 |
| `sw_sector_cache` | 5609（symbol → GICS 行业） | 提案 1 行业因子直接可用 |
| `factor_returns` | 85 天，列：`market + 11 GICS 行业 + mom_score/sze_score/val_score` | toraniko 输出，digest 已展示 |
| `quarterly_financials` | 177,261 | Value 因子的营收数据 |
| `concept_mappings` | 544（东财概念→GICS，含人工确认位） | 行业分类兜底 |
| `portfolio_weights` | 1 | 优化器输出，**不是持仓账本** |
| `north_flow` / `dragon_tiger` | 0 | 提案 4 想用北向数据的话目前为空 |

**关键缺口：没有 securities 主表、没有 portfolios/positions 持仓表、没有 risk 快照表。**

### 1.3 自研回测引擎 `backtest/`（接口冻结于 docs/backtest_design.md）

- `data.py::load_market_data(market, symbols, start, end, data_dir) -> MarketData`：parquet → 对齐统一日历的 date×symbol 宽表（open/high/low/close/volume/tradable/涨跌停矩阵），处理复权、停牌 ffill、上市前 NaN。**这是全项目最好的数据加载层，风险引擎直接复用，不要重写。**
- `data.py::load_benchmark(name)`：index/crypto 目录找基准收盘序列。
- `engine.py::run_backtest(data, weights, ...)`：目标权重驱动 T+1 回测，含 A 股费税/涨跌停/停牌语义。
- `metrics.py::compute_metrics`、`report.py::render_report(..., extra_sections=...)`：markdown+PNG 报告，`extra_sections` 是现成的报告扩展点。
- `event_study.py::map_ticker / load_signal_events / event_study`：signals 表 ticker→(market, symbol) 映射与 T+h 事件收益统计。**提案 3 的信号绩效回填 = 把 event_study 的逐事件收益落库，代码大半现成。**
- 纪律：backtest 包禁止 import x_agent（依赖方向单向 `data ← engine ← … ← report`）；反向 x_agent → backtest **不违反**该约束。

### 1.4 x_agent 已有的"半个 Aladdin"

| 模块 | 现状 | 局限 |
|---|---|---|
| `factor_model.py` | toraniko 截面因子模型跑通，写 `factor_returns` 表；SW_TO_GICS（31 申万→11 GICS）+ CONCEPT_TO_GICS 映射齐全 | 数据源是 price_bars（仅 2025 起）；市值用**单日快照摊平到全历史**；max_symbols=300 |
| `risk_analyzer.py` | riskfolio-lib CVaR/MV/MDD 组合优化 + `compute_risk_report` 单标的风险指标 | 只吃 config 里的 watchlist，无因子分解、无组合概念 |
| `portfolio_optimizer.py` | PyPortfolioOpt Black-Litterman，信号分数→观点 | 同上，price_bars 短历史 |
| `digest.py::build_digest` | 模块化 `_xxx_section(store)` 拼 markdown，已有 `_factor_section`、`_portfolio_section` | **插风险区块的接缝就在这里**，模式成熟 |
| `main.py` | `--source factor/risk/portfolio` 已接线 | 新增 `--source riskreport` 照抄模式即可 |
| `classifier.py` | 关键词打分（拍脑袋权重），`TICKER_RE`/`ASHARE_RE` 提取 ticker | 提案 3 的"数据驱动调权"目标 |
| `finance_fetcher.py` | AKShareClient：fetch_fundamentals / quarterly_financials / dragon_tiger / north_flow | ETF 持仓抓取**未实现**（提案 5 缺口） |

### 1.5 匹配度总表

| 提案 | 调研预估可行性 | 代码核实后 | 修正原因 |
|---|---|---|---|
| 1 风险日报 | 高（几百行 numpy） | **更高**：因子模型/行业分类/优化器已存在，只缺暴露回归+协方差+分解+报告 | factor_model.py 超出调研预期 |
| 2 危机重放 | 高 | 高，但**依赖先用 parquet 重建全历史因子收益**（现有 factor_returns 只到 2026-02） | 数据 OK，引擎要先行 |
| 3 信号→组合→风险闭环 | 高（杠杆最大） | **拆两半**：持仓/主表切片必须先行；信号绩效部分因 signals 仅 260 行降级 | 无持仓表是提案 1 的阻塞项 |
| 4 相似日匹配 | 中高 | 中高，宏观变量比预想全（美元指数/中美利率曲线 parquet 在手；北向为空） | bond/fx parquet 是意外之喜 |
| 5 ETF 穿透 | 中 | 中，**唯一需要新数据源**；本地 53 只 ETF 日线可回测但无持仓 | 不变 |

---

## 2. 总体架构

新增一个 `x_agent/risk/` 子包承载全部风险计算；数据加载**统一走 `backtest.data`**（x_agent→backtest 方向允许）；计算结果落 SQLite 新表 + `output/risk_report.md`；digest 只从表里读（延续现有 `_xxx_section(store)` 模式，也是 Aladdin "one source of truth" 的最小落地——digest、报告、回测引用同一份落库结果）。

```
data/*.parquet ──backtest.data.load_market_data──▶ date×symbol 宽表
                                                     │
x_agent/risk/factors.py    ◀─ sw_sector_cache ───────┤   全历史因子收益（缓存 output/factors/）
x_agent/risk/exposure.py   ◀─ positions 表 ──────────┤   持仓因子 beta（250日滚动回归）
x_agent/risk/covariance.py                           │   EWMA 因子协方差
x_agent/risk/decompose.py                            ▼   σ_p、MCR/CCR、TE
x_agent/risk/scenarios.py                            危机重放（提案2）
x_agent/risk/analog.py                               相似日 KNN（提案4）
x_agent/risk/lookthrough.py ◀─ etf_holdings 表       ETF 穿透（提案5）
x_agent/risk/report.py ──▶ output/risk_report.md + risk_snapshots 表
x_agent/digest.py::_risk_section(store) ──▶ digest.md 风险区块
scripts/manage_portfolio.py / scripts/run_risk_report.py   CLI
```

依赖纪律：
- `x_agent/risk/*` 只依赖 pandas/numpy + `backtest.data` + sqlite；**不 import fetcher/classifier**（风险层不抓数据）。
- 所有 SQLite 写入走 `storage.py::Store` 的新方法，保持单一写入口。
- `output/x_agent.db` 新表由 `_SCHEMA` 建；`rag.db` 绝不触碰。

---

## 3. 提案 1：个人版 Green Package——组合风险因子分解日报

### 3.1 数据需求核对

| 需要 | 已有 | 缺口与补法 |
|---|---|---|
| 持仓组合（symbol, 数量/权重） | ❌ 无任何持仓表 | 新增 `portfolios/positions` 表 + 手动录入 CLI（见 §5 提案 3 切片） |
| 全市场日收益 | ✅ parquet 全历史，`load_market_data` 直接给复权 close 宽表 | 无 |
| 行业分类 | ✅ `sw_sector_cache`（5609 只 → 11 GICS） | 新股缺分类 → 落到 "Others"，每月跑一次 `scripts/update_concept_sectors.py` 补 |
| 市值（Size 因子） | ⚠️ `fundamentals` 仅 2026-06-28 一天 | v1 用**60 日滚动日均成交额**作规模代理分组（历史可算）；v2 再回填历史市值 |
| 因子收益历史 | ⚠️ `factor_returns` 表仅 85 天且基于 price_bars | 用 parquet 重算全历史（2005 起），缓存 `output/factors/factor_returns_a.parquet` |
| 基准 | ✅ `load_benchmark("000300_SS")`（2021 起）/ `000001_SS`（2000 起） | 跟踪误差 2021 前用 000001_SS，报告注明 |

### 3.2 模块划分与核心签名（伪代码）

```python
# x_agent/risk/factors.py —— 简版因子库（纯 pandas，不依赖 toraniko）
FACTORS = ["mkt", "size", "mom", "vol"] + GICS_11          # 4 风格 + 11 行业

def build_factor_returns(market="a", start="2005-01-01", end=None,
                         data_dir="data", cache_dir="output/factors",
                         force=False) -> pd.DataFrame:
    """全历史因子日收益，date×factor。有缓存且未过期(end<=缓存尾)直接读。
    - mkt  = 全A等权日收益（可选 000001_SS）
    - size = 规模代理(60日均成交额)底3成 - 顶3成 分组等权多空
    - mom  = 12-1月动量 top30% - bottom30%
    - vol  = 60日波动 低30% - 高30%
    - 行业 = 该行业等权收益 - mkt（超额形式，避免与mkt共线）
    实现要点：分批 load_market_data(chunk=500只) 拼收益宽表，控内存。
    """

def load_sector_map(db_path="output/x_agent.db") -> dict[str, str]:
    """sw_sector_cache: '600519'→GICS；键统一转成 parquet symbol 'sh.600519'。"""

# x_agent/risk/exposure.py
def estimate_betas(stock_returns: pd.DataFrame, factor_returns: pd.DataFrame,
                   window: int = 250, min_periods: int = 120) -> pd.DataFrame:
    """逐持仓 OLS 时序回归 → symbol×factor 的 beta（取窗口末值）。
    行业哑变量不进回归，直接查 sector_map 赋 1（避免多重共线）。
    返回附带 residual_vol（特质波动，年化）。"""

def portfolio_exposure(betas: pd.DataFrame, weights: pd.Series) -> pd.Series:
    """组合暴露 b = Σ w_i × beta_i。"""

# x_agent/risk/covariance.py
def ewma_cov(factor_returns: pd.DataFrame, halflife: int = 90) -> pd.DataFrame:
    """EWMA 因子协方差（年化）。就是 factor_returns.ewm(halflife).cov() 取末期。"""

# x_agent/risk/decompose.py
@dataclass
class RiskReport:
    vol_ann: float                 # 组合年化波动
    var99_1d: float                # 参数化 1 日 VaR（2.33σ_日）
    factor_vol: float; specific_vol: float
    exposures: pd.Series           # factor → beta
    ccr: pd.Series                 # factor → 风险贡献占比（和=因子部分）
    stock_ccr: pd.Series           # symbol → 风险贡献占比
    te_ann: float | None           # 对基准跟踪误差（250日实证）

def decompose(weights, betas, fcov, resid_vol, benchmark_ret=None) -> RiskReport:
    """σ_f² = bᵀΣb；σ_s² = Σ w²·resid²；σ_p = sqrt(σ_f²+σ_s²)
    MCR_k = (Σb)_k / σ_p；CCR_k = b_k × MCR_k；个股贡献同理按 w_i·beta_i 分摊。"""

# x_agent/risk/portfolio.py
def load_positions(store, portfolio_id="real", asof=None) -> pd.DataFrame:
    """positions 表最近一次快照 → [symbol, quantity, weight]；
    weight 为空时用最新 close×quantity 现算并回写。"""

# x_agent/risk/report.py
def render_risk_report(store, portfolio_id="real",
                       out_path="output/risk_report.md") -> Path:
    """编排：load_positions → build_factor_returns → estimate_betas
    → ewma_cov → decompose →（batch2 后追加 scenarios）→ 渲染 md
    → store.save_risk_snapshot(...)。"""
```

### 3.3 与现有代码的接缝

1. **数据加载**：`factors.py` 内部调 `backtest.data.load_market_data("a", symbols, start, end)`。唯一要动 backtest 的地方：**无**（分批加载在 risk 侧做，`load_market_data` 本身不改；若嫌 5000 只×26 年一次载入慢，可在 risk 侧按 500 只一批循环拼 close 宽表）。
2. **storage.py 新表**（追加进 `_SCHEMA`，另加 `save_risk_snapshot/latest_risk_snapshot` 方法）：

```sql
CREATE TABLE IF NOT EXISTS risk_snapshots(
  portfolio_id  TEXT,
  date          TEXT,               -- 快照日 YYYY-MM-DD
  vol_ann       REAL,
  var99_1d      REAL,
  te_ann        REAL,
  factor_vol    REAL, specific_vol REAL,
  exposures     TEXT,               -- JSON {factor: beta}
  risk_contrib  TEXT,               -- JSON {factor: ccr_pct}
  stock_contrib TEXT,               -- JSON [{symbol, name, pct}] top10
  method        TEXT DEFAULT 'ewma_factor_v1',
  computed_at   TEXT,
  PRIMARY KEY(portfolio_id, date)
);
```

3. **digest.py**：新增 `_risk_section(store)`（照抄 `_factor_section` 的容错模式：表不存在/为空返回 []），在 `build_digest` 里插到 `_factor_section` 之后、`_portfolio_section` 之前——逻辑顺序"因子行情 → 组合风险 → 调仓建议"。
4. **main.py**：新增 `--source riskreport` 分支，仿 `--source factor`：跑 `render_risk_report` → `build_digest` 刷新。
5. **factor_model.py 不动**：toraniko 线路保留（吃 price_bars、每日增量），新因子库吃 parquet 管全历史；两者输出列名对齐（market/行业/mom/sze），第二批做交叉验证后再决定是否合并。

### 3.4 工作量估计

| 任务 | 量级 |
|---|---|
| factors.py 全历史因子收益 + parquet 缓存 + 分批加载 | 1 天 |
| exposure.py + covariance.py + decompose.py（含手算单元测试） | 1 天 |
| portfolio.py + report.py + digest 区块 + main.py 接线 | 1 天 |
| 用真实持仓端到端跑通、数字 sanity check（对比 riskfolio 的组合 vol） | 0.5 天 |
| **合计** | **3.5 天**（另加提案 3 切片 1.5 天，见 §5） |

### 3.5 风险与简化取舍

- **行业分类用现值近似历史**（sw_sector_cache 是当前行业，无历史时间线）——个股改行业罕见，可接受；文档标注。
- **Size 因子用成交额代理**而非真实市值——排序相关性高，v1 够用；`fundamentals` 开始每日积累后（`--source finance` 已在写）平滑切换。
- **VaR 用参数化 2.33σ** 而非历史模拟——第二批危机重放天然给出历史视角，不必急。
- ETF/基金持仓先按"一只普通标的"算 beta（etf_history 有日线），穿透留给提案 5。
- 停牌股 ffill 收益为 0 会低估 beta——持仓里有长期停牌股时报告加警示行即可。

---

## 4. 提案 2：历史危机场景重放（穷人版压力测试）

### 4.1 数据需求核对

| 需要 | 已有 | 缺口 |
|---|---|---|
| 危机窗口全市场日线 | ✅ parquet 2000 年起全覆盖 | 无 |
| 危机窗口因子收益路径 | ⚠️ 由提案 1 的 `build_factor_returns` 全历史输出直接切片 | 依赖提案 1 先行 |
| 当年未上市标的的合成 | ✅ 提案 1 的 beta × 情景因子路径 | 无 |
| 指数基准 | ✅ `000001_SS`（2000 起）；000300 仅 2021 起 → 2008/2015 情景用上证综指 | 无 |

### 4.2 情景库（常量定义，写死在代码里）

```python
# x_agent/risk/scenarios.py
SCENARIOS = {                              # name: (start, end, benchmark, 描述)
  "2008_gfc":        ("2008-01-14", "2008-11-04", "000001_SS", "全球金融危机"),
  "2015_crash":      ("2015-06-12", "2015-08-26", "000001_SS", "A股杠杆股灾"),
  "2016_fuse":       ("2015-12-31", "2016-01-28", "000001_SS", "熔断"),
  "2018_tradewar":   ("2018-01-24", "2018-10-18", "000001_SS", "贸易战阴跌"),
  "2020_covid":      ("2020-01-20", "2020-03-23", "000001_SS", "疫情熔断（含美股联动）"),
  "2022_fed":        ("2021-12-13", "2022-04-26", "000300_SS", "美联储激进加息"),
  "2024_microcap":   ("2024-01-02", "2024-02-07", "000001_SS", "微盘股流动性危机"),
}

@dataclass
class ScenarioResult:
    name: str
    total_return: float           # 组合情景总收益
    max_daily_loss: float
    max_drawdown: float
    recovery_days: int | None     # 情景后回到期初净值的交易日数（数据内找不到则 None）
    per_position: pd.DataFrame    # symbol, mode("replay"|"synthetic"), ret, contrib
    coverage: float               # 直接重放的权重占比

def replay(weights: pd.Series, scenario: str, betas: pd.DataFrame,
           factor_returns: pd.DataFrame, data_dir="data") -> ScenarioResult:
    """1) 窗口内有真实行情的持仓：load_market_data 切片直接重放；
    2) 当年未上市/无数据的：ret_t = beta ∙ factor_ret_t（特质项取0，保守标注）；
    3) 权重固定为当前权重（不再平衡），逐日复利。"""

def run_all(weights, betas, factor_returns) -> pd.DataFrame:
    """7 情景汇总表，供 report.py 渲染。"""
```

### 4.3 接缝

- 因子收益路径直接来自提案 1 的缓存 parquet（`output/factors/factor_returns_a.parquet`），零新数据。
- `report.py::render_risk_report` 增加一节"压力测试"，调 `run_all`；digest 区块只放最差 2 个情景一行摘要。
- 结果可选落库（表 `scenario_results(portfolio_id, date, scenario, total_return, max_dd, coverage, detail_json)`，PRIMARY KEY(portfolio_id, date, scenario)）——个人建议 v1 只写 md 不落库，等报告形态稳定再加表。
- 预计算缓存：各情景的因子路径 + 全市场收益切片在 `build_factor_returns` 时顺手切好存 `output/factors/scenario_{name}.parquet`（几 MB），重放时不再碰全量 parquet。

### 4.4 工作量

| 任务 | 量级 |
|---|---|
| scenarios.py（窗口切片 + 合成收益 + 汇总） | 1 天 |
| 报告渲染 + 情景日期校准（对着指数走势核对起止点） | 0.5 天 |
| 恢复天数/逐持仓贡献等细节 + 测试 | 0.5 天 |
| **合计** | **2 天** |

### 4.5 取舍

- **权重固定不再平衡**是有意简化（Aladdin 的即时压力测试同样如此），标注即可。
- 合成收益忽略特质项 → 系统性低估个股极端损失，报告对 synthetic 行加 `*` 标注并给 coverage 百分比。
- 2008 情景里美股/加密持仓混合组合：v1 各市场分别用自己的日历重放后按权重加总（日历不对齐忽略，误差可接受）；跨市场统一日历留 v2。
- ETF 无复权价 → 情景窗口若跨分红日有小误差，53 只 ETF 分红少，接受。

---

## 5. 提案 3：信号→组合→风险闭环（one source of truth）

### 5.1 拆分决策

| 切片 | 内容 | 时机 |
|---|---|---|
| **3a 主数据+持仓（前置）** | securities / portfolios / positions 表 + 录入 CLI | 第一批（提案 1 的阻塞依赖） |
| **3b 信号绩效（延后）** | signal_performance 回填 + classifier 调权报告 | 第二批 |

### 5.2 数据需求核对

- 证券主数据的种子**全部现成**：`a_share_names.json`（代码→名称）、`sw_sector_cache`（行业）、`backtest.data.list_symbols()`（各市场 parquet 清单）、`event_study.map_ticker`（ticker 文本→统一 symbol 的映射规则）。
- 信号绩效的计算逻辑**大半现成**：`event_study.load_signal_events` + `event_study()` 已实现 T+h 收益统计，只差"逐事件明细落库"而非只出汇总。
- 缺口：signals 表只有 260 行且集中在加密——3b 做完管道后**等数据**，不追求立刻出结论。

### 5.3 Schema 草案（追加进 storage.py `_SCHEMA`）

```sql
CREATE TABLE IF NOT EXISTS securities(
  symbol       TEXT PRIMARY KEY,   -- 统一主键，与 parquet 文件名对齐：sh.600519 / NVDA / BTC-USD
  market       TEXT,               -- a / us / hk / crypto / etf / index / cb / fx / futures
  name         TEXT DEFAULT '',
  sector_gics  TEXT DEFAULT '',    -- 迁移自 sw_sector_cache
  aliases      TEXT DEFAULT '[]',  -- JSON: ["600519", "$MOUTAI", "贵州茅台"] 供信号 ticker 解析
  has_parquet  INTEGER DEFAULT 0,
  updated_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_sec_market ON securities(market);

CREATE TABLE IF NOT EXISTS portfolios(
  portfolio_id TEXT PRIMARY KEY,   -- 'real' / 'paper_signals' / 'paper_optimizer'
  name         TEXT, base_ccy TEXT DEFAULT 'CNY', created_at TEXT
);
CREATE TABLE IF NOT EXISTS positions(
  portfolio_id TEXT, date TEXT, symbol TEXT,
  quantity REAL, cost_price REAL, weight REAL,
  source TEXT DEFAULT 'manual',    -- manual / signal / optimizer
  note   TEXT DEFAULT '',
  PRIMARY KEY(portfolio_id, date, symbol)
);
CREATE INDEX IF NOT EXISTS idx_pos_latest ON positions(portfolio_id, date);

CREATE TABLE IF NOT EXISTS signal_performance(
  tweet_id TEXT, symbol TEXT, event_date TEXT,
  horizon INTEGER,                 -- 1/5/20 交易日
  ret REAL, excess_ret REAL,       -- 对市场基准的超额
  category TEXT, score INTEGER,    -- 冗余存打分现场，方便按类别聚合
  filled_at TEXT,
  PRIMARY KEY(tweet_id, symbol, horizon)
);
CREATE INDEX IF NOT EXISTS idx_sigperf_cat ON signal_performance(category, horizon);
```

约定：**不给 tweets/signals 加外键做强迁移**（260 行不值得动存量 schema），关联靠 `map_ticker` 在读取时解析——securities.aliases 覆盖非标写法即可。

### 5.4 模块与接缝

```python
# scripts/build_securities_master.py（一次性 + 幂等重跑）
#   list_symbols 各市场 → INSERT OR REPLACE securities
#   + a_share_names.json 填 name + sw_sector_cache 填 sector_gics
#   预计 2.1 万行，跑一次 <1 分钟

# scripts/manage_portfolio.py（持仓录入 CLI）
#   add --portfolio real --symbol sh.600519 --qty 200 --cost 1450
#   set-weights --portfolio paper --weights '{"sh.600519":0.3, "BTC-USD":0.1}'
#   show --portfolio real   （现价估值：latest close 来自 parquet 尾行）
#   每次变更写一条新 date 快照（不可变账本，历史可回放）

# x_agent/risk/signal_perf.py（3b）
def backfill_signal_performance(store, horizons=(1,5,20)) -> int:
    """load_signal_events(market=each) → 对每事件用 load_market_data 切
    [t-1, t+max(h)] 小窗口算收益与超额 → INSERT OR IGNORE signal_performance。
    幂等：主键去重；未到期(t+h 超出数据尾)跳过，下次运行补。"""

def keyword_hit_report(store) -> str:
    """JOIN signals→signal_performance，按 category/score 分桶输出
    平均 T+5/T+20 超额 markdown 表 → 供人工调 classifier 权重（不自动改权重）。"""
```

- **classifier.py 不自动改**：第一步只产出"分数桶 × 未来收益"报告（digest 或独立 md），人工看数据调 `STRATEGY_KEYWORDS` 权重，保留人工确认环节（符合项目关键约束的精神）。
- `portfolio_optimizer.py` 的输出可以直接写成 `paper_optimizer` 组合的 positions 快照（source='optimizer'），从而复用同一套风险报告——一行胶水。

### 5.5 工作量

| 任务 | 量级 |
|---|---|
| 3a：schema + build_securities_master + manage_portfolio CLI | 1.5 天 |
| 3b：signal_perf 回填 + 命中率报告 + digest 一节 | 1 天 |
| **合计** | **2.5 天** |

### 5.6 取舍

- 主表不含上市日/退市状态（parquet 首行日期即隐含上市日，用时现算）。
- 持仓不做交易流水（buy/sell 记录），只做快照账本——个人手动调仓频率低，快照够用；将来接 `run_backtest` 的 trades 格式再升级。
- 信号绩效不区分"信号方向"（做多/做空）——extracted JSON 里 direction 字段质量不稳，v1 全按做多算，标注局限。

---

## 6. 提案 4：What-if 历史相似日匹配

### 6.1 数据需求核对

| 特征维度 | 来源 | 状态 |
|---|---|---|
| 市场/风格/行业因子日收益 | 提案 1 的 factor_returns 缓存（2005 起，~5000 天） | 依赖提案 1 |
| 行业轮动强度 | 11 行业因子收益的截面标准差，现算 | ✅ |
| 美元指数 | `data/fx_history/DX_Y_NYB.parquet` | ✅ |
| 中美利率 | `data/bond_history/us_yield_us10y.parquet`、`cn_yield_curve.parquet` | ✅ |
| 人民币汇率 | 无 USDCNY，用 EURCNY/EURUSD 推导 | ⚠️ 推导，v1 可先不放 |
| 北向资金 | north_flow 表为 0 行 | ❌ 先不用 |

### 6.2 设计

```python
# x_agent/risk/analog.py
def build_day_vectors(factor_returns, macro_dir="data") -> pd.DataFrame:
    """date × feature 矩阵并 z-score 标准化（全历史均值方差，查询日截断防未来）。
    features = [mkt, size, mom, vol, sector_dispersion, dxy_ret, us10y_chg, cn10y_chg]"""

def find_analogs(condition: dict, day_vectors, n=30,
                 feature_weights=None) -> pd.DataFrame:
    """condition 如 {"mkt": -0.03, "dxy_ret": +0.005}（未指定的维度不参与距离）
    → 加权余弦/欧氏 KNN → 返回 n 个相似日 + 相似度。"""

def conditional_outlook(analog_days, weights, betas, factor_returns,
                        horizon=1) -> dict:
    """相似日集合上：组合合成收益(beta×当日因子收益)的 P10/P50/P90 分位区间
    + 各行业在这些日子的平均表现表。只给区间，不给点估计。"""
```

- 入口：`scripts/whatif.py --mkt -3% --dxy +0.5%` 打印 markdown；不进 digest 日报（按需查询型工具）。
- 组合在相似日的表现用 **beta 合成**而非真实重放（相似日大多在持仓上市前，与提案 2 的 synthetic 路径同一函数，直接复用）。

### 6.3 工作量与取舍

- **1.5 天**：day_vectors 0.5 + KNN与分位输出 0.5 + CLI 与文案 0.5。
- 取舍：不做宏观变量的领先滞后处理（利率是水平变化、当日对齐即可）；北向/两融等待 north_flow 有数据后追加维度；条件维度少时相似日可能同质（如全是 2015 年）——输出附相似日的年份分布提醒。

---

## 7. 提案 5：ETF/基金持仓穿透（look-through）

### 7.1 数据需求核对

| 需要 | 已有 | 缺口与补法 |
|---|---|---|
| ETF 全持仓 | ❌ | akshare `fund_portfolio_hold_em(code, year)` 季度前十 + `index_stock_cons_weight_csindex(index_code)` 指数权重（宽基 ETF 用跟踪指数权重近似全持仓） |
| 场外基金重仓 | ❌ | 同 `fund_portfolio_hold_em`，仅季报前十大，如实标 coverage |
| ETF↔跟踪指数映射 | ❌ | 手工维护 53 只（本地 etf_history 就这么多），一个 dict 常量足够 |
| 成分股行情/beta | ✅ 提案 1 全套 | 无 |

### 7.2 设计

```python
# x_agent/etf_holdings_fetcher.py（fetcher 层，可访问网络；风险层只读表）
def fetch_etf_holdings(codes: list[str]) -> list[dict]:
    """宽基→csindex 指数权重；行业/主动→季报前十大。
    返回 [{etf_symbol, report_date, symbol, weight, source}]"""

# storage.py 新表
# CREATE TABLE IF NOT EXISTS etf_holdings(
#   etf_symbol TEXT, report_date TEXT, symbol TEXT, weight REAL,
#   source TEXT,          -- csindex / quarterly_top10
#   fetched_at TEXT,
#   PRIMARY KEY(etf_symbol, report_date, symbol));

# x_agent/risk/lookthrough.py
def expand_positions(positions: pd.DataFrame, store) -> tuple[pd.DataFrame, dict]:
    """持仓里 market=='etf' 的行 → 按最新 etf_holdings 展开成等效个股权重，
    未覆盖部分(1-Σholding_weight)保留为 'ETF残差' 虚拟标的（beta 用 ETF 自身回归值）。
    返回 (等效持仓, {etf: coverage})。"""
```

- 接缝：`report.py` 加开关 `lookthrough=True` → 先 `expand_positions` 再走原有 exposure/decompose 流程；报告出"名义 vs 穿透"两列对比表（行业集中度、个股最大权重、组合 beta）。
- `main.py --source etf_holdings` 接抓取（月度手动跑一次即可，季报频率数据不必 cron）。

### 7.3 工作量与取舍

- **2.5 天**：fetcher+表 1 天，映射表与清洗 0.5 天，lookthrough+报告对比 1 天。
- 取舍：主动基金只有前十大（覆盖 40-60%），残差按 ETF 自身 beta 处理是 Aladdin 处理低透明资产的同款思路；QDII/商品 ETF v1 不穿透（标 not-covered）；持仓权重用报告期静态值，不做日间漂移调整。

---

## 8. 迭代计划（个人业余时间，每批 1-2 周）

### 批次一：能看到自己组合的风险日报（提案 3a + 提案 1）——约 5 天

| # | 任务 | 量级 |
|---|---|---|
| 1 | schema（securities/portfolios/positions/risk_snapshots）+ build_securities_master | 1 天 |
| 2 | manage_portfolio.py 录入真实/虚拟持仓 | 0.5 天 |
| 3 | factors.py 全历史因子收益 + 缓存 | 1 天 |
| 4 | exposure/covariance/decompose + 手算测试 | 1 天 |
| 5 | report.py + digest `_risk_section` + main.py `--source riskreport` | 1 天 |
| 6 | 端到端 sanity check（组合 vol 对比 riskfolio 口径） | 0.5 天 |

**验收标准**：
- `scripts/manage_portfolio.py add ...` 录入 ≥5 只跨市场持仓后，`python main.py --source riskreport` 生成 `output/risk_report.md`，含：组合年化波动、1 日 VaR、因子暴露表、风险贡献 top5 因子 + top10 个股；
- `output/digest.md` 出现"组合风险"区块；`risk_snapshots` 表有当日记录；
- 单测：2 只股票的玩具组合，beta/协方差/σ_p 手算值与代码一致（容差 1e-6）。

### 批次二：压力测试 + 信号闭环管道（提案 2 + 3b）——约 4.5 天

任务：scenarios.py（2 天）→ 风险报告加压力测试节（含 7 情景表、synthetic 标注、coverage）→ signal_perf 回填 + 命中率报告（1 天）→ portfolio_optimizer 输出接 paper 组合（0.5 天）→ 校准与测试（1 天）。

**验收标准**：报告能回答"我的组合放进 2015 股灾会回撤多少、哪只最伤、多少权重是合成估计"；`signal_performance` 表回填全部可算事件，digest 出现"信号命中率"小节。

### 批次三：What-if 与穿透（提案 4 + 5）——约 4 天

任务：analog.py + whatif CLI（1.5 天）→ etf_holdings fetcher + lookthrough + 名义 vs 穿透对比（2.5 天）。

**验收标准**：`scripts/whatif.py --mkt -3%` 输出相似日分布与组合条件分位区间；持有 1 只 ETF 时报告给出穿透前后行业集中度对比及 coverage。

### 后续（不排期）

- fundamentals 每日积累满 1 年后：Size 因子从成交额代理切换真实市值；toraniko 线与自建因子线对表合并。
- signals 积累 >2000 条后：命中率报告 → classifier 权重的半自动建议（仍人工确认）。
- north_flow/dragon_tiger 有数据后进 analog 特征。

---

## 9. 事实核对说明

**读代码/查库确认的事实**（本文档设计依据）：

- `backtest/data.py`、`engine.py`、`metrics.py`、`report.py`、`strategy.py`、`event_study.py` 全文已读；`load_market_data`/`render_report(extra_sections)`/`map_ticker` 等签名照实引用。
- parquet 实测：`stock_history/a` 5535 个文件（sh.600000 为 2000-01-04~2026-06-30，date 为字符串列，adj_close 全 None）；us 12899、hk 2788、crypto 20（ticker 列）、index 17、etf 53（文件名含中文名、无 adj_close、有 prevclose/name 列）、fx 含 DX_Y_NYB/EURCNY（无 USDCNY）、bond 含 cn_yield_curve 与 us_yield_us10y 等。
- `output/x_agent.db` 只读查询：signals/tweets 各 260 行；price_bars 1,941,924 行（market='A' 5570 只，2025-01-02~2026-06-26）；fundamentals 5867 行且**仅 2026-06-28 一天**；sw_sector_cache 5609 行（格式 `('002142','Financials',ts)`，键为 6 位裸代码）；factor_returns 85 行（2026-02-13~2026-06-26，列=market+11 GICS+mom/sze/val_score）；quarterly_financials 177,261 行；north_flow/dragon_tiger 0 行；portfolio_weights 1 行。
- `x_agent/factor_model.py`（toraniko、SW_TO_GICS 31 行业映射、市值单日摊平的实现）、`risk_analyzer.py`（riskfolio）、`portfolio_optimizer.py`（pypfopt BL）、`digest.py`（`_xxx_section` 模式与插入顺序）、`classifier.py`、`storage.py` 全部 schema、`main.py` 的 `--source` 分支均已读原文。
- `docs/backtest_design.md`（接口冻结、000300_SS 仅 2021-03 起、x_agent.db 只读约束）、`docs/data_requirements.md`（已过时：写"price_bars 仅 2 行"，实际已 194 万行）、`docs/persona_design.md`、`docs/rag_architecture.md` 已读。

**假设/未核实项**（实施时需先验证）：

1. **akshare ETF 持仓接口**（`fund_portfolio_hold_em`、`index_stock_cons_weight_csindex`）的可用性与字段名——凭对 akshare 的一般了解写出，未在本环境实测；提案 5 开工第一件事是探针脚本。
2. **危机情景起止日期**为记忆值（如 2015-06-12 高点、2020-03-23 低点），实施时对照 `000001_SS` parquet 校准到具体交易日。
3. 全历史因子收益的**计算耗时与内存**（5000 只×26 年宽表）未实测；分批 500 只的方案是预防性设计，若单机一次载入可行则简化。
4. `sw_sector_cache` 键（6 位裸代码）与 parquet symbol（`sh.600519`）的映射需在 `load_sector_map` 里做前缀转换——转换规则参照 `event_study.map_ticker`（6→sh，0/3→sz），**北交所/B 股等边角未核实覆盖**。
5. toraniko 的 `factor_returns` 与自建简版因子的收益口径（回归系数 vs 多空组合）不同，两者数值**不可直接比较**，批次二对表时需换算——此为方法论判断非代码事实。
6. ETF parquet 的 close 是否除权：从"无 adj_close、有 prevclose"推断为不复权原始价，未逐只验证。
