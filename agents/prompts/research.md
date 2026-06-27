# Agent Prompt — 研报跟进模块

**Worktree**: `.claude/worktrees/feature-research`  
**分支**: `feature/research`  
**核心文件**: `x_agent/research_fetcher.py`, `x_agent/storage.py`, `x_agent/digest.py`, `config.yaml`

---

## Prompt 模板

```
你在 /Users/pany19/Documents/x_agent_proj/.claude/worktrees/feature-research 这个 git worktree
（分支 feature/research）里工作。Python 3.14，venv 在 /Users/pany19/Documents/x_agent_proj/.venv。

## 模块职责
跟踪指定公司的券商研报（评级、目标价）和供应链动态，
每次运行后产出结构化数据（SQLite）+ Markdown 摘要。

## 数据源（国内可访问）
- 东方财富研报列表（JSONP）：reportapi.eastmoney.com/report/list
  参数：stockCode=代码, code=SH/SZ+代码, beginTime/endTime
- 同花顺研报备用：reportdatas.10jqka.com.cn/reportCenter/index
- 新浪财经关键词新闻：feed.mix.sina.com.cn/api/roll/get（供应商动态）
- 巨潮资讯公告：cninfo.com.cn（重要公告）

## 核心文件说明
- research_fetcher.py：ResearchClient，方法：
  fetch_reports_eastmoney(stock_code) / fetch_reports_ths(stock_code)
  fetch_supplier_news(supplier_name, customer_name)
- ResearchReport dataclass：stock_code, title, org_name, analyst, rating, target_price, published_at
- SupplierUpdate dataclass：supplier_name, customer_name, event_type, title, content

## 存储约定
storage.py 新增两张表：
- research_reports：研报记录（report_id PRIMARY KEY, stock_code, title, org_name, analyst, rating, target_price, published_at, url）
- supplier_updates：供应商动态（id, supplier_code, supplier_name, customer_name, event_type, title, content, source, published_at, url）

## 评级标准化
不同券商评级叫法不同，统一映射：
- 买入/强烈推荐/强推 → buy
- 增持/推荐/优于大市 → outperform
- 中性/持有/观望 → neutral
- 减持/低于大市 → underperform
- 卖出/回避 → sell

## 任务：[在这里描述具体任务]

完成后 git add + git commit。注释用中文。
```

---

## 模块现状（初始化）

**待配置跟踪标的**（在 config.yaml 的 research 节添加）：
```yaml
research:
  watch_stocks:
    - code: "300750"
      name: "宁德时代"
      suppliers: ["赣锋锂业", "华友钴业", "恩捷股份"]
    - code: "600519"
      name: "贵州茅台"
      suppliers: []
    - code: "688041"
      name: "海光信息"
      suppliers: ["台积电", "中芯国际"]
  report_days: 90   # 拉取最近 N 天的研报
```
