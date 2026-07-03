#!/usr/bin/env bash
# ==============================================================================
# 夜间跑批：数据增量 → 新鲜度检查 → LLM 步骤(走 claude CLI 订阅，不需 API key)
#
# 设计目标：幂等、可重入保护(锁文件)、单点失败不中断整批(分段 set +e)、全程日志。
#
# 用法：
#   scripts/nightly_pipeline.sh              # 全量：data + freshness + llm
#   scripts/nightly_pipeline.sh --data-only  # 只跑纯 python 数据增量+新鲜度(可无人值守 cron)
#   scripts/nightly_pipeline.sh --llm-only    # 只跑 LLM 步骤(需登录会话/OAuth token)
#
# 环境坑(务必遵守，见 docs/ops_automation.md)：
#   * 必须用项目 .venv/bin/python(3.14)，别用坏掉的 anaconda 3.8。
#   * clash 代理会劫持 localhost → 本地 sqlite/回环一律绕代理(NO_PROXY)。
#   * claude CLI 订阅态凭据在 macOS 钥匙串里，裸 cron 环境读不到(会 403 / not logged in)。
#     → LLM 步骤要么在登录会话跑，要么用 CLAUDE_CODE_OAUTH_TOKEN(见 docs)。
# ==============================================================================
set -u  # 未定义变量报错；注意：故意不 set -e，各段自行判定，单段失败不拖垮整批

# ── 路径(全绝对，cron 下 cwd 不可靠) ─────────────────────────────────────────
ROOT="/Users/pany19/Documents/x_agent_proj"
PY="$ROOT/.venv/bin/python"
LOG_DIR="$ROOT/output/logs"
LOCK="$ROOT/output/.nightly.lock"
STAMP="$(date +%Y%m%d)"
LOG="$LOG_DIR/nightly_${STAMP}.log"

# claude CLI 绝对路径(nvm 装的，cron PATH 里通常没有)
CLAUDE_BIN="${CLAUDE_BIN:-/Users/pany19/.nvm/versions/node/v22.22.2/bin/claude}"

# 保护本地回环不被 clash 劫持；证书用 certifi
export NO_PROXY="localhost,127.0.0.1,::1${NO_PROXY:+,$NO_PROXY}"
export no_proxy="$NO_PROXY"
export SSL_CERT_FILE="${SSL_CERT_FILE:-$("$PY" -c 'import certifi;print(certifi.where())' 2>/dev/null)}"

MODE="all"
[ "${1:-}" = "--data-only" ] && MODE="data"
[ "${1:-}" = "--llm-only" ] && MODE="llm"

mkdir -p "$LOG_DIR"

# ── 日志助手 ──────────────────────────────────────────────────────────────────
log()  { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }
red()  { echo "[$(date '+%Y-%m-%d %H:%M:%S')] 🔴 $*" | tee -a "$LOG"; }
sect() { echo | tee -a "$LOG"; log "──────── $* ────────"; }

# ── 锁文件防重入(记录 pid，陈旧锁自动清理) ────────────────────────────────────
if [ -e "$LOCK" ]; then
  old_pid="$(cat "$LOCK" 2>/dev/null || echo '')"
  if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
    log "已有实例在跑 (pid=$old_pid)，退出"; exit 0
  fi
  log "发现陈旧锁 (pid=$old_pid 已不在)，清理后继续"
fi
echo "$$" > "$LOCK"
cleanup() { rm -f "$LOCK"; }
trap cleanup EXIT

FAILED=0  # 汇总是否有段失败(供退出码)

log "=== 夜间跑批开始 (mode=$MODE, pid=$$) ==="
cd "$ROOT" || { red "cd $ROOT 失败"; exit 2; }

# ══════════════════════════════════════════════════════════════════════════════
# 阶段 A：数据增量(纯 python，无需 LLM/联网订阅)
# ══════════════════════════════════════════════════════════════════════════════
run_data() {
  sect "阶段A 数据增量"
  # A1 股票(A/港/美) —— 逐市场跑，单市场失败不影响其它
  for m in a hk us; do
    log "daily_update.py --market $m"
    "$PY" scripts/daily_update.py --market "$m" --days 7 >>"$LOG" 2>&1 \
      || { red "daily_update $m 失败(见日志)"; FAILED=1; }
  done
  # A2 新市场(crypto/index/fx/bond/etf/fred...)
  log "daily_update_markets.py --market all"
  "$PY" scripts/daily_update_markets.py --market all --days 15 >>"$LOG" 2>&1 \
    || { red "daily_update_markets 失败(见日志)"; FAILED=1; }
}

# ══════════════════════════════════════════════════════════════════════════════
# 阶段 B：数据新鲜度检查(#2)，滞后就标红。串在 data 之后，反映增量后的真实状态
# ══════════════════════════════════════════════════════════════════════════════
run_freshness() {
  sect "阶段B 数据新鲜度"
  "$PY" scripts/data_freshness.py >>"$LOG" 2>&1
  local rc=$?
  if [ "$rc" -ne 0 ]; then
    red "数据新鲜度检查发现滞后/异常源(退出码=$rc)，详见上方报告"
    FAILED=1
  else
    log "数据新鲜度：全部告警级数据源均新鲜 ✅"
  fi
}

# ══════════════════════════════════════════════════════════════════════════════
# 阶段 C：LLM 步骤 —— 用 claude CLI 无头模式(-p)跑，走订阅、不需 ANTHROPIC_API_KEY
#
# 本步替代 digest.py 里"LLM 解读未启用"的死路径(那条路径 hard-wire 到 anthropic
# .Anthropic()，而 ANTHROPIC_API_KEY 是空的)。这里改由 claude -p 生成日报注解。
# 说明：classifier 的逐条结构化抽取、run_persona.py 仍 hard-wire 到 anthropic SDK，
# 未在此接入(需改代码走 CLI)，属遗留(见 docs)。
# ══════════════════════════════════════════════════════════════════════════════
llm_available() {
  # 探测 claude CLI 订阅态是否可用(能拿到回复且非 error)
  local out
  out="$("$CLAUDE_BIN" -p "reply with exactly: PONG" --output-format json 2>/dev/null)" || return 1
  echo "$out" | "$PY" -c 'import sys,json;d=json.load(sys.stdin);sys.exit(0 if not d.get("is_error") and "PONG" in (d.get("result") or "") else 1)' 2>/dev/null
}

run_llm() {
  sect "阶段C LLM 步骤(claude -p)"
  if ! llm_available; then
    red "claude CLI 订阅态不可用(裸 cron 环境常见：钥匙串读不到/403)。"
    log "  → 跳过 LLM 步骤。修复见 docs/ops_automation.md(登录会话跑 或 CLAUDE_CODE_OAUTH_TOKEN)"
    FAILED=1
    return
  fi
  log "claude CLI 可用 ✅，生成 LLM 日报注解"

  local digest="$ROOT/output/digest.md"
  local fresh_txt="$LOG_DIR/_freshness_${STAMP}.txt"
  local brief="$ROOT/output/nightly_brief_${STAMP}.md"

  # 抓一份当日新鲜度纯文本喂给 LLM
  "$PY" scripts/data_freshness.py > "$fresh_txt" 2>/dev/null

  local prompt="你是投研运维助手。下面是今天的数据新鲜度报告，"
  prompt+="以及(若有)最新的资讯摘要 digest。请用中文写一段简短日报："
  prompt+="1) 数据健康：哪些源滞后、影响哪些下游；2) 若有 digest，提炼 3-5 条要点解读。"
  prompt+="只输出 markdown 正文，不要客套。\n\n"
  prompt+="=== 数据新鲜度 ===\n$(cat "$fresh_txt")\n\n"
  if [ -f "$digest" ]; then
    prompt+="=== 资讯摘要(截断) ===\n$(head -c 6000 "$digest")\n"
  else
    prompt+="(今日无 digest.md)\n"
  fi

  local out
  out="$(printf '%b' "$prompt" | "$CLAUDE_BIN" -p --output-format json 2>>"$LOG")"
  if [ -z "$out" ]; then
    red "claude -p 无输出，LLM 步骤失败"; FAILED=1; return
  fi
  # 解析 .result 落盘
  echo "$out" | "$PY" -c '
import sys, json, datetime
d = json.load(sys.stdin)
if d.get("is_error"):
    print("LLM_ERROR", file=sys.stderr); sys.exit(1)
open(sys.argv[1], "w").write(
    f"# 夜间 LLM 日报 · {datetime.date.today()}\n\n" + (d.get("result") or ""))
' "$brief" 2>>"$LOG" \
    && log "LLM 日报已写入 $brief" \
    || { red "解析/落盘 LLM 输出失败"; FAILED=1; }
}

# ── 编排 ──────────────────────────────────────────────────────────────────────
case "$MODE" in
  data) run_data; run_freshness ;;
  llm)  run_llm ;;
  all)  run_data; run_freshness; run_llm ;;
esac

sect "收尾"
if [ "$FAILED" -ne 0 ]; then
  red "跑批完成，但有段失败/滞后(见日志 $LOG)"; exit 1
fi
log "=== 夜间跑批全部成功 ==="; exit 0
