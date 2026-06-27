# X 资讯抓取 Agent

自动监控 X (Twitter) 上的**活跃交易策略**和 **Web3 资讯**（如 Serenity 等账号），
打分筛选后入库，并生成一份 Markdown 摘要。可一次性运行，也可定时轮询。

## 它做什么

1. **抓取**：拉取你指定账号的近期推文 + 按关键词搜索（行情策略 / Web3）。
2. **分类打分**：关键词加权识别「交易策略信号」和「Web3 资讯」，过滤噪音。
3. **结构化（可选）**：用 Claude 把策略推文解析成 `方向/入场/目标/止损/置信度`。
4. **入库 + 去重**：存进 SQLite，重复推文不再处理。
5. **生成摘要**：输出 `digest.md`，按类别归档。

## 先了解成本与合规（重要）

- **没有免费读取了**。2026 年起官方 X API 对新开发者是**按量付费**：读一条推文约
  **$0.005**，每月硬上限 200 万条。粗估月成本 ≈ `(账号数×轮询次数 + 搜索条数) × 单次返回量 × $0.005`。
- **想压成本**：第三方数据 API（TwitterAPI.io / GetXAPI / Sorsa 等）约 **$0.15/千条**，
  比官方便宜一个量级。把 `config.yaml` 里 `provider` 改成 `thirdparty`，
  并在 `fetcher.py` 的 `ThirdPartyXClient` 里按所选供应商文档补全字段映射即可。
- **不要绕过接口直接爬网页**：违反 X 服务条款，且反爬会让脚本频繁失效。本项目只走合规 API。
- 若以后要加「自动转发/发帖」，务必保留人工确认环节——发布类操作不应全自动。

## 安装

```bash
pip install -r requirements.txt
```

## 配置

编辑 `config.yaml`：
- `accounts`：要盯的 handle（去掉 @），把 Serenity 等账号填进去。
- `searches`：关键词查询，用 X recent-search 语法（支持 `$BTC`、`-is:retweet`、`lang:en` 等）。
- `fetch.poll_interval_minutes`：`0` = 跑一次退出；`60` = 每小时抓一次。
- `classify.use_llm`：设 `true` 启用 Claude 结构化抽取。

## 运行

```bash
export X_BEARER_TOKEN="你的官方 API Bearer Token"
export ANTHROPIC_API_KEY="..."        # 仅 use_llm: true 时需要

python main.py                        # 用默认 config.yaml
python main.py my_config.yaml         # 指定其它配置
```

跑完看 `digest.md`，明细在 `x_agent.db`（SQLite，可用任意客户端查询）。

## 拿 Token

X 开发者后台（developer.x.com）建 Project + App → Keys and Tokens → 复制 Bearer Token。
新账号默认按量付费，记得在控制台**设置消费上限**，避免搜索量过大跑飞账单。

## 项目结构

```
x_agent_proj/
├── main.py              # 编排 + 定时循环
├── config.yaml          # 账号 / 关键词 / 参数
├── requirements.txt
└── x_agent/
    ├── fetcher.py       # 官方 API 客户端 + 第三方适配器骨架
    ├── classifier.py    # 关键词打分 + 可选 LLM 抽取
    ├── storage.py       # SQLite 去重存储
    └── digest.py        # 生成 Markdown 摘要
```

## 容易扩展的方向

- **推送**：在 `run_once` 命中信号后接 Telegram / 飞书 / 邮件机器人。
- **打分调优**：改 `classifier.py` 里的关键词权重和阈值。
- **去广告/防钓鱼**：对 Web3 推文加一层「可疑 airdrop 链接」过滤。
- **回看面板**：在 SQLite 上套个简单 Web 看板看历史信号。
