# X 资讯抓取 Agent — 项目说明（Claude Code 常驻上下文）

每次会话开始时 Claude Code 会自动读取本文件。改动项目时请保持它准确、精简。

## 这是什么
监控 X (Twitter) 上的活跃交易策略与 Web3 资讯（如 Serenity 等账号），
关键词打分筛选后存入 SQLite，并生成 Markdown 摘要。可单次运行或定时轮询。

## 架构速览
- `main.py` — 编排：加载配置 → 抓取 → 分类 → 入库 → 生成摘要，支持定时循环
- `x_agent/fetcher.py` — 数据源抽象。`OfficialXClient`=官方 X API v2；
  `ThirdPartyXClient`=第三方适配器骨架；`build_client()` 按配置选择数据源
- `x_agent/classifier.py` — 关键词加权打分（策略 / Web3）+ 可选的 Claude 结构化抽取
- `x_agent/storage.py` — SQLite 去重存储（tweets / signals 两张表）
- `x_agent/digest.py` — 生成 `digest.md`
- `config.yaml` — 账号、搜索词、参数；`${VAR}` 形式从环境变量展开

## 常用命令
- 装依赖：`pip install -r requirements.txt`
- 运行：先 `export X_BEARER_TOKEN=...`，再 `python main.py`
- 指定配置：`python main.py my_config.yaml`
- 开启 LLM 抽取：config.yaml 设 `classify.use_llm: true` + `export ANTHROPIC_API_KEY=...`

## 关键约束（重要）
- **只走官方/第三方 API，绝不直接爬 x.com 网页** —— 违反 X 条款且脚本易失效
- 官方 API 按量付费，务必在开发者后台设消费上限；读取每月硬上限 200 万条
- **密钥绝不写进代码或 config.yaml**，一律走环境变量
- 若以后新增自动发帖/转发，必须保留人工确认环节，不要做成全自动

## 约定
- 调打分规则 → 改 `classifier.py` 里 `STRATEGY_KEYWORDS / WEB3_KEYWORDS` 的权重与阈值
- 接新数据源 → 在 `fetcher.py` 实现相同的 `user_tweets / search` 接口，主程序无需改动
- 注释用中文，保持模块解耦
