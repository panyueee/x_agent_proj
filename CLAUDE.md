# X 资讯抓取 Agent — 项目说明（Claude Code 常驻上下文）

每次会话开始时 Claude Code 会自动读取本文件。改动项目时请保持它准确、精简。

## 这是什么
多源金融/加密资讯监控 Agent，抓取 X (Twitter)、小红书、淘股吧、东方财富、天眼查等平台数据，
关键词打分筛选后存入 SQLite，生成 Markdown 摘要，并触发产业链联动分析。

## 目录结构

```
x_agent_proj/
├── main.py            # 主入口：加载配置 → 抓取 → 分类 → 入库 → 摘要
├── config.yaml        # 全局配置（账号、搜索词、参数）；${VAR} 从环境变量展开
├── requirements.txt
│
├── x_agent/           # 核心包
│   ├── fetcher.py          # X API 数据源抽象（OfficialXClient / ThirdPartyXClient）
│   ├── classifier.py       # 关键词加权打分 + 可选 Claude 结构化抽取
│   ├── storage.py          # SQLite 去重存储（tweets / signals / companies 等表）
│   ├── digest.py           # 生成 output/digest.md
│   ├── finance_fetcher.py  # A股/美股/加密/指数行情（东方财富）
│   ├── finance_chart.py    # K线图辅助
│   ├── industry_fetcher.py # 产业链板块数据
│   ├── industry_learner.py # 产业链联动触发与 LLM 学习
│   ├── pipeline.py         # 产业链摘要编排 → output/pipeline_digest.md
│   ├── qcc_fetcher.py      # 天眼查（工商信息 1001 接口）+ 东方财富上市公司
│   ├── research_fetcher.py # 研报数据
│   ├── tgb_fetcher.py      # 淘股吧（调用 _tgb_scraper.py）
│   ├── xhs_fetcher.py      # 小红书
│   └── _tgb_scraper.py     # Playwright 淘股吧爬虫（被 tgb_fetcher 子进程调用）
│
├── scripts/           # 一次性工具 / 调试脚本（不参与主流程）
│   ├── fetch_following.py  # 抓取指定账号的 following 列表写入 config.yaml
│   ├── probe_following.py  # 测试 twitterapi.io 连通性
│   ├── probe_tgb.py        # 测试淘股吧 Playwright 抓取
│   └── test_xhs.py         # 测试小红书抓取
│
├── docs/              # 参考文档
│   └── 1001-工商信息.pdf   # 天眼查工商信息接口文档
│
├── agents/            # Claude Code 多 Agent 工作目录
│   ├── dashboard.py
│   ├── workflow.md
│   ├── notes/         # 各模块开发记录
│   └── prompts/       # 各 Agent 提示词模板
│
└── output/            # 运行时生成（已 gitignore）
    ├── x_agent.db
    ├── digest.md
    └── pipeline_digest.md
```

## 常用命令

```bash
pip install -r requirements.txt

# 基本运行（需设环境变量）
export THIRDPARTY_API_KEY=...   # twitterapi.io
export ANTHROPIC_API_KEY=...    # Claude（use_llm: true 时需要）
export TYC_TOKEN=...            # 天眼查（qcc 模块）
python main.py

# 指定配置文件
python main.py my_config.yaml

# 工具脚本
python scripts/fetch_following.py   # 更新 following 列表
```

## 关键约束

- **只走 API，绝不直接爬 x.com 网页** —— 违反 X 条款且脚本易失效
- 官方 X API 按量付费，务必在开发者后台设消费上限
- **密钥绝不写进代码或 config.yaml**，一律走环境变量
- 天眼查工商信息走 1001 接口（`open.api.tianyancha.com`，¥1/次），检查 `error_code == 0`
- 若新增自动发帖/转发，必须保留人工确认环节

## 约定

- 调打分规则 → `x_agent/classifier.py` 的 `STRATEGY_KEYWORDS / WEB3_KEYWORDS`
- 接新数据源 → 在 `fetcher.py` 实现相同的 `user_tweets / search` 接口，主程序无需改动
- 注释用中文，保持模块解耦
