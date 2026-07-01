#!/bin/bash
# 知识星球研报「每晚慢速小批」抓取——频率检测下的安全节奏。
# 30s 全局间隔 + 命中 1059 自动停整轮 + 断点续传(data/zsxq_files_done.json)。
# 每晚只抓一小批(--limit 60，约30分钟)，日积月累把历史抓完；抓完后 done 不再增长。
# 由 crontab 每晚调用。手动跑：bash scripts/zsxq_nightly.sh
set -euo pipefail
cd /Users/pany19/Documents/x_agent_proj || exit 1
set -a; source .env 2>/dev/null || true; set +a
export ZSXQ_MIN_INTERVAL="${ZSXQ_MIN_INTERVAL:-30}"

echo "===== $(date '+%F %T') 知识星球夜间抓取开始 ====="
.venv/bin/python scripts/ingest_zsxq_files.py \
  --group-id 28888222154481 --group-name "180K Research" --limit 60
echo "===== $(date '+%F %T') 结束 ====="
