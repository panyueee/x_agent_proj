# 运维自动化：数据新鲜度监控 + 夜间跑批

两块：① `scripts/data_freshness.py` 各数据源新鲜度体检；② `scripts/nightly_pipeline.sh` 夜间跑批（数据增量 → 新鲜度 → LLM 步骤）。LLM 步骤走 **claude CLI 订阅、不需 ANTHROPIC_API_KEY**（用户选定方案）。

## 1. 数据新鲜度监控 `scripts/data_freshness.py`
遍历各市场 parquet / price_bars / 宏观 / rag.db 各来源，报**最新日期 + 落后今天几天**，超阈值标红。退出码：全新鲜=0、有滞后=1（可做 cron 告警）。
```bash
.venv/bin/python scripts/data_freshness.py            # 人读报告
.venv/bin/python scripts/data_freshness.py --strict   # 有滞后退出码1
```
当前状态（今日增量后）：行情各市场均 🟢（落后 1-3 天在阈值内）；**已知红项**：macro PPI/CPI parquet 停 2025-09（用 akshare 新鲜序列重刷，见 M5 清单 P4）。

## 2. 夜间跑批 `scripts/nightly_pipeline.sh`
幂等 + 锁文件防重入 + 分段 set +e（单段失败不拖垮整批）+ 全程日志（output/logs/nightly_*.log）。三模式：
```bash
scripts/nightly_pipeline.sh              # 全量：data + freshness + llm
scripts/nightly_pipeline.sh --data-only  # 只纯 python 数据增量+新鲜度（裸 cron 安全）
scripts/nightly_pipeline.sh --llm-only   # 只 LLM 步骤（需登录会话/OAuth token）
```
- **阶段A 数据**：daily_update.py（A/港/美股）+ daily_update_markets.py（crypto/指数/汇率/债/ETF/REITs/FRED/cb）
- **阶段B 新鲜度**：调 data_freshness，滞后标红进日志
- **阶段C LLM**：`claude -p` 无头模式生成"数据健康 + digest 要点"日报（output/nightly_brief_*.md），走订阅无需 key；有可用性探测 `llm_available()`，不可用则**优雅跳过**不崩。

## 3. ⚠️ claude CLI 无头模式的关键限制
`claude -p` 的订阅凭据在 **macOS 钥匙串**里，**裸 cron 环境读不到**（会 403 / not logged in）。所以：
- **阶段A/B（纯 python）可以放心进裸 cron**。
- **阶段C（LLM）不能靠裸 cron**。两条修复路径：
  1. 在**登录会话**里跑（`--llm-only`），或用 launchd 的 user-session agent（有钥匙串访问）。
  2. 设 `CLAUDE_CODE_OAUTH_TOKEN` 环境变量给 cron（若你的版本支持长期 token）。

## 4. 建议 crontab（**未安装，待你审后装**）
```cron
# 每晚 20:30 数据增量 + 新鲜度（裸 cron 安全，无 LLM）
30 20 * * * /Users/pany19/Documents/x_agent_proj/scripts/nightly_pipeline.sh --data-only >> /Users/pany19/Documents/x_agent_proj/output/logs/cron.log 2>&1
```
LLM 日报建议放**登录会话**（手动或 launchd user-agent），不进裸 cron：
```bash
# 早上在已登录终端里
scripts/nightly_pipeline.sh --llm-only
```
安装：`crontab -e` 粘上一行；停用：`crontab -e` 删除或 `crontab -r`。查看：`crontab -l`。

## 5. 遗留 TODO
- **classifier 逐条结构化抽取 + run_persona.py 仍 hard-wire 到 anthropic SDK**（ANTHROPIC_API_KEY 空→死路径）。要么恢复 key，要么改这两处走 `claude -p`。当前 nightly 只把 digest 日报注解接了 CLI。
- QA 门禁（report_qa）可串进 nightly 阶段C 后对 digest/risk 做 `--strict` 校验。
- macro parquet 重刷（M5 清单 P4）。
