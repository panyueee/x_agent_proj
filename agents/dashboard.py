"""Agent 开发看板：实时显示 worktree 状态，一键启动开发，实时看日志，支持干预。

启动：
    source .venv/bin/activate
    python agents/dashboard.py

访问：http://localhost:8765
"""
import os
import subprocess
import threading
import time
import json
from pathlib import Path
from typing import List

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
import uvicorn

# ── 路径配置 ──────────────────────────────────────────────────────────────────

ROOT      = Path(__file__).parent.parent
NOTES_DIR = Path(__file__).parent / "notes"
LOGS_DIR  = Path(__file__).parent / "logs"
NOTES_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

CLAUDE_BIN = "/Users/pany19/.nvm/versions/node/v22.22.2/bin/claude"

AGENTS = [
    {"id": "x",       "name": "X (Twitter)",  "branch": "feature/x",
     "worktree": ".claude/worktrees/feature-x"},
    {"id": "xhs",     "name": "小红书",         "branch": "feature/xhs",
     "worktree": ".claude/worktrees/feature-xhs"},
    {"id": "tgb",     "name": "淘股吧",         "branch": "feature/tgb",
     "worktree": ".claude/worktrees/feature-tgb"},
    {"id": "finance", "name": "金融行情",        "branch": "feature/finance",
     "worktree": ".claude/worktrees/feature-finance"},
]

# ── 运行状态（内存，进程重启后重置）──────────────────────────────────────────

_run = {
    "status": "idle",    # idle | running | done | error
    "pid":    None,
    "log":    [],        # list of str lines
    "proc":   None,      # subprocess.Popen
}
_log_lock = threading.Lock()


def _append_log(line: str):
    with _log_lock:
        _run["log"].append(line)
        # 只保留最近 2000 行
        if len(_run["log"]) > 2000:
            _run["log"] = _run["log"][-2000:]


# ── Git 工具 ──────────────────────────────────────────────────────────────────

def _git(args: List[str], cwd: Path) -> str:
    try:
        r = subprocess.run(["git"] + args, cwd=cwd,
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    except Exception:
        return ""


def _agent_status(agent: dict) -> dict:
    wt = ROOT / agent["worktree"]
    if not wt.exists():
        return {**agent, "exists": False, "last_commit": "", "last_commit_time": "",
                "dirty_files": [], "ahead": 0, "status": "no_worktree"}
    last_commit      = _git(["log", "-1", "--pretty=%s"], wt)
    last_commit_time = _git(["log", "-1", "--pretty=%cr"], wt)
    dirty = _git(["status", "--short"], wt)
    dirty_files = [l.strip() for l in dirty.splitlines() if l.strip()] if dirty else []
    try:
        ahead = int(_git(["rev-list", "--count", "main..HEAD"], wt))
    except ValueError:
        ahead = 0
    status = "dirty" if dirty_files else ("ready" if ahead > 0 else "clean")
    return {**agent, "exists": True, "last_commit": last_commit,
            "last_commit_time": last_commit_time, "dirty_files": dirty_files,
            "ahead": ahead, "status": status}


# ── Notes ─────────────────────────────────────────────────────────────────────

def _read_note(agent_id: str) -> str:
    f = NOTES_DIR / f"{agent_id}.md"
    return f.read_text(encoding="utf-8") if f.exists() else ""


def _write_note(agent_id: str, content: str):
    (NOTES_DIR / f"{agent_id}.md").write_text(content, encoding="utf-8")


def _build_prompt() -> str:
    lines = ["开始新一轮并行开发，各模块指令如下：\n"]
    for a in AGENTS:
        note = _read_note(a["id"]).strip()
        lines.append(f"**{a['name']}**：{note if note else '按默认方向继续优化'}")
    return "\n".join(lines)


# ── Claude 进程管理 ───────────────────────────────────────────────────────────

def _stream_proc(proc):
    """后台线程：把进程 stdout 逐行写入 _run['log']。"""
    for raw in iter(proc.stdout.readline, ""):
        line = raw.rstrip("\n")
        if line:
            _append_log(line)
    proc.wait()
    _run["status"] = "done" if proc.returncode == 0 else "error"
    _run["proc"]   = None
    _append_log(f"\n── 进程结束 (exit {proc.returncode}) ──")


def _launch_claude(prompt: str):
    if _run["status"] == "running":
        return False, "已有任务在运行"
    with _log_lock:
        _run["log"] = []
    _run["status"] = "running"

    _append_log(f"── 启动 Claude Code ──\n{prompt}\n── 开始执行 ──\n")

    proc = subprocess.Popen(
        [CLAUDE_BIN, "--dangerously-skip-permissions", "-p", prompt],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    _run["proc"] = proc
    _run["pid"]  = proc.pid
    threading.Thread(target=_stream_proc, args=(proc,), daemon=True).start()
    return True, proc.pid


def _intervene(message: str):
    """用 --continue 把干预消息追加进同一个 Claude 会话。"""
    if _run["status"] == "running":
        return False, "有任务正在运行，请等待完成后再干预"

    _run["status"] = "running"
    _append_log(f"\n── 干预消息 ──\n{message}\n── 继续执行 ──\n")

    proc = subprocess.Popen(
        [CLAUDE_BIN, "--dangerously-skip-permissions", "--continue", "-p", message],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    _run["proc"] = proc
    _run["pid"]  = proc.pid
    threading.Thread(target=_stream_proc, args=(proc,), daemon=True).start()
    return True, proc.pid


# ── FastAPI ───────────────────────────────────────────────────────────────────

app = FastAPI(title="Agent 开发看板")


class NotePayload(BaseModel):
    content: str


class IntervenePayload(BaseModel):
    message: str


@app.get("/api/status")
def get_status():
    return [_agent_status(a) for a in AGENTS]


@app.get("/api/run-status")
def run_status():
    return {"status": _run["status"], "pid": _run["pid"]}


@app.get("/api/notes/{agent_id}")
def get_note(agent_id: str):
    if agent_id not in {a["id"] for a in AGENTS}:
        raise HTTPException(404)
    return {"content": _read_note(agent_id)}


@app.post("/api/notes/{agent_id}")
def save_note(agent_id: str, payload: NotePayload):
    if agent_id not in {a["id"] for a in AGENTS}:
        raise HTTPException(404)
    _write_note(agent_id, payload.content)
    return {"ok": True}


@app.post("/api/launch")
def launch():
    prompt = _build_prompt()
    ok, info = _launch_claude(prompt)
    if not ok:
        raise HTTPException(409, info)
    return {"ok": True, "pid": info}


@app.post("/api/intervene")
def intervene(payload: IntervenePayload):
    ok, info = _intervene(payload.message)
    if not ok:
        raise HTTPException(409, info)
    return {"ok": True, "pid": info}


@app.post("/api/stop")
def stop():
    proc = _run.get("proc")
    if proc and _run["status"] == "running":
        proc.terminate()
        _run["status"] = "idle"
        _append_log("\n── 已手动停止 ──")
        return {"ok": True}
    return {"ok": False, "msg": "没有运行中的任务"}


@app.get("/api/logs/stream")
def logs_stream():
    """SSE：实时推送新增日志行。"""
    def event_gen():
        sent = 0
        while True:
            with _log_lock:
                lines = _run["log"]
                new = lines[sent:]
                sent = len(lines)
            for line in new:
                yield f"data: {json.dumps(line, ensure_ascii=False)}\n\n"
            time.sleep(0.3)
    return StreamingResponse(event_gen(),
                             media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return HTML


# ── HTML ──────────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<title>Agent 开发看板</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: #0f1117; color: #e2e8f0; min-height: 100vh; padding: 24px;
}
header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 24px; gap: 12px; flex-wrap: wrap;
}
h1 { font-size: 20px; font-weight: 600; color: #f8fafc; }
.header-right { display: flex; align-items: center; gap: 12px; }
.refresh-info { font-size: 12px; color: #64748b; }

/* 卡片网格 */
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 14px; margin-bottom: 24px;
}
.card {
  background: #1e2130; border: 1px solid #2d3348;
  border-radius: 12px; padding: 18px;
  display: flex; flex-direction: column; gap: 12px;
}
.card-header { display: flex; align-items: center; gap: 8px; }
.card-title { font-size: 15px; font-weight: 600; }
.badge { font-size: 11px; font-weight: 500; padding: 2px 8px; border-radius: 20px; }
.badge-clean        { background:#14532d; color:#4ade80; }
.badge-ready        { background:#1e3a5f; color:#60a5fa; }
.badge-dirty        { background:#451a03; color:#fb923c; }
.badge-no_worktree  { background:#3f3f46; color:#a1a1aa; }
.section-label { font-size:11px; font-weight:500; text-transform:uppercase;
  letter-spacing:.05em; color:#64748b; margin-bottom:3px; }
.commit-msg  { font-size:13px; color:#cbd5e1; line-height:1.4; }
.commit-time { font-size:11px; color:#64748b; margin-top:2px; }
.branch-tag  { font-size:11px; background:#1e293b; color:#94a3b8;
  padding:2px 7px; border-radius:4px; font-family:monospace; display:inline-block; }
.dirty-item  { font-size:11px; font-family:monospace; color:#fbbf24;
  background:#1c1a08; padding:2px 6px; border-radius:4px; }
.ahead-pill  { font-size:11px; background:#1e3a5f; color:#60a5fa;
  padding:2px 7px; border-radius:10px; display:inline-block; }
.divider     { border:none; border-top:1px solid #2d3348; }
textarea {
  width:100%; background:#0f1117; border:1px solid #2d3348;
  border-radius:8px; color:#e2e8f0; font-size:13px;
  padding:10px; resize:vertical; min-height:70px;
  font-family:inherit; line-height:1.5; outline:none;
  transition:border-color .15s;
}
textarea:focus { border-color:#3b82f6; }
.btn-save {
  align-self:flex-end; background:#334155; color:#cbd5e1;
  border:none; border-radius:6px; padding:5px 14px;
  font-size:12px; cursor:pointer; transition:background .15s;
}
.btn-save:hover { background:#475569; }
.btn-save.saved { background:#15803d; color:#fff; }

/* 启动按钮 */
.btn-launch {
  background: linear-gradient(135deg,#7c3aed,#2563eb);
  color:#fff; border:none; border-radius:8px;
  padding:9px 22px; font-size:14px; font-weight:600;
  cursor:pointer; transition:opacity .15s; white-space:nowrap;
}
.btn-launch:hover { opacity:.85; }
.btn-launch:disabled { opacity:.4; cursor:default; }

/* 运行状态指示 */
.run-badge { font-size:12px; font-weight:600; padding:4px 12px;
  border-radius:20px; }
.run-idle    { background:#1e293b; color:#64748b; }
.run-running { background:#451a03; color:#fb923c; }
.run-done    { background:#14532d; color:#4ade80; }
.run-error   { background:#450a0a; color:#f87171; }

/* 日志面板 */
.log-panel {
  background:#0a0c10; border:1px solid #2d3348; border-radius:12px;
  display:flex; flex-direction:column; overflow:hidden;
}
.log-header {
  display:flex; align-items:center; justify-content:space-between;
  padding:12px 16px; border-bottom:1px solid #2d3348;
  background:#111827;
}
.log-header-left { display:flex; align-items:center; gap:10px; }
.log-title { font-size:13px; font-weight:600; color:#94a3b8; }
.log-body {
  font-family:"SF Mono", "Fira Code", monospace; font-size:12px;
  line-height:1.6; color:#a3e635; padding:14px 16px;
  height:340px; overflow-y:auto; white-space:pre-wrap; word-break:break-all;
}
.log-body .dim   { color:#4b5563; }
.log-body .info  { color:#60a5fa; }
.log-body .warn  { color:#fbbf24; }
.log-body .err   { color:#f87171; }

/* 干预输入框 */
.intervene-row {
  display:flex; gap:8px; padding:12px 16px;
  border-top:1px solid #2d3348; background:#111827;
}
.intervene-input {
  flex:1; background:#0f1117; border:1px solid #2d3348;
  border-radius:8px; color:#e2e8f0; font-size:13px;
  padding:8px 12px; font-family:inherit; outline:none;
  transition:border-color .15s;
}
.intervene-input:focus { border-color:#3b82f6; }
.btn-send {
  background:#7c3aed; color:#fff; border:none;
  border-radius:8px; padding:8px 18px; font-size:13px;
  font-weight:600; cursor:pointer; white-space:nowrap;
  transition:background .15s;
}
.btn-send:hover { background:#6d28d9; }
.btn-send:disabled { opacity:.4; cursor:default; }
.btn-stop {
  background:#7f1d1d; color:#fca5a5; border:none;
  border-radius:8px; padding:8px 14px; font-size:13px;
  cursor:pointer; transition:background .15s;
}
.btn-stop:hover { background:#991b1b; }
</style>
</head>
<body>
<header>
  <h1>🤖 Agent 开发看板</h1>
  <div class="header-right">
    <span class="refresh-info" id="refresh-info">30s 后刷新状态</span>
    <span class="run-badge run-idle" id="run-badge">空闲</span>
    <button class="btn-launch" id="btn-launch" onclick="launchDev()">🚀 启动新轮开发</button>
  </div>
</header>

<div class="grid" id="grid">
  <div class="card" style="color:#64748b">加载中...</div>
</div>

<!-- 日志面板 -->
<div class="log-panel">
  <div class="log-header">
    <div class="log-header-left">
      <span class="log-title">📋 执行日志</span>
    </div>
    <button class="btn-stop" onclick="stopRun()">⏹ 停止</button>
  </div>
  <div class="log-body" id="log-body"><span class="dim">等待启动...</span></div>
  <div class="intervene-row">
    <input class="intervene-input" id="intervene-input"
           placeholder="干预指令，例如：先只做 finance 模块，其他跳过..."
           onkeydown="if(event.key==='Enter')sendIntervene()">
    <button class="btn-send" id="btn-send" onclick="sendIntervene()">发送</button>
  </div>
</div>

<script>
const AGENTS = ['x','xhs','tgb','finance'];
const LABELS = {clean:'同步',ready:'待 merge',dirty:'开发中',no_worktree:'未创建'};
const notes = {};
let logES = null;

// ── 状态卡片 ──────────────────────────────────────────────────────────────────

async function loadNotes() {
  await Promise.all(AGENTS.map(async id => {
    const r = await fetch('/api/notes/' + id);
    notes[id] = (await r.json()).content || '';
  }));
}

function renderCard(s) {
  const dirty = s.dirty_files && s.dirty_files.length
    ? s.dirty_files.map(f => `<div class="dirty-item">${f}</div>`).join('')
    : '<span style="color:#475569;font-size:12px">无未提交变更</span>';
  const ahead = s.ahead > 0
    ? `<span class="ahead-pill">↑ 领先 main ${s.ahead} 个 commit</span>` : '';
  const note = notes[s.id] || '';
  const body = s.exists ? `
    <div><div class="section-label">分支</div>
      <span class="branch-tag">${s.branch}</span> ${ahead}</div>
    <div><div class="section-label">最后提交</div>
      <div class="commit-msg">${s.last_commit||'—'}</div>
      <div class="commit-time">${s.last_commit_time||''}</div></div>
    <div><div class="section-label">未提交变更</div>
      <div>${dirty}</div></div>` :
    `<p style="color:#64748b;font-size:13px">Worktree 不存在，先运行 new_round.sh</p>`;
  return `<div class="card">
    <div class="card-header">
      <span class="card-title">${s.name}</span>
      <span class="badge badge-${s.status}">${LABELS[s.status]||s.status}</span>
    </div>
    ${body}
    <hr class="divider">
    <div><div class="section-label">下轮指令</div>
      <textarea id="note-${s.id}" placeholder="留下给下轮 agent 的指令...">${note}</textarea>
      <button class="btn-save" id="btn-${s.id}" onclick="saveNote('${s.id}')">保存</button>
    </div>
  </div>`;
}

async function refreshCards() {
  const [statusRes] = await Promise.all([fetch('/api/status'), loadNotes()]);
  const statuses = await statusRes.json();
  document.getElementById('grid').innerHTML = statuses.map(renderCard).join('');
}

async function saveNote(id) {
  const ta  = document.getElementById('note-' + id);
  const btn = document.getElementById('btn-' + id);
  await fetch('/api/notes/' + id, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({content: ta.value})
  });
  btn.textContent = '✓ 已保存'; btn.classList.add('saved');
  setTimeout(() => { btn.textContent = '保存'; btn.classList.remove('saved'); }, 2000);
}

// ── 运行状态同步 ──────────────────────────────────────────────────────────────

const RUN_LABEL = {idle:'空闲', running:'运行中...', done:'已完成', error:'出错'};
const RUN_CLASS = {idle:'run-idle', running:'run-running', done:'run-done', error:'run-error'};

function updateRunBadge(status) {
  const el = document.getElementById('run-badge');
  el.className = 'run-badge ' + (RUN_CLASS[status] || 'run-idle');
  el.textContent = RUN_LABEL[status] || status;
  const btn = document.getElementById('btn-launch');
  btn.disabled = status === 'running';
  document.getElementById('btn-send').disabled = status === 'running';
}

async function pollRunStatus() {
  try {
    const r = await fetch('/api/run-status');
    const d = await r.json();
    updateRunBadge(d.status);
  } catch(e) {}
}

// ── 日志 SSE ──────────────────────────────────────────────────────────────────

function startLogStream() {
  if (logES) { logES.close(); }
  const body = document.getElementById('log-body');
  body.innerHTML = '';
  logES = new EventSource('/api/logs/stream');
  logES.onmessage = e => {
    const line = JSON.parse(e.data);
    const div = document.createElement('div');
    // 简单着色
    if (line.startsWith('──'))        div.className = 'info';
    else if (/error|Error|失败/i.test(line)) div.className = 'err';
    else if (/warn|warning/i.test(line))     div.className = 'warn';
    div.textContent = line;
    body.appendChild(div);
    body.scrollTop = body.scrollHeight;
  };
}

// ── 操作 ──────────────────────────────────────────────────────────────────────

async function launchDev() {
  document.getElementById('btn-launch').disabled = true;
  startLogStream();
  const r = await fetch('/api/launch', {method:'POST'});
  if (!r.ok) {
    const d = await r.json();
    alert('启动失败：' + (d.detail || ''));
    document.getElementById('btn-launch').disabled = false;
  }
  updateRunBadge('running');
}

async function sendIntervene() {
  const inp = document.getElementById('intervene-input');
  const msg = inp.value.trim();
  if (!msg) return;
  inp.value = '';
  startLogStream();
  const r = await fetch('/api/intervene', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({message: msg})
  });
  if (!r.ok) {
    const d = await r.json();
    alert('发送失败：' + (d.detail || ''));
  } else {
    updateRunBadge('running');
  }
}

async function stopRun() {
  await fetch('/api/stop', {method:'POST'});
  updateRunBadge('idle');
}

// ── 主循环 ────────────────────────────────────────────────────────────────────

let countdown = 30;
setInterval(() => {
  countdown--;
  document.getElementById('refresh-info').textContent = countdown + 's 后刷新状态';
  if (countdown <= 0) { countdown = 30; refreshCards(); }
  pollRunStatus();
}, 1000);

startLogStream();
refreshCards();
pollRunStatus();
</script>
</body>
</html>"""

if __name__ == "__main__":
    print("看板启动中... 访问 http://localhost:8765")
    uvicorn.run(app, host="0.0.0.0", port=8765, log_level="warning")
