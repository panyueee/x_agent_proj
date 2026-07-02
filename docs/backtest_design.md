# backtest 自研回测系统 — 设计文档

> 状态：定稿（接口冻结）。改接口必须先改本文档。
> 定位：日频、向量化信号 + 逐日循环会计的轻量组合回测，与 x_agent 包完全解耦，只依赖 pandas/numpy/matplotlib。

## 0. 开源框架调研：借鉴了什么 / 为什么不直接用

| 框架 | 借鉴点 | 不直接用的原因 |
|---|---|---|
| [vectorbt](https://github.com/polakowo/vectorbt) | "信号层全向量化（date×symbol 宽表）、账户层逐行推进"的总体架构；策略输出矩阵而非逐 bar 回调 | 重度依赖 Numba（Python 3.14 支持滞后）；API 面极大、学习成本高；A 股规则（T+1/涨跌停/印花税）需自行魔改 |
| [rqalpha](https://github.com/ricequant/rqalpha) | **A 股规则细节的主要参照**：`price_limit` 涨跌停撮合限制（涨停不可买、跌停不可卖）、停牌不可成交、T+1 持仓、佣金双边+最低 5 元+卖出印花税、一手 100 股取整。本设计的撮合限制语义与其 `sys_simulation` mod 对齐 | 事件驱动 + bundle 数据格式，必须先 ingest 成它的 h5 数据包，与我们现成的 parquet 结构不兼容；框架太重（mod 体系、自带数据源） |
| [backtrader](https://github.com/mementum/backtrader) / [zipline-reloaded](https://github.com/stefan-jansen/zipline-reloaded) | 策略 API 的"极简单入口"思想（zipline 的 `order_target_percent` 即目标权重语义，本引擎直接以目标权重矩阵为一等公民） | 逐 bar 事件驱动，日频组合回测下慢且啰嗦；backtrader 已停维护；zipline ingest 数据管线笨重且对 A 股规则零支持 |
| [microsoft/qlib](https://github.com/microsoft/qlib) | 数据层"按市场分目录、每标的一文件、加载后对齐统一日历"的组织方式（我们的 parquet 目录天然就是这个结构） | 面向 ML 因子工程的平台，回测只是附属；自有二进制数据格式，转换成本高于收益 |
| [PyBroker](https://github.com/edtechre/pybroker) | 无特别借鉴（定位类似但绑定自家数据源） | 同样不认识我们的 parquet；A 股规则缺失 |
| [bt](https://github.com/pmorissette/bt) / [ffn](https://github.com/pmorissette/ffn) | **绩效指标公式直接参照 ffn**：年化=几何、回撤=nav/cummax-1、夏普=均值/标准差×√freq、卡玛=年化/最大回撤 | 未 pip 引入：ffn 指标总量不过百行且在 Python 3.14 上未验证兼容；自写可控且便于加"换手率/相对基准超额"等 ffn 没有的口径 |

结论：轻量自研。核心理由——(1) 数据层必须原生吃本项目 parquet（列名/复权口径有自己的坑，见 §1）；(2) A 股规则按 rqalpha 语义实现即可覆盖需求；(3) signals 表事件研究是完全自定义的用例，任何框架都要写同量代码。

调研来源：[rqalpha sys_simulation README](https://github.com/ricequant/rqalpha/blob/master/rqalpha/mod/rqalpha_mod_sys_simulation/README.rst)、[Ricequant 回测文档](https://www.ricequant.com/doc/quant/backtest)、[框架对比 (autotradelab)](https://autotradelab.com/blog/backtrader-vs-nautilusttrader-vs-vectorbt-vs-zipline-reloaded)、[vectorbt 精度讨论](https://github.com/polakowo/vectorbt/discussions/185)。

## 1. 已核实的数据事实（实现必须处理）

- 统一 schema：`date, open, high, low, close, volume, amount, adj_close, symbol, market`，RangeIndex，**date 为字符串列**（如 "2026-06-30"）。
- **A 股 parquet 的 `adj_close` 全为 None**（object 列），但 `close` 本身已是前复权价（baostock qfq，如 sh.600519 2001 年 close=4.64）。规则：`adj_close` 整列为空 → 复权因子取 1，直接用 OHLC；否则 factor = adj_close/close，OHLC × factor。
- `index_history/` 与 `crypto_history/` 的列名是 **`ticker`**（非 `symbol`），且多 `name`/`category` 列、无 `market`/`amount` 列 → loader 需重命名兼容。
- `index_history/000300_SS.parquet` 仅覆盖 **2021-03-11 起**（GSPC 从 2000 年起）。基准区间不足时只在重叠区间算超额，不报错。
- 停牌：A 股停牌日在 parquet 中**无行**（对齐日历后为 NaN）；也可能出现 volume=0 的行。两者都判为不可交易。
- `data/a_share_names.json`：dict `{"sh.600519": "贵州茅台", ...}` 共 8812 条，含 "ST"/"*ST" 前缀 → 用于涨跌停幅度判定。局限：只有**当前**名称，无历史 ST 状态（记入 TODO）。
- signals 表（`output/x_agent.db`，**只读**，另有进程在写 rag.db，绝不能碰）：260 行，`tweet_id/category/score/tickers(JSON)/extracted`；时间戳需 JOIN tweets.created_at（ISO 格式，**可能为空串**）。tickers 多为 `$BTC`/`$ETH` 等加密符号，少量 6 位 A 股代码。

## 2. 目录与模块

```
backtest/
├── __init__.py        # 导出主要入口
├── data.py            # 数据层：parquet → 对齐日历的宽表 MarketData
├── engine.py          # 引擎：CostModel + run_backtest（T+1 目标权重驱动）
├── strategy.py        # Strategy 基类 + MACross / MomentumTopN / SignalEvent 三示例
├── event_study.py     # signals 表读取、ticker 映射、事件研究统计
├── metrics.py         # 绩效指标
└── report.py          # markdown + PNG 报告 → output/backtest/
scripts/run_backtest.py    # CLI
tests/test_backtest_data.py / _engine.py / _metrics.py
docs/backtest_design.md    # 本文档
```

依赖方向单向：`data ← engine ← (strategy, event_study) ← report/CLI`。禁止 import x_agent。

## 3. 冻结接口（实现必须逐字对齐签名）

### 3.1 data.py

```python
MARKET_DIRS = {"a": "stock_history/a", "us": "stock_history/us", "hk": "stock_history/hk",
               "crypto": "crypto_history", "index": "index_history", "etf": "etf_history",
               "futures": "futures_history", "fx": "fx_history", "bond": "bond_history",
               "cb": "cb_history"}

@dataclass
class MarketData:
    market: str
    calendar: pd.DatetimeIndex        # 所加载标的日期并集，升序
    open: pd.DataFrame                # date×symbol 复权开盘价，ffill 后
    high: pd.DataFrame
    low: pd.DataFrame
    close: pd.DataFrame               # 复权收盘价，ffill 后；收益一律用它
    volume: pd.DataFrame              # 原始成交量，缺失填 0
    tradable: pd.DataFrame            # bool：当日有真实行情且 volume>0
    limit_up: pd.DataFrame            # bool：收盘涨停（仅 A 股可能 True，其余全 False）
    limit_down: pd.DataFrame
    open_limit_up: pd.DataFrame       # bool：开盘即涨停（开盘涨幅达限，近似一字板）
    open_limit_down: pd.DataFrame

def load_market_data(market: str, symbols: list[str], start: str | None = None,
                     end: str | None = None, data_dir: str | Path = "data") -> MarketData
def load_benchmark(name: str = "000300_SS", start: str | None = None,
                   end: str | None = None, data_dir: str | Path = "data") -> pd.Series
def limit_rate(symbol: str, name: str | None) -> float
def list_symbols(market: str, data_dir: str | Path = "data") -> list[str]
```

细节：
- 文件定位：`{data_dir}/{MARKET_DIRS[market]}/{symbol}.parquet`，缺文件抛 `FileNotFoundError` 并列出缺失清单。
- date 列 `pd.to_datetime` 后设为索引；`ffill` 价格但 `tradable=False` 标记缺行/volume=0。ffill 之前的开头 NaN（未上市）保持 NaN 且不可交易。
- 复权：见 §1；OHLC 全部乘同一 factor。
- 涨跌停判定（仅 market=="a"；**基于前复权价的近似**，除权日附近可能误判，可接受）：
  - `rate = limit_rate(symbol, name)`：名称含 "ST" → 0.05；代码前缀 300/301/688/689 → 0.20；bj → 0.30；其余 → 0.10。名称查 `data/a_share_names.json`（读不到则视为 None）。
  - `limit_up = close/prev_close - 1 >= rate - 0.003`（0.003 容差对应 rqalpha 涨停价四舍五入的误差带）；`open_limit_up = open/prev_close - 1 >= rate - 0.003`。down 侧对称。
- `load_benchmark(name)`：依次尝试 `index_history/{name}.parquet`、`crypto_history/{name}.parquet`；返回复权收盘 `pd.Series`（DatetimeIndex，name=标的名）。

### 3.2 engine.py

```python
@dataclass
class CostModel:
    commission_rate: float = 0.00025   # 双边佣金
    min_commission: float = 5.0        # 单笔最低佣金
    stamp_tax_rate: float = 0.001      # 印花税，仅卖出
    slippage_rate: float = 0.001       # 滑点：买入抬价、卖出压价（成交价按比例调整）
    lot_size: int = 100                # 一手股数；0 表示允许小数股
    @classmethod
    def for_market(cls, market: str) -> "CostModel"
    def buy_cost(self, amount: float) -> float     # max(amount*commission_rate, min_commission)
    def sell_cost(self, amount: float) -> float    # 佣金(含最低) + amount*stamp_tax_rate

@dataclass
class BacktestResult:
    name: str
    nav: pd.Series                # 逐日净值，起点 1.0（按日终 close 估值）
    returns: pd.Series            # nav.pct_change().fillna(0)
    positions: pd.DataFrame       # date×symbol 日终持仓市值
    weights: pd.DataFrame         # date×symbol 日终实际权重
    cash: pd.Series
    turnover: pd.Series           # 当日成交额合计 / 当日组合市值
    trades: pd.DataFrame          # columns: date, symbol, side("buy"/"sell"), shares, price, amount, cost
    benchmark: pd.Series | None   # 对齐到 nav.index 并归一到 1.0 的基准净值
    initial_capital: float

def run_backtest(data: MarketData, weights: pd.DataFrame, *,
                 initial_capital: float = 1_000_000.0,
                 cost_model: CostModel | None = None,     # None → CostModel.for_market(data.market)
                 trade_at: str = "open",                  # "open" | "close"：T+1 日的成交价
                 benchmark: pd.Series | None = None,
                 name: str = "backtest") -> BacktestResult
```

**费用参数表**（`CostModel.for_market`）：

| market | 佣金 | 最低佣金 | 印花税(卖) | 滑点 | lot |
|---|---|---|---|---|---|
| a | 万 2.5 | ¥5 | 千 1 | 千 1 | 100 |
| hk | 万 2.5 | 5 | 千 1（近似，实际双边） | 千 1 | 100 |
| us | 万 0.5 | 0 | 0 | 万 5 | 1 |
| crypto | 千 1（taker） | 0 | 0 | 万 5 | 0（小数股） |
| 其他 | 同 us | | | | |

注：A 股印花税 2023-08 起实际为千 0.5，此处按任务要求默认千 1，参数可配。

**防未来函数（核心约定）**：`weights` 行 `t` 表示"基于 t 日收盘可得信息计算的目标权重"。引擎内部**统一执行 `weights.shift(1)`**，在 t+1 日以 `trade_at` 价格成交——策略永远不需要也不允许自己 shift。T+1 卖出限制在"每日至多一次调仓"的日频模型下自动满足（当日买入的仓位最早 t+1 日才会被再平衡卖出）。

**逐日循环**（对 calendar 每日 t）：
1. `target = shifted_weights.loc[t]`（NaN→0；行内负值报错；行和 >1+1e-6 报错，<1 视为留现金）。
2. 成交价 `px = open[t] 或 close[t]`；估值 V = cash + Σ shares×px（px 为 NaN 的持仓用最近 ffill 价）。
3. 目标股数：`floor(target*V / (px*(1+slippage)) / lot) * lot`（lot=0 不取整）；target=0 时卖出全部持仓（含零股）。
4. **先卖后买**；买单若现金不足按比例缩减。
5. 撮合限制（与 rqalpha `price_limit` 语义一致）：`not tradable[t]` → 该标的跳过；买入方向遇 `open_limit_up`（trade_at="open"）或 `limit_up`（trade_at="close"）→ 跳过；卖出遇跌停 → 跳过。跳过即保留原持仓，不排队。
6. 成交：买入现金减 `amount + buy_cost`，卖出现金加 `amount - sell_cost`；amount 按含滑点价计。
7. 日终以 close[t]（ffill 价）估值记 nav/positions/weights/turnover。

### 3.3 metrics.py

```python
def annual_factor(index: pd.DatetimeIndex) -> float      # 索引含周六/日 → 365（加密），否则 252（纯工作日日历日均间隔≈1.4 天，"<1.5 天"判据会误判，弃用）
def max_drawdown(nav: pd.Series) -> tuple[float, pd.Timestamp, pd.Timestamp]  # (回撤为负数, 峰, 谷)
def annualized_return(nav: pd.Series, freq: float) -> float                   # 几何年化
def sharpe(returns: pd.Series, freq: float, rf: float = 0.0) -> float
def compute_metrics(result: BacktestResult, freq: float | None = None) -> dict
```

`compute_metrics` 返回 keys：`start, end, n_days, total_return, annual_return, annual_vol, sharpe, max_drawdown, max_drawdown_peak, max_drawdown_trough, calmar, win_rate, avg_daily_turnover, n_trades, total_cost`，有基准时另含 `benchmark_total_return, benchmark_annual_return, excess_annual_return`（几何超额：(1+ra)/(1+rb)-1）。空仓期（returns 全 0 段）计入。波动为 0 时 sharpe 记 0。

### 3.4 strategy.py

```python
class Strategy(abc.ABC):
    name: str = "strategy"
    market: str = "a"
    def symbols(self) -> list[str]: ...                       # 需要加载的标的
    @abc.abstractmethod
    def generate_weights(self, data: MarketData) -> pd.DataFrame: ...
    # 返回 date×symbol 目标权重；行 t 只准用 t 及以前的数据（引擎负责 shift）

class MACrossStrategy(Strategy):     # 双均线择时（单标的）：fast>slow 满仓，否则空仓
    def __init__(self, symbol: str, market: str = "a", fast: int = 20, slow: int = 60)
class MomentumTopN(Strategy):        # 动量轮动：lookback 收益排序取 Top-N 等权，每 rebalance_days 调一次
    def __init__(self, symbols: list[str], market: str = "crypto",
                 lookback: int = 20, top_n: int = 5, rebalance_days: int = 5)
class SignalEventStrategy(Strategy): # signals 事件：信号日等权买入持有 hold_days 日
    def __init__(self, db_path: str = "output/x_agent.db", market: str = "crypto",
                 hold_days: int = 5, min_score: int = 3)

STRATEGIES: dict[str, type[Strategy]] = {"ma_cross": ..., "momentum": ..., "signal_event": ...}
```

实现注意：MomentumTopN 非调仓日**保持上次权重**（而非置 0）；上市不足 lookback 的标的不入选；权重矩阵在信号可算之前的日期全 0。

### 3.5 event_study.py

```python
def map_ticker(ticker: str) -> tuple[str, str] | None
# "$BTC"→("crypto","BTC-USD")；"600519"/6 位数字→("a", "sh.600519"/"sz.000001" 按前缀 6→sh,0/3→sz)；映射不了返回 None
def load_signal_events(db_path: str = "output/x_agent.db", market: str = "crypto",
                       min_score: int = 0) -> pd.DataFrame
# 只读连接（sqlite3 URI mode=ro）；JOIN tweets 取 created_at，空串/无法解析的丢弃；
# 返回 columns: date(交易日归一化), symbol, score；同日同标的去重
def event_study(events: pd.DataFrame, data: MarketData, benchmark: pd.Series | None = None,
                horizons: tuple[int, ...] = (1, 3, 5, 10)) -> pd.DataFrame
# 每个 horizon 一行: n_events, avg_return, median_return, win_rate, avg_excess(有基准时), t_stat
# 事件日 t 的 h 日收益 = close[t+h]/close[t] - 1（t 为信号日或其后第一个交易日）
```

### 3.6 report.py

```python
def render_report(result: BacktestResult, metrics: dict, *, out_dir: str | Path = "output/backtest",
                  run_name: str | None = None, plot: bool = True,
                  extra_sections: dict[str, str] | None = None) -> Path
# 生成 {out_dir}/{run_name}.md（+ 同名 .png）；返回 md 路径
```

markdown 含：参数摘要、指标表、月度收益表（可选）、前 10 大交易、图片引用。图用 matplotlib `Agg` 后端：上幅净值 vs 基准（对数刻度可选），下幅回撤面积图；样式克制（无花哨配色，两条线 + 灰色回撤）。`extra_sections` 用于事件研究表等附加内容。

### 3.7 CLI（scripts/run_backtest.py）

```
.venv/bin/python scripts/run_backtest.py --strategy ma_cross --market a --symbols sz.000890 --start 2018-01-01 [--end ...] [--benchmark 000300_SS] [--trade-at open] [--capital 1000000] [--run-name xxx] [--no-plot]
策略参数: --fast/--slow (ma_cross)；--lookback/--top-n/--rebalance-days (momentum)；--hold-days/--min-score/--db (signal_event)
--symbols all 且 market=crypto/etf 等 → list_symbols() 全池
--benchmark 缺省: market=a → 000300_SS, crypto → BTC-USD, us → GSPC, 其他不设
```

流程：构造策略 → `load_market_data` → `generate_weights` → `run_backtest` → `compute_metrics` → `render_report`，并把关键指标打印到 stdout。signal_event 策略额外跑 `event_study` 塞进 `extra_sections`。

## 4. 测试计划（tests/test_backtest_*.py）

手工小 fixture（tmp_path 写 3~5 行 parquet，或直接构造 MarketData）：
- data：日历对齐/停牌 ffill+tradable=False、adj_close 空列回退、涨跌停标记、ST/创业板幅度。
- engine：T+1（t 日权重 t+1 日按 open 成交，手算股数）、成本（佣金最低 5 元、卖出印花税，手算现金轨迹）、涨停买入被跳过、停牌保留持仓、现金不足缩单、行和>1 报错。
- metrics：已知净值序列手算总收益/年化/回撤/夏普；annual_factor 对加密 365。
- event_study/strategy：map_ticker 映射、双均线权重只依赖历史（构造未来突变数据验证无泄漏）。

## 5. 已知局限 / TODO

- ST 状态用当前名称近似，无历史 ST 时间线；分红送转导致的除权日涨跌停判定有误差（前复权价近似）。
- 未建模：分红现金流（用复权价隐含处理）、融资融券、期货保证金、盘中价格路径、成交量冲击（rqalpha 的 volume_percent 限制未实现）。
- 沪深300 基准仅 2021-03 起，更早区间超额收益缺失。
- signals 库仅 260 条且时间跨度短，事件研究统计功效有限——先把管道跑通，等信号积累。
