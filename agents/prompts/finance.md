# Agent Prompt — 金融行情模块

**Worktree**: `.claude/worktrees/feature-finance`  
**分支**: `feature/finance`  
**核心文件**: `x_agent/finance_fetcher.py`, `x_agent/finance_chart.py`, `x_agent/storage.py`, `x_agent/digest.py`, `main.py`, `config.yaml`

---

## Prompt 模板

```
你在 /Users/pany19/Documents/x_agent_proj/.claude/worktrees/feature-finance 这个 git worktree
（分支 feature/finance）里工作。Python 3.8，venv 在 /Users/pany19/Documents/x_agent_proj/.venv。

数据源（全部国内可访问，无需 API Key）：
- A股实时：新浪财经 hq.sinajs.cn（gbk编码，Referer头必须）
- A股K线：baostock（已安装，login()/logout() 包裹）
- 美股实时+K线：东方财富 push2.eastmoney.com（secid格式：105.AAPL）
- 加密货币实时+K线：gate.io API（api.gateio.ws，国内可访问）

禁用数据源（国内访问受限）：
- Binance / OKX → 超时
- Yahoo Finance (yfinance) → 解析失败
- akshare → 不支持 Python 3.8（aiohttp 依赖冲突）

核心文件说明：
- finance_fetcher.py：FinanceClient，4个方法：
  fetch_a_shares / fetch_us_stocks / fetch_crypto / fetch_kline
- finance_chart.py：save_kline_chart()，用 mplfinance 生成 K线 PNG → ./charts/
- storage.py：price_bars 表（symbol, market, timestamp 联合主键）
  save_price_bar() / recent_price_bars() / latest_price()
- digest.py：build_digest() 末尾附加"💹 市场行情"Markdown 表格

东方财富价格缩放：f43 字段 ÷ 1000 = 真实价格（如 283780 → 283.78）
东方财富涨跌幅：f170 字段 ÷ 100 = 真实百分比（如 314 → 3.14%）

任务：[在这里描述具体任务]

Python 3.8 约束：不用新式类型注解，不用 walrus 运算符。
依赖安装：uv pip install（不用 pip，否则可能有版本冲突）

完成后 git add + git commit。注释用中文。
```

---

## 模块现状（最后更新：2026-06-27）

**监控标的：**
- A股：600519 贵州茅台 / 000858 五粮液 / 300750 宁德时代 / 601318 中国平安
- 美股：AAPL / NVDA / TSLA / MSFT
- 加密货币：BTC/USDT / ETH/USDT / SOL/USDT
- K线：kline_days=7（最近7个交易日）

**已安装依赖：**
- baostock（A股K线）
- yfinance==0.2.49 + multitasking==0.0.10（锁版本，Python 3.8 兼容）
- ccxt（安装了但 Binance/OKX 国内访问受限，暂不用）
- mplfinance（K线图）

## 常见问题

| 问题 | 处理方式 |
|---|---|
| A股非交易时间 | 新浪返回上一个交易日收盘价，正常 |
| 美股 secid 找不到 | 依次试 105→106→107，全失败才报错 |
| gate.io 返回空 | 检查 currency_pair 格式，用下划线：BTC_USDT |
| baostock 登录失败 | 包在 try/except 里，失败返回 [] |
| mplfinance 缺少 | uv pip install mplfinance |
