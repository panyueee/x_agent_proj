#!/bin/bash
# 书库 OCR 跑完后自动接力：确保 23 本入完 → tooze 全量 → nxny 钟正生/牟一凌。
# 全程串行，避免抢 rag.db 写锁。由会话外 nohup 独立跑，会话死也不影响。
set -u
P=/Users/pany19/Documents/x_agent_proj
PY=$P/.venv/bin/python
CLOG=$P/data/_run_chain.log
SLOG=$P/data/_run_shuku.log
cd "$P" || exit 1
[ -f "$P/.env" ] && { set -a; . "$P/.env"; set +a; }

log() { echo "[$(date '+%H:%M:%S')] $*" >> "$CLOG"; }

# 1. 等当前书库进程(pid 3286)结束
log "等书库 pid ${SHUKU_PID:-3286} 结束..."
while kill -0 "${SHUKU_PID:-3286}" 2>/dev/null; do sleep 120; done
log "书库进程已退出"

# 2. 断点续传把 23 本补齐（最多再试 3 轮，无进展就停）
done_n() { "$PY" -c "import json;print(len(json.load(open('$P/data/netdisk_ingest_done.json'))))" 2>/dev/null || echo 0; }
for i in 1 2 3; do
  n=$(done_n)
  log "书库已入 $n/23"
  [ "$n" -ge 23 ] && break
  log "续传第 $i 轮..."
  "$PY" scripts/ingest_netdisk_folder.py --dir /书库 >> "$SLOG" 2>&1
  m=$(done_n)
  [ "$m" -le "$n" ] && { log "无进展($n→$m)，停止续传"; break; }
done
log "书库最终入库 $(done_n)/23"

# 3. tooze 全量（~507 免费篇）
log "启动 tooze 全量..."
"$PY" scripts/ingest_global_gurus.py --sources tooze >> "$CLOG" 2>&1
log "tooze 完成"

# 4. nxny 钟正生 + 牟一凌
log "启动 nxny 钟正生,牟一凌..."
"$PY" scripts/ingest_chief_web.py --sources nxny --authors 钟正生,牟一凌 >> "$CLOG" 2>&1
log "nxny 完成"

log "===== CHAIN DONE ====="
