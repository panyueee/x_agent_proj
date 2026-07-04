#!/bin/bash
# 把项目大资产(gitignore 的 output/ + data/ + .env)增量同步到 M5。
# 代码走 git;这里只搬 rag.db/x_agent.db/断点/feeds/待ASR音频/密钥等 git 不带的东西。
# rsync 增量:只传变化的文件,可反复跑做同步。断了重跑 --partial 接着传。
#
# 用法:
#   scripts/push_to_m5.sh            # 精简:带 whisper 模型、不带行情缓存(~4.3G首次)
#   WITH_STOCK=1 scripts/push_to_m5.sh   # 连行情缓存 data/stock_history 一起带(+2G)
#   NO_MODELS=1  scripts/push_to_m5.sh   # 连 whisper 模型也不带(M5 重下,-1.5G)
#   DRY=1 scripts/push_to_m5.sh       # 只看会传哪些、不真传
set -euo pipefail

M5_USER="${M5_USER:-panyue}"
M5_IP="${M5_IP:-192.168.11.190}"
DEST_DIR="${DEST_DIR:-~/Documents/x_agent_proj/}"
SRC="$(cd "$(dirname "$0")/.." && pwd)/"

EXCLUDES=(
  --exclude='.venv' --exclude='bin'
  --exclude='data/_mag_tmp.pdf' --exclude='data/_netdisk_tmp.pdf'
  --exclude='__pycache__' --exclude='.DS_Store'
  --exclude='*.pyc'
)
# 行情缓存 2G:可在 M5 重拉,默认不带
[ "${WITH_STOCK:-0}" = "1" ] || EXCLUDES+=(--exclude='data/stock_history')
# whisper 模型 1.5G:默认带(ASR 免下载),NO_MODELS=1 则不带
[ "${NO_MODELS:-0}" = "1" ] && EXCLUDES+=(--exclude='models')

# macOS 自带 rsync 2.6.9 不认 --info=progress2(那是 rsync 3.x),用 -P(=--partial --progress)
FLAGS=(-a -P)
# 局域网不加 -z(rag.db/mp3/pdf 压不动,压缩反拖 CPU)
[ "${DRY:-0}" = "1" ] && FLAGS+=(--dry-run)

echo "同步 $SRC → ${M5_USER}@${M5_IP}:${DEST_DIR}"
echo "排除: ${EXCLUDES[*]}"
rsync "${FLAGS[@]}" "${EXCLUDES[@]}" "$SRC" "${M5_USER}@${M5_IP}:${DEST_DIR}"
echo "✅ 同步完成"
