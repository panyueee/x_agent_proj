# Agent Prompt — 产业链分析模块

**Worktree**: `.claude/worktrees/feature-industry`  
**分支**: `feature/industry`  
**核心文件**: `x_agent/industry_fetcher.py`, `x_agent/storage.py`, `x_agent/digest.py`, `config.yaml`

---

## Prompt 模板

```
你在 /Users/pany19/Documents/x_agent_proj/.claude/worktrees/feature-industry 这个 git worktree
（分支 feature/industry）里工作。Python 3.14，venv 在 /Users/pany19/Documents/x_agent_proj/.venv。

## 模块职责
跟踪指定产业链的上下游关系、行业动态、关键节点变化。
目标是每次运行后产出结构化数据（SQLite）+ Markdown 摘要。

## 数据源（国内可访问）
- 东方财富行业板块成分股：push2.eastmoney.com/api/qt/clist/get
  sector_code 示例：BK0471（新能源汽车）、BK0481（半导体）、BK0493（AI算力）
- 新浪财经行业新闻：feed.mix.sina.com.cn/api/roll/get（k=关键词）
- 巨潮资讯公告：www.cninfo.com.cn/new/hisAnnouncement/query（POST）
- 东方财富个股资金流向：push2.eastmoney.com/api/qt/stock/fflow/kline/get

## 核心文件说明
- industry_fetcher.py：IndustryClient，方法：
  fetch_sector_stocks(sector_code) / fetch_company_news(keyword) / fetch_cninfo_announcements(code)
- IndustryNode dataclass：code, name, role(upstream/core/downstream), chain
- ChainEvent dataclass：chain, title, content, source, url, published_at

## 存储约定
storage.py 新增两张表：
- industry_nodes：产业链节点（code, name, role, chain, notes, updated_at）
- chain_events：产业链事件（id, chain, title, content, source, url, published_at, relevance_score）

## 任务：[在这里描述具体任务]

完成后 git add + git commit。注释用中文。
```

---

## 模块现状（初始化）

**待配置产业链**（在 config.yaml 的 industry 节添加）：
```yaml
industry:
  chains:
    - name: "新能源汽车"
      sector_code: "BK0471"
      keywords: ["宁德时代", "比亚迪", "汽车电池", "碳酸锂"]
      core_stocks: ["300750", "002594"]
    - name: "AI算力"
      sector_code: "BK0493"
      keywords: ["英伟达", "AI芯片", "算力", "大模型"]
      core_stocks: ["688041", "002415"]
```
