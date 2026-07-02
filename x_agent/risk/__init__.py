"""x_agent.risk：组合风险因子分解（个人版 Aladdin 批次一，见 docs/aladdin/05）。

依赖纪律：只依赖 pandas/numpy + backtest.data + sqlite；不 import fetcher/classifier
（风险层不抓数据）；SQLite 写入一律走 storage.Store。
"""
