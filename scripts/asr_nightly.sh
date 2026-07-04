#!/bin/bash
# ASR大师课转写完整流程 — 后台持续处理
# 用法: nohup bash scripts/asr_nightly.sh &

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_FILE="$ROOT/output/asr_nightly.log"
COURSE="70天共读"

function log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

function check_asr_done() {
    # 检查ASR是否全部完成
    DONE_FILE="$ROOT/data/asr_done_${COURSE}.json"
    AUDIO_DIR="$ROOT/data/asr_audio"

    if [ ! -f "$DONE_FILE" ]; then
        return 1  # 未开始
    fi

    TOTAL=$(find "$AUDIO_DIR" -maxdepth 1 -type f \( -iname "*.mp3" -o -iname "*.m4a" -o -iname "*.wav" \) | wc -l)
    DONE=$(python3 -c "import json; print(len(json.load(open('$DONE_FILE'))))" 2>/dev/null || echo 0)

    [ "$TOTAL" -eq "$DONE" ] && [ "$TOTAL" -gt 0 ]
}

trap "log '❌ 脚本出错'; exit 1" ERR

log "=========================================="
log "🚀 ASR Nightly 流程启动"
log "=========================================="

# 第1阶段：批量转写（如果未完成）
if ! check_asr_done; then
    log "📝 阶段1: 启动ASR批量转写"
    log "进程: .venv/bin/python scripts/transcribe_asr_m5_parallel.py --course '$COURSE' --workers 14"

    "$ROOT/.venv/bin/python" "$ROOT/scripts/transcribe_asr_m5_parallel.py" \
        --course "$COURSE" --workers 14 \
        2>&1 | tee -a "$LOG_FILE"
else
    log "⏭️  ASR转写已完成，跳过"
fi

# 第2阶段：质量验证和报告
log ""
log "✔️  阶段2: 质量验证"
"$ROOT/.venv/bin/python" "$ROOT/scripts/monitor_asr.py" 2>&1 | tee -a "$LOG_FILE"

# 第3阶段：向量化和统计
log ""
log "🔍 阶段3: 向量化和统计"
"$ROOT/.venv/bin/python" "$ROOT/scripts/asr_post_process.py" "$COURSE" 2>&1 | tee -a "$LOG_FILE"

# 第4阶段：Git提交
log ""
log "💾 阶段4: Git提交转写成果"
cd "$ROOT"
git add -A output/asr_report_* data/asr_done_* data/asr_rejected_* output/rag.db 2>/dev/null || true

COMMIT_MSG="ASR: Complete 70-day course transcription and ingestion

- Processed 36 audio files from 华尔街见闻大师课
- Generated vector embeddings for RAG
- Quality filtering applied
- Details in output/asr_report_*.md"

if git diff --cached --quiet; then
    log "✅ 无新改动，跳过提交"
else
    git commit -m "$COMMIT_MSG" \
        -m "Co-Authored-By: ASR Nightly Pipeline <asr@m5.local>" \
        2>&1 | tee -a "$LOG_FILE" || log "⚠️  提交失败（可能无网络）"
fi

log ""
log "=========================================="
log "✅ ASR Nightly 流程完成"
log "=========================================="
log "总耗时: $(date)"
