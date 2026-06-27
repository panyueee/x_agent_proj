# Agent Prompt — X (Twitter) 模块

**Worktree**: `.claude/worktrees/feature-x`  
**分支**: `feature/x`  
**核心文件**: `x_agent/fetcher.py`, `x_agent/classifier.py`, `main.py`, `config.yaml`

---

## Prompt 模板

```
你在 /Users/pany19/Documents/x_agent_proj/.claude/worktrees/feature-x 这个 git worktree
（分支 feature/x）里工作。Python 3.8，venv 在 /Users/pany19/Documents/x_agent_proj/.venv。

数据源：twitterapi.io（第三方 X API），配置在 config.yaml x_api.thirdparty。
速率限制：1 请求 / 6 秒（free tier），ThirdPartyXClient.min_interval=6.0。

核心文件说明：
- fetcher.py：ThirdPartyXClient，user_tweets() / search() 接口
- classifier.py：STRATEGY_KEYWORDS / WEB3_KEYWORDS / STOCK_KEYWORDS 加权打分，
  classify() 输出 category: strategy|web3|finance|both|none
- config.yaml：account_groups.serenity_following（177个账号）+ searches（8条）

任务：[在这里描述具体任务]

Python 3.8 约束：
- 不用 list[str]、dict[str,int] 等新式类型注解
- 不用 walrus 运算符（:=）
- from __future__ import annotations 已存在

完成后在 worktree 目录里 git add + git commit。
注释用中文。
```

---

## 模块现状（最后更新：2026-06-27）

- `ThirdPartyXClient`：min_interval=6.0，inactive_accounts 缓存，网络错误退避重试
- 分类器：strategy / web3 / finance / A股 stock 四类关键词
- 搜索词：8条（加密策略×2中英 / Web3×2中英 / 美股财报 / A股涨停 / FOMC / A股财报）
- account_groups：serenity_following（177账号，打 group_tag）

## 常见问题

| 问题 | 处理方式 |
|---|---|
| 429 限流 | _get() 自动退避重试，最多5次 |
| 账号无近期推文 | 加入 inactive_accounts，本轮跳过 |
| 搜索无结果 | 正常，跳过即可 |
