# 信号 track-record 报告（signal → 前瞻收益闭环）

> 生成参数: horizon=1, since=全部. Aladdin 路线图 3b. 反哺建议仅供人工核验，**未自动改 classifier**。

## 0. 重要免责

- **样本极小、且信号多集中在近两周** → 本报告是**管道验证，非统计结论**。逐桶看 n_signals，个位数样本不要据此下注。

- **方向假设**：所有信号按"做多"计收益。feed 内含做空/看空/跌停论点（extracted 为空无法确知方向），对疑似做空信号打了 short_share 列披露；一个"看空且股票真跌"的正确信号会被记成负超额，勿据此判信源好坏。

- **超额口径**：crypto 基准 BTC-USD、A股 000300、美股 GSPC。基准真实数据未覆盖的窗口（如 000300 止于 2026-06-26）**不外推**，excess 置空、回退用原始收益。当前 excess 有效覆盖 77/206。

- **cashtag 消歧**：`$SYM` 先按加密（存在 {SYM}-USD）再按美股再按 ETF，数据驱动。

- **入场防未来函数**：入场=信号本地日"严格之后"的第一个交易日，收益窗口不足则丢弃该行（不补值）。

- **入场可成交性**：复用 backtest 涨跌停/停牌约定，入场日停牌或一字涨停记 tradable_entry=0（收盘价无法建仓，收益虚高）；当前 3 行如此，未剔除仅打标，可自行过滤。

- **excess_cov 列**：该桶里"真实超额"观测占比。偏低说明 avg_excess 实为原始收益（基准未覆盖回退），别当 alpha 读——尤其 A 股桶。


## 1. signal_performance 概况

- 落表行数（signal×security×horizon）: **206**

- 各市场 × horizon 行数:

```
market  horizon
a       1          136
        5            6
        20           5
crypto  1           59
```

- 说明: 美股行情本地数据止于 ~06-29 而美股信号多为 06-27，h=1 出场即越界被丢弃；crypto/A股 h=5、h=20 多数信号太新，待数据积累后重跑。


## 2. Track-record（horizon=1，信号级折叠，多标的信号只算一票）

### 2.1 按信源大类 (source)

| source | n_signals | n_obs | hit_rate | avg_excess | excess_cov | avg_ret | short_share |
| --- | --- | --- | --- | --- | --- | --- | --- |
| taoguba | 8 | 8 | 62% | +3.46% | 88% | +3.74% | 12% |
| twitter | 49 | 187 | 47% | +1.04% | 32% | +0.78% | 43% |


### 2.2 按 feed (source_label)

| source_label | n_signals | n_obs | hit_rate | avg_excess | excess_cov | avg_ret | short_share |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 6777446 | 7 | 7 | 71% | +4.24% | 100% | +4.57% | 0% |
| a_stock_earnings_zh | 1 | 10 | 100% | +2.80% | 0% | +2.80% | 100% |
| web3_news | 2 | 8 | 100% | +2.20% | 100% | +1.40% | 50% |
| a_stock_news | 3 | 118 | 67% | +1.56% | 0% | +1.56% | 0% |
| crypto_strategy | 31 | 37 | 42% | +0.96% | 100% | +0.45% | 45% |
| crypto_strategy_zh | 12 | 14 | 42% | +0.77% | 100% | +1.18% | 42% |
| 11056656 | 1 | 1 | 0% | -2.06% | 0% | -2.06% | 100% |


### 2.3 按作者 (author) — 只列样本≥2

| author | n_signals | n_obs | hit_rate | avg_excess | excess_cov | avg_ret | short_share |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 木岩Pierre | 7 | 7 | 71% | +4.24% | 100% | +4.57% | 0% |
| carla121100 | 2 | 97 | 100% | +3.28% | 0% | +3.28% | 50% |
| dens_club | 2 | 8 | 100% | +2.20% | 100% | +1.40% | 50% |
| cash_callinAI | 4 | 4 | 50% | +2.04% | 100% | +3.06% | 25% |
| vivekofficialyt | 2 | 2 | 50% | +1.31% | 100% | -1.31% | 0% |
| Finora_EN | 6 | 6 | 50% | +0.94% | 100% | +0.14% | 100% |
| ztx99999 | 2 | 2 | 50% | +0.76% | 100% | +1.78% | 0% |
| ztx999btc | 2 | 2 | 50% | +0.76% | 100% | +1.78% | 0% |
| ok5ok55 | 3 | 3 | 0% | +0.00% | 100% | -2.63% | 100% |


### 2.4 按信号类型 (category)

| category | n_signals | n_obs | hit_rate | avg_excess | excess_cov | avg_ret | short_share |
| --- | --- | --- | --- | --- | --- | --- | --- |
| strategy+stock+finance | 5 | 34 | 80% | +3.54% | 6% | +3.72% | 40% |
| stock+finance | 6 | 101 | 67% | +2.93% | 4% | +3.00% | 0% |
| both+finance | 1 | 6 | 100% | +2.63% | 100% | +0.00% | 0% |
| both | 2 | 3 | 100% | +1.65% | 100% | +2.67% | 100% |
| strategy | 42 | 50 | 40% | +0.89% | 100% | +0.61% | 43% |
| stock | 1 | 1 | 0% | -0.18% | 100% | +0.83% | 0% |


### 2.5 按关键词 (keyword) — 只列样本≥2

| keyword | table | n_signals | hit_rate | avg_excess | excess_cov | avg_ret | short_share |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 目标价 | STRATEGY_KEYWORDS_ZH | 2 | 100% | +7.55% | 100% | +7.98% | 0% |
| 毛利率 | FINANCE_KEYWORDS_ZH | 2 | 100% | +6.10% | 1% | +6.88% | 0% |
| 指数 | STOCK_KEYWORDS_ZH | 3 | 67% | +4.35% | 67% | +4.64% | 33% |
| 涨停 | STOCK_KEYWORDS_ZH | 3 | 67% | +4.35% | 67% | +4.64% | 33% |
| 龙头 | STOCK_KEYWORDS_ZH | 10 | 80% | +3.88% | 4% | +4.12% | 20% |
| 营收 | STOCK_KEYWORDS_ZH | 8 | 75% | +3.23% | 5% | +3.21% | 25% |
| 板块 | STOCK_KEYWORDS_ZH | 8 | 62% | +2.74% | 35% | +2.84% | 25% |
| 突破 | STRATEGY_KEYWORDS_ZH | 9 | 67% | +2.31% | 16% | +2.85% | 22% |
| depin | WEB3_KEYWORDS | 2 | 100% | +2.20% | 100% | +1.40% | 50% |
| liquidation | STRATEGY_KEYWORDS | 2 | 100% | +2.07% | 100% | +1.27% | 100% |
| defi | WEB3_KEYWORDS | 6 | 83% | +1.86% | 100% | +2.28% | 50% |
| revenue | STOCK_KEYWORDS | 2 | 100% | +1.72% | 100% | +0.91% | 0% |
| A股 | STOCK_KEYWORDS_ZH | 5 | 60% | +1.66% | 2% | +1.53% | 40% |
| target | STRATEGY_KEYWORDS | 10 | 70% | +1.53% | 100% | +1.46% | 70% |
| 仓位 | STRATEGY_KEYWORDS_ZH | 3 | 33% | +1.53% | 67% | +1.64% | 67% |
| setup | STRATEGY_KEYWORDS | 8 | 38% | +1.53% | 100% | +2.09% | 25% |
| leverage | STRATEGY_KEYWORDS | 3 | 67% | +1.38% | 100% | +1.19% | 67% |
| loss | FINANCE_KEYWORDS | 2 | 50% | +1.31% | 100% | +0.51% | 50% |
| ema | STRATEGY_KEYWORDS | 4 | 75% | +1.20% | 100% | +1.31% | 100% |
| 主力 | STOCK_KEYWORDS_ZH | 3 | 67% | +1.19% | 0% | +1.19% | 33% |


## 3. 给 classifier 的权重调整建议（仅建议）

> 改 `x_agent/classifier.py` 对应表的权重前，先看 n_signals 是否够大。当前样本下这些只是**方向性提示**。


**建议上调权重（正超额、样本≥3）**：
- `指数`（STOCK_KEYWORDS_ZH）: 平均超额 +4.35%, 命中 67%, n_signals=3, 真实超额覆盖 67%
- `涨停`（STOCK_KEYWORDS_ZH）: 平均超额 +4.35%, 命中 67%, n_signals=3, 真实超额覆盖 67%
- `龙头`（STOCK_KEYWORDS_ZH）: 平均超额 +3.88%, 命中 80%, n_signals=10, 真实超额覆盖 4%
- `营收`（STOCK_KEYWORDS_ZH）: 平均超额 +3.23%, 命中 75%, n_signals=8, 真实超额覆盖 5%
- `板块`（STOCK_KEYWORDS_ZH）: 平均超额 +2.74%, 命中 62%, n_signals=8, 真实超额覆盖 35%
- `突破`（STRATEGY_KEYWORDS_ZH）: 平均超额 +2.31%, 命中 67%, n_signals=9, 真实超额覆盖 16%

**建议下调/观察（负超额）**：
- `回调`（STRATEGY_KEYWORDS_ZH）: 平均超额 -0.69%, 命中 0%, n_signals=3, 真实超额覆盖 67%
- `业绩`（STOCK_KEYWORDS_ZH）: 平均超额 -0.07%, 命中 33%, n_signals=3, 真实超额覆盖 0%
