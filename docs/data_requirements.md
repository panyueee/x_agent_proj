# 数据需求清单

最后更新：2026-06-28

本文档记录 Riskfolio-Lib 风险优化 和 toraniko 多因子模型 所需的全部数据，
以及当前缺口和推荐补充方式。

---

## 现状快照

运行 `python main.py --source factor` 可随时刷新数据就绪状态。

| 数据类型 | 当前状态 | 就绪条件 |
|---------|---------|---------|
| 日收益率（价格历史） | ❌ 仅 2 行 | ≥ 252 行（约 1 年） |
| 市值（每日浮动） | ❌ 未实现 | 每个品种每日 1 条 |
| P/B 市净率倒数 | ❌ 未实现 | 每个品种每日 1 条 |
| 行业分类（申万一级） | ⚠️ 空表 | 每个监控品种 1 条 |
| P/S 市销率倒数 | ❌ 未实现 | 可暂时跳过 |
| P/CF 现金流价格比 | ❌ 未实现 | 可暂时跳过 |

---

## 第一优先级：Riskfolio-Lib 最低需求

> 只需补这一项，CVaR 风险优化就能跑通。

### 日收益率（≥ 60 行解锁 Riskfolio，≥ 252 行解锁 toraniko）

- **当前**：`price_bars` 表每个品种只有 2 个数据点
- **需要**：至少 60 个交易日（Riskfolio），最好 252 个（toraniko Momentum 因子）
- **格式**：

```
price_bars 表
  symbol    TEXT    -- 品种代码，如 600519 / NVDA / BTCUSDT
  timestamp TEXT    -- 日期 YYYY-MM-DD
  close     REAL    -- 收盘价
```

- **获取方式（两种选一）**：

**方式 A — AKShare 一次性回填（推荐）**

```python
import akshare as ak

# A 股（需转格式）
df = ak.stock_zh_a_hist(symbol="600519", period="daily",
                         start_date="20250101", end_date="20260628",
                         adjust="qfq")
# 字段：日期 / 开盘 / 收盘 / 最高 / 最低 / 成交量 / 涨跌幅

# 加密货币
df = ak.crypto_hist_doge_em()   # 示例，各币种接口不同

# 美股
df = ak.stock_us_hist(symbol="NVDA", period="daily",
                       start_date="20250101", end_date="20260628",
                       adjust="qfq")
```

**方式 B — 让 `--source finance` 每轮自动积累**（慢，60 轮才够）

- **入库位置**：`x_agent/finance_fetcher.py` → `store.save_price_bar()`
- **负责人**：待开发 `scripts/backfill_prices.py`

---

## 第二优先级：toraniko 基础因子

### 市值（每日浮动总市值）

- **用途**：Size 因子 + Momentum 因子加权
- **格式**：

```
price_bars 表（需新增列）或独立表 fundamentals
  symbol      TEXT
  date        TEXT
  market_cap  REAL    -- 单位：亿元
```

- **获取方式**：

```python
import akshare as ak

# 东方财富个股信息（含总市值）
info = ak.stock_individual_info_em(symbol="600519")
# 返回 DataFrame，行包括：总市值、流通市值、市净率、市盈率等

# 批量获取当日全市场市值
spot = ak.stock_zh_a_spot_em()
# 字段包含：代码、名称、总市值、流通市值
```

- **入库建议**：新增 `fundamentals` 表（见下方 Schema），或在 `price_bars` 加列

---

### P/B 市净率倒数（账价比 = book_price）

- **用途**：toraniko Value 因子（三个估值指标之一，优先级最高）
- **计算**：`book_price = 1 / 市净率（PB）`
- **获取方式**：

```python
import akshare as ak

info = ak.stock_individual_info_em(symbol="600519")
# 从返回结果中取"市净率"字段，取倒数即为 book_price
```

---

### 行业分类（申万一级 → toraniko GICS 映射）

- **用途**：toraniko Sector 因子（11 个 0/1 指示列）
- **格式**：每个股票 1 条，11 列 0/1 指示

| symbol | Technology | Financials | Energy | Industrials | Consumer | Healthcare | Materials | Real Estate | Utilities | Communication | Others |
|--------|-----------|-----------|-------|------------|---------|-----------|---------|------------|---------|--------------|-------|
| 600519 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| 300750 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |

- **申万→GICS 映射**（已在 `x_agent/factor_model.py` 的 `SW_TO_GICS` 实现，覆盖 2021 修订版全部 31 个行业）：

```
计算机 / 电子                        → Technology
通信 / 传媒                          → Communication Services
医药生物 / 美容护理（2021新增）       → Health Care
银行 / 非银金融                      → Financials
食品饮料 / 农林牧渔                  → Consumer Staples
汽车 / 家用电器 / 商贸零售 / 社会服务（2021新增） → Consumer Discretionary
轻工制造 / 机械设备 / 军工 / 交通 / 建筑 → Industrials
化工 / 钢铁 / 有色金属 / 建筑材料   → Materials
煤炭 / 石油石化                      → Energy
电力设备 / 公用事业 / 环保（2021新增）→ Utilities
房地产                               → Real Estate
综合                                 → Industrials
```

- **概念板块→GICS 映射**（已在 `x_agent/factor_model.py` 的 `CONCEPT_TO_GICS` 实现，覆盖 60+ 主流概念）：

```
AI算力 / 大模型 / 半导体 / 信创 / 云计算  → Technology
人形机器人 / 低空经济 / 央企改革           → Industrials
新能源 / 光伏 / 储能 / 碳中和             → Utilities
锂电池 / 碳酸锂 / 固态电池 / 稀土 / 黄金 → Materials
新能源汽车 / 免税 / 跨境电商             → Consumer Discretionary
白酒 / 消费复苏                           → Consumer Staples
创新药 / CXO / 医疗器械 / 医美           → Health Care
5G / 6G / 卫星通信 / 北斗                → Communication Services
券商 / 保险                               → Financials
REITs                                     → Real Estate
```

- **获取方式**：

```python
import akshare as ak

# 获取申万一级行业成分股
board = ak.stock_board_industry_name_em()
# 字段：板块名称
# 再用 ak.stock_board_industry_cons_em(symbol="食品饮料") 取成分股
```

---

## 第三优先级：toraniko Value 因子补全

### P/S 市销率倒数（sales_price）

- **用途**：Value 因子第二项
- **计算**：`sales_price = 营收 / 市值`
- **获取方式**：

```python
import akshare as ak

# 财务分析指标（含营收增速、ROE 等）
df = ak.stock_financial_analysis_indicator(symbol="600519", start_year="2024")
# 或使用东方财富利润表接口
df = ak.stock_profit_sheet_by_report_em(symbol="600519", indicator="年报")
```

- **频率**：季报/年报频率（不是日频），需插值到日频使用

### P/CF 现金流价格比（cf_price）

- **用途**：Value 因子第三项（可暂时跳过）
- **计算**：`cf_price = 经营现金流 / 市值`
- **获取方式**：

```python
df = ak.stock_cash_flow_sheet_by_report_em(symbol="600519", indicator="年报")
```

---

## 推荐 fundamentals 表 Schema

```sql
CREATE TABLE IF NOT EXISTS fundamentals (
  id           TEXT PRIMARY KEY,   -- hash(symbol + date)
  symbol       TEXT,
  date         TEXT,               -- YYYY-MM-DD
  market_cap   REAL,               -- 总市值（亿元）
  pb           REAL,               -- 市净率
  book_price   REAL,               -- 1/PB（账价比）
  pe_ttm       REAL,               -- 市盈率 TTM
  ps_ttm       REAL,               -- 市销率 TTM
  sales_price  REAL,               -- 1/PS（销价比）
  roe          REAL,               -- ROE（%）
  sector_sw    TEXT,               -- 申万一级行业名称
  sector_gics  TEXT,               -- 映射后的 GICS 行业
  fetched_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_fund_symbol ON fundamentals(symbol);
CREATE INDEX IF NOT EXISTS idx_fund_date   ON fundamentals(date);
```

---

## 解锁路线图

```
现在
  │
  ├─ 补 price_returns（≥60行）→ 解锁 Riskfolio-Lib CVaR 优化
  │       方式：backfill_prices.py 一次性回填 1 年 A股/美股/加密
  │
  ├─ 补 market_cap + book_price（每日）→ 解锁 toraniko Size + Value 因子
  │       方式：finance_fetcher.py 每轮顺带抓 ak.stock_individual_info_em
  │
  ├─ 补 sector（申万一级）→ 解锁 toraniko Sector 因子
  │       方式：一次性脚本，结果存 industry_nodes 或 fundamentals 表
  │
  └─ 补 sales_price / cf_price（季报频率）→ 解锁完整 toraniko Value 因子
          方式：季报抓取脚本，按季更新
```

---

## 快速验证命令

```bash
# 检查当前数据就绪状态
python main.py --source factor

# 回填完价格历史后，测试 Riskfolio 是否能跑
python main.py --source risk

# 信号质量分析（不需要额外数据）
python scripts/analyze_signals.py --days 30
```
