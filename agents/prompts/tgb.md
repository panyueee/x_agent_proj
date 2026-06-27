# Agent Prompt — 淘股吧模块

**Worktree**: `.claude/worktrees/feature-tgb`  
**分支**: `feature/tgb`  
**核心文件**: `x_agent/_tgb_scraper.py`, `x_agent/tgb_fetcher.py`, `main.py`, `config.yaml`

---

## Prompt 模板

```
你在 /Users/pany19/Documents/x_agent_proj/.claude/worktrees/feature-tgb 这个 git worktree
（分支 feature/tgb）里工作。

重要：_tgb_scraper.py 必须用系统 python3（不是 venv），因为 Playwright 装在系统环境里。
调用方式：subprocess.run(["python3", SCRAPER, ...])

核心文件说明：
- _tgb_scraper.py（系统 python3 运行）：
  - scrape_blog(user_id, max_results)：滚动博客列表，返回文章链接
  - scrape_article(url)：抓单篇文章（title/body/views/comments/created_at）
  - scrape_stock_posts(stock_code, max_results)：抓个股讨论页帖子
  - CLI：python3 _tgb_scraper.py [blog|article|stock] <args>
- tgb_fetcher.py：TgbClient，user_posts() / stock_posts()，间隔 2s 防封 IP
- config.yaml：tgb.users（米开朗基瑞 ID:11056656）+ tgb.stocks（茅台/五粮液/宁德时代）

任务：[在这里描述具体任务]

URL 格式：https://www.tgb.cn（不是 taoguba.com.cn，SSL 证书只对 *.tgb.cn 有效）
文章 body 上限：1000 字符
抓文章间隔：time.sleep(2)

完成后 git add + git commit。注释用中文。
```

---

## 模块现状（最后更新：2026-06-27）

- 大V用户：米开朗基瑞（ID: 11056656）
- 个股监控：600519 贵州茅台 / 000858 五粮液 / 300750 宁德时代
- 滚动加载：稳定性检测，最多8次 End 键
- body：1000字 + 图片 alt 文字（最多5张）

## 常见问题

| 问题 | 处理方式 |
|---|---|
| SSL 错误 | 确认用 tgb.cn 而非 taoguba.com.cn |
| Playwright 502 | 确保用系统 python3，不是 venv python |
| 文章链接格式 | /a/xxxxx（短链）或 /Article/数字，两种都支持 |
| 个股页无帖子 | scrape_stock_posts 返回 []，正常跳过 |
