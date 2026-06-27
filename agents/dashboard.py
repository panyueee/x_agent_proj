"""Agent 开发看板：实时显示四个 worktree 的状态，支持留指令给下轮开发。

启动：
    source .venv/bin/activate
    python agents/dashboard.py

访问：http://localhost:8765
"""
import os
import subprocess
import datetime
from pathlib import Path
from typing import Dict, List

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
import uvicorn

# ── 路径配置 ──────────────────────────────────────────────────────────────────

ROOT       = Path(__file__).parent.parent          # 项目根目录
NOTES_DIR  = Path(__file__).parent / "notes"
NOTES_DIR.mkdir(exist_ok=True)

AGENTS = [
    {"id": "x",       "name": "X (Twitter)",  "branch": "feature/x",
     "worktree": ".claude/worktrees/feature-x",
     "files": ["x_agent/fetcher.py", "x_agent/classifier.py"]},
    {"id": "xhs",     "name": "小红书",         "branch": "feature/xhs",
     "worktree": ".claude/worktrees/feature-xhs",
     "files": ["x_agent/xhs_fetcher.py"]},
    {"id": "tgb",     "name": "淘股吧",         "branch": "feature/tgb",
     "worktree": ".claude/worktrees/feature-tgb",
     "files": ["x_agent/_tgb_scraper.py", "x_agent/tgb_fetcher.py"]},
    {"id": "finance", "name": "金融行情",        "branch": "feature/finance",
     "worktree": ".claude/worktrees/feature-finance",
     "files": ["x_agent/finance_fetcher.py", "x_agent/finance_chart.py"]},
]

# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _git(args: List[str], cwd: Path) -> str:
    try:
        r = subprocess.run(
            ["git"] + args, cwd=cwd,
            capture_output=True, text=True, timeout=5
        )
        return r.stdout.strip()
    except Exception:
        return ""


def _worktree_path(agent: dict) -> Path:
    return ROOT / agent["worktree"]


def _agent_status(agent: dict) -> dict:
    wt = _worktree_path(agent)
    exists = wt.exists()

    if not exists:
        return {
            "id": agent["id"],
            "name": agent["name"],
            "branch": agent["branch"],
            "exists": False,
            "last_commit": "",
            "last_commit_time": "",
            "dirty_files": [],
            "ahead": 0,
            "status": "no_worktree",
        }

    # 最后一次 commit
    last_commit = _git(["log", "-1", "--pretty=%s"], wt)
    last_commit_time = _git(["log", "-1", "--pretty=%cr"], wt)

    # 未提交的修改
    dirty = _git(["status", "--short"], wt)
    dirty_files = [l.strip() for l in dirty.splitlines() if l.strip()] if dirty else []

    # 相对 main 领先几个 commit
    ahead_str = _git(["rev-list", "--count", "main..HEAD"], wt)
    try:
        ahead = int(ahead_str)
    except ValueError:
        ahead = 0

    # 判断状态
    if dirty_files:
        status = "dirty"       # 有未提交变更（agent 正在工作中）
    elif ahead > 0:
        status = "ready"       # 有新 commit 待 merge
    else:
        status = "clean"       # 与 main 同步，等待新任务

    return {
        "id": agent["id"],
        "name": agent["name"],
        "branch": agent["branch"],
        "exists": True,
        "last_commit": last_commit,
        "last_commit_time": last_commit_time,
        "dirty_files": dirty_files,
        "ahead": ahead,
        "status": status,
    }


def _read_note(agent_id: str) -> str:
    f = NOTES_DIR / f"{agent_id}.md"
    return f.read_text(encoding="utf-8") if f.exists() else ""


def _write_note(agent_id: str, content: str) -> None:
    (NOTES_DIR / f"{agent_id}.md").write_text(content, encoding="utf-8")

# ── FastAPI ───────────────────────────────────────────────────────────────────

app = FastAPI(title="Agent 开发看板")


class NotePayload(BaseModel):
    content: str


@app.get("/api/status")
def get_status():
    return [_agent_status(a) for a in AGENTS]


@app.get("/api/notes/{agent_id}")
def get_note(agent_id: str):
    if agent_id not in {a["id"] for a in AGENTS}:
        raise HTTPException(404, "agent not found")
    return {"content": _read_note(agent_id)}


@app.post("/api/notes/{agent_id}")
def save_note(agent_id: str, payload: NotePayload):
    if agent_id not in {a["id"] for a in AGENTS}:
        raise HTTPException(404, "agent not found")
    _write_note(agent_id, payload.content)
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return HTML

# ── HTML 前端 ─────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Agent 开发看板</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #0f1117; color: #e2e8f0; min-height: 100vh;
    padding: 24px;
  }
  header {
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 24px;
  }
  h1 { font-size: 20px; font-weight: 600; color: #f8fafc; }
  .refresh-info { font-size: 12px; color: #64748b; }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 16px;
  }
  .card {
    background: #1e2130; border: 1px solid #2d3348;
    border-radius: 12px; padding: 20px;
    display: flex; flex-direction: column; gap: 14px;
  }
  .card-header { display: flex; align-items: center; gap: 10px; }
  .card-title { font-size: 16px; font-weight: 600; }
  .badge {
    font-size: 11px; font-weight: 500; padding: 2px 8px;
    border-radius: 20px; white-space: nowrap;
  }
  .badge-clean    { background: #14532d; color: #4ade80; }
  .badge-ready    { background: #1e3a5f; color: #60a5fa; }
  .badge-dirty    { background: #451a03; color: #fb923c; }
  .badge-no_worktree { background: #3f3f46; color: #a1a1aa; }

  .section-label {
    font-size: 11px; font-weight: 500; text-transform: uppercase;
    letter-spacing: .05em; color: #64748b; margin-bottom: 4px;
  }
  .commit-msg { font-size: 13px; color: #cbd5e1; line-height: 1.4; }
  .commit-time { font-size: 11px; color: #64748b; margin-top: 2px; }
  .branch-tag {
    font-size: 11px; background: #1e293b; color: #94a3b8;
    padding: 2px 7px; border-radius: 4px; font-family: monospace;
    display: inline-block;
  }
  .dirty-list { display: flex; flex-direction: column; gap: 3px; }
  .dirty-item {
    font-size: 11px; font-family: monospace; color: #fbbf24;
    background: #1c1a08; padding: 2px 6px; border-radius: 4px;
  }
  .ahead-pill {
    font-size: 11px; background: #1e3a5f; color: #60a5fa;
    padding: 2px 7px; border-radius: 10px; display: inline-block;
  }
  textarea {
    width: 100%; background: #0f1117; border: 1px solid #2d3348;
    border-radius: 8px; color: #e2e8f0; font-size: 13px;
    padding: 10px; resize: vertical; min-height: 80px;
    font-family: inherit; line-height: 1.5; outline: none;
    transition: border-color .15s;
  }
  textarea:focus { border-color: #3b82f6; }
  .btn-save {
    align-self: flex-end;
    background: #2563eb; color: #fff; border: none;
    border-radius: 6px; padding: 6px 16px; font-size: 13px;
    cursor: pointer; transition: background .15s;
  }
  .btn-save:hover { background: #1d4ed8; }
  .btn-save.saved { background: #15803d; }
  .no-wt { color: #64748b; font-size: 13px; }
  .divider { border: none; border-top: 1px solid #2d3348; }
  .last-updated { font-size: 11px; color: #475569; text-align: right; }
</style>
</head>
<body>
<header>
  <h1>🤖 Agent 开发看板</h1>
  <span class="refresh-info" id="refresh-info">30s 后自动刷新</span>
</header>
<div class="grid" id="grid">
  <div class="card" style="justify-content:center;align-items:center;color:#64748b">加载中...</div>
</div>
<p class="last-updated" id="last-updated"></p>

<script>
const AGENTS = ['x','xhs','tgb','finance'];
const LABELS = {clean:'同步',ready:'待 merge',dirty:'开发中',no_worktree:'未创建'};
const notes = {};

async function loadNotes() {
  for (const id of AGENTS) {
    const r = await fetch('/api/notes/' + id);
    const d = await r.json();
    notes[id] = d.content || '';
  }
}

async function saveNote(id) {
  const ta = document.getElementById('note-' + id);
  const btn = document.getElementById('btn-' + id);
  await fetch('/api/notes/' + id, {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({content: ta.value})
  });
  btn.textContent = '已保存 ✓';
  btn.classList.add('saved');
  setTimeout(() => { btn.textContent = '保存指令'; btn.classList.remove('saved'); }, 2000);
}

function renderCard(s) {
  const dirtyHtml = s.dirty_files.length
    ? s.dirty_files.map(f => `<div class="dirty-item">${f}</div>`).join('')
    : '<span style="color:#475569;font-size:12px">无未提交变更</span>';

  const aheadHtml = s.ahead > 0
    ? `<span class="ahead-pill">↑ 领先 main ${s.ahead} 个 commit</span>` : '';

  const note = notes[s.id] || '';

  if (!s.exists) {
    return `<div class="card">
      <div class="card-header">
        <span class="card-title">${s.name}</span>
        <span class="badge badge-no_worktree">未创建</span>
      </div>
      <p class="no-wt">Worktree 不存在，运行 <code>bash agents/scripts/new_round.sh</code> 创建</p>
      <hr class="divider">
      <div>
        <div class="section-label">下轮指令</div>
        <textarea id="note-${s.id}" placeholder="留下给下轮 agent 的指令...">${note}</textarea>
        <button class="btn-save" id="btn-${s.id}" onclick="saveNote('${s.id}')">保存指令</button>
      </div>
    </div>`;
  }

  return `<div class="card">
    <div class="card-header">
      <span class="card-title">${s.name}</span>
      <span class="badge badge-${s.status}">${LABELS[s.status] || s.status}</span>
    </div>
    <div>
      <div class="section-label">分支</div>
      <span class="branch-tag">${s.branch}</span>
      ${aheadHtml}
    </div>
    <div>
      <div class="section-label">最后提交</div>
      <div class="commit-msg">${s.last_commit || '—'}</div>
      <div class="commit-time">${s.last_commit_time || ''}</div>
    </div>
    <div>
      <div class="section-label">未提交变更</div>
      <div class="dirty-list">${dirtyHtml}</div>
    </div>
    <hr class="divider">
    <div>
      <div class="section-label">下轮指令（Claude 启动 agent 时自动读取）</div>
      <textarea id="note-${s.id}" placeholder="例如：优化 OCR 准确率，跳过重复内容...">${note}</textarea>
      <button class="btn-save" id="btn-${s.id}" onclick="saveNote('${s.id}')">保存指令</button>
    </div>
  </div>`;
}

async function refresh() {
  const [statusRes] = await Promise.all([fetch('/api/status'), loadNotes()]);
  const statuses = await statusRes.json();
  document.getElementById('grid').innerHTML = statuses.map(renderCard).join('');
  const now = new Date().toLocaleTimeString('zh-CN');
  document.getElementById('last-updated').textContent = '最后更新：' + now;
}

// 倒计时刷新
let countdown = 30;
setInterval(() => {
  countdown--;
  document.getElementById('refresh-info').textContent = countdown + 's 后自动刷新';
  if (countdown <= 0) {
    countdown = 30;
    refresh();
  }
}, 1000);

refresh();
</script>
</body>
</html>"""


if __name__ == "__main__":
    print("看板启动中... 访问 http://localhost:8765")
    uvicorn.run(app, host="0.0.0.0", port=8765, log_level="warning")
