# Agent Prompt — 小红书模块

**Worktree**: `.claude/worktrees/feature-xhs`  
**分支**: `feature/xhs`  
**核心文件**: `x_agent/xhs_fetcher.py`, `config.yaml`

---

## Prompt 模板

```
你在 /Users/pany19/Documents/x_agent_proj/.claude/worktrees/feature-xhs 这个 git worktree
（分支 feature/xhs）里工作。Python 3.8，venv 在 /Users/pany19/Documents/x_agent_proj/.venv。

数据源：xhs CLI（xiaohongshu-cli，已安装）+ EasyOCR（图片文字识别）。
CLI 用法：xhs search "关键词" --sort latest | xhs read <note_id>

核心文件说明：
- xhs_fetcher.py：XhsClient，search() / user_posts() 接口
  - _ocr_images()：下载图片 → EasyOCR 识别，每张限 MAX_IMGS=3
  - _card_to_tweet()：XHS note 结构 → Tweet，含 tag_list 话题词提取
  - search() 里用 ThreadPoolExecutor 超时保护（15s/条）
- config.yaml：xhs.searches（13条搜索），xhs.accounts（留空）

任务：[在这里描述具体任务]

Python 3.8 约束：不用新式类型注解，不用 walrus 运算符。

注意：
- 视频笔记跳过 OCR（card.type == "video"）
- created_at 为空的笔记不过滤（保留）
- 互动数用 _parse_count() 处理"1.2万"/"3k"格式

完成后 git add + git commit。注释用中文。
```

---

## 模块现状（最后更新：2026-06-27）

- 搜索词：13条（BTC/Web3/合约/量化/A股×5/ETF/可转债/大盘/美股/龙头股）
- max_per_search：15
- OCR：EasyOCR ch_sim+en，gpu=False，首次加载约30s

## 常见问题

| 问题 | 处理方式 |
|---|---|
| xhs CLI 返回非 YAML | _read_note() 异常捕获，返回 {} |
| OCR 超时 | ThreadPoolExecutor timeout=15s，跳过该条 |
| 登录失效 | 终端手动重新登录 xhs，不是代码问题 |
| CDN 防盗链 | HEADERS 里加 Referer: https://www.xiaohongshu.com |
