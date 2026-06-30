"""Agent 开发看板：实时显示 worktree 状态，一键启动开发，实时看日志，支持干预。

启动：
    source .venv/bin/activate
    python agents/dashboard.py

访问：http://localhost:8765
"""
import os
import re
import subprocess
import threading
import time
import json
import urllib.request
import urllib.parse
from pathlib import Path
from typing import List, Optional

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, BackgroundTasks
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
import uvicorn

# ── 路径配置 ──────────────────────────────────────────────────────────────────

ROOT      = Path(__file__).parent.parent
NOTES_DIR = Path(__file__).parent / "notes"
LOGS_DIR  = Path(__file__).parent / "logs"
BOOKS_DIR = ROOT / "books"
NOTES_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)
BOOKS_DIR.mkdir(exist_ok=True)

CLAUDE_BIN = "/Users/pany19/.nvm/versions/node/v22.22.2/bin/claude"

AGENTS = [
    {"id": "x",        "name": "X (Twitter)",  "branch": "feature/x",
     "worktree": ".claude/worktrees/feature-x"},
    {"id": "xhs",      "name": "小红书",         "branch": "feature/xhs",
     "worktree": ".claude/worktrees/feature-xhs"},
    {"id": "tgb",      "name": "淘股吧",         "branch": "feature/tgb",
     "worktree": ".claude/worktrees/feature-tgb"},
    {"id": "finance",  "name": "金融行情",        "branch": "feature/finance",
     "worktree": ".claude/worktrees/feature-finance"},
    {"id": "industry", "name": "产业链分析",      "branch": "feature/industry",
     "worktree": ".claude/worktrees/feature-industry"},
    {"id": "research", "name": "研报跟进",        "branch": "feature/research",
     "worktree": ".claude/worktrees/feature-research"},
]

# 合法 agent id 集合（AGENTS 为常量，提前构建避免每次请求重复生成集合）
_AGENT_IDS = {a["id"] for a in AGENTS}

# ── 运行状态（内存，进程重启后重置）──────────────────────────────────────────

_run = {
    "status":     "idle",   # idle | running | done | error
    "pid":        None,
    "log":        [],       # list of str lines
    "proc":       None,     # subprocess.Popen
    "session_id": None,     # 最近一次成功完成的 claude 会话 ID
    "master_fd":  None,     # PTY master fd
}
_log_lock = threading.Lock()


def _append_log(line: str):
    with _log_lock:
        _run["log"].append(line)
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
    # 一次 git log 同时取出 subject 与相对时间（用 \x1f 分隔），少起一个子进程
    log_line = _git(["log", "-1", "--pretty=format:%s%x1f%cr"], wt)
    if "\x1f" in log_line:
        last_commit, last_commit_time = log_line.split("\x1f", 1)
    else:
        last_commit, last_commit_time = log_line, ""
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

_TOOL_ICONS = {
    "Bash": "💻", "Read": "📖", "Edit": "✏️", "Write": "📝",
    "Glob": "🔍", "Grep": "🔎", "WebFetch": "🌐", "WebSearch": "🌐",
    "Task": "🤖", "TodoWrite": "📋",
}


def _format_event(ev: dict) -> Optional[str]:
    """将 stream-json 事件转换为可读日志行，None 表示跳过。"""
    t = ev.get("type", "")

    if t == "system" and ev.get("subtype") == "init":
        sid = ev.get("session_id", "")
        _run["session_id"] = sid
        model = ev.get("model", "")
        return f"[会话 {sid[:8]}] 已连接 ({model})"

    if t == "system" and ev.get("subtype") == "status":
        status = ev.get("status", "")
        if status == "requesting":
            return "正在请求 Claude API..."
        return None

    if t == "stream_event":
        event = ev.get("event", {})
        etype = event.get("type", "")
        if etype == "content_block_start":
            cb = event.get("content_block", {})
            if cb.get("type") == "tool_use":
                name = cb.get("name", "?")
                icon = _TOOL_ICONS.get(name, "🔧")
                return f"{icon} {name}..."
        return None

    if t == "assistant":
        msg = ev.get("message", {})
        for block in msg.get("content", []):
            if block.get("type") == "text":
                text = block.get("text", "").strip()
                if text:
                    lines = text.split("\n")
                    return "\n".join(f"  {l}" for l in lines if l.strip())
            if block.get("type") == "tool_use":
                name = block.get("name", "?")
                inp = block.get("input", {})
                icon = _TOOL_ICONS.get(name, "🔧")
                detail = ""
                if name == "Bash":
                    detail = inp.get("command", "")[:80]
                elif name in ("Read", "Edit", "Write", "Glob", "Grep"):
                    detail = inp.get("file_path") or inp.get("pattern") or ""
                    detail = str(detail)[:60]
                return f"{icon} {name}: {detail}" if detail else f"{icon} {name}"
        return None

    if t == "tool_result":
        content = ev.get("content", "")
        if isinstance(content, list):
            content = " ".join(b.get("text", "") for b in content if b.get("type") == "text")
        if isinstance(content, str) and content.strip():
            lines = content.strip().split("\n")
            preview = lines[0][:100] + ("…" if len(lines) > 1 or len(lines[0]) > 100 else "")
            return f"  └ {preview}"
        return None

    if t == "result":
        if ev.get("is_error"):
            return "❌ 执行出错"
        cost = ev.get("total_cost_usd", 0)
        ms = ev.get("duration_ms", 0)
        result_text = ev.get("result", "")
        lines = [f"✅ 完成 ({ms/1000:.1f}s, ${cost:.3f})"]
        if result_text:
            for l in result_text.strip().split("\n")[:5]:
                lines.append(f"  {l}")
        return "\n".join(lines)

    return None


def _stream_json(proc):
    """后台线程：读取 claude --output-format stream-json 输出并写入日志。"""
    text_buf = ""
    for raw_line in proc.stdout:
        line = raw_line.rstrip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            _append_log(line)
            continue

        # 累积文本块（streaming deltas）
        if ev.get("type") == "stream_event":
            event = ev.get("event", {})
            if event.get("type") == "content_block_delta":
                delta = event.get("delta", {})
                if delta.get("type") == "text_delta":
                    text_buf += delta.get("text", "")
                    # 每遇到换行就输出
                    while "\n" in text_buf:
                        chunk, text_buf = text_buf.split("\n", 1)
                        if chunk.strip():
                            _append_log(f"  {chunk}")
                    continue
            elif event.get("type") == "content_block_stop" and text_buf.strip():
                _append_log(f"  {text_buf.strip()}")
                text_buf = ""

        msg = _format_event(ev)
        if msg:
            _append_log(msg)

    # 剩余文本缓冲
    if text_buf.strip():
        _append_log(f"  {text_buf.strip()}")

    proc.wait()
    code = proc.returncode
    if code == 0:
        _run["status"] = "done"
    elif code in (-15, 143):
        _run["status"] = "idle"
    else:
        _run["status"] = "error"
    _run["proc"] = None
    _run["pid"]  = None
    _append_log(f"\n── 进程结束 (exit {code}) ──")


def _run_claude(cmd, header):
    """启动 claude（stream-json 模式），返回 (ok, pid_or_errmsg)。"""
    full_cmd = cmd + ["--output-format", "stream-json", "--verbose"]
    try:
        proc = subprocess.Popen(
            full_cmd, cwd=str(ROOT),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
    except Exception as e:
        return False, str(e)

    _run["proc"] = proc
    _run["pid"]  = proc.pid
    _append_log(header)
    threading.Thread(target=_stream_json, args=(proc,), daemon=True).start()
    return True, proc.pid


def _launch_claude(prompt: str):
    if _run["status"] == "running":
        return False, "已有任务在运行"
    with _log_lock:
        _run["log"] = []
    _run["status"] = "running"

    cmd = [CLAUDE_BIN, "--dangerously-skip-permissions", "-p", prompt]
    header = f"── 启动 Claude Code ──\n{prompt}\n── 开始执行 ──\n"
    return _run_claude(cmd, header)


def _intervene(message: str):
    """追加干预消息：有记录的 session_id 则用 -r 恢复，否则用 --continue。"""
    if _run["status"] == "running":
        return False, "有任务正在运行，请等待完成后再干预"

    _run["status"] = "running"
    sid = _run.get("session_id")
    if sid:
        cmd = [CLAUDE_BIN, "--dangerously-skip-permissions", "-r", sid, "-p", message]
        header = f"\n── 干预（续接会话 {sid[:8]}…）──\n{message}\n── 继续执行 ──\n"
    else:
        cmd = [CLAUDE_BIN, "--dangerously-skip-permissions", "-p", message]
        header = f"\n── 新会话指令 ──\n{message}\n── 开始执行 ──\n"
    return _run_claude(cmd, header)


# ── RAG 入库状态 ──────────────────────────────────────────────────────────────

import sys as _sys
_sys.path.insert(0, str(ROOT))

_rag_state = {
    "status":  "idle",     # idle | running | done | error
    "log":     [],         # 日志行列表
    "stats":   {},         # 最新统计
    "total":   0,          # 本次任务总数
    "current": 0,          # 已处理数
}
_rag_lock = threading.Lock()


def _rag_log(msg: str):
    with _rag_lock:
        _rag_state["log"].append(msg)
        if len(_rag_state["log"]) > 500:
            _rag_state["log"] = _rag_state["log"][-500:]


def _rag_progress(current: int, total: int):
    with _rag_lock:
        _rag_state["current"] = current
        _rag_state["total"] = total
        _rag_state["log"].append(f"__progress__:{current}/{total}")


def _rag_stats() -> dict:
    try:
        from x_agent.rag import collection_stats
        return collection_stats()
    except Exception as e:
        return {"error": str(e)}


def _do_ingest_file(path: Path):
    """后台线程：入库单个 PDF。"""
    _rag_state["status"] = "running"
    _rag_log(f"开始入库：{path.name}")
    try:
        from x_agent.rag import ingest_pdf
        n = ingest_pdf(str(path), source_type="upload")
        if n > 0:
            _rag_log(f"✅ {path.name}  新增 {n} 块")
        else:
            _rag_log(f"⏭ {path.name}  内容已入库，跳过")
        _rag_state["stats"] = _rag_stats()
        _rag_state["status"] = "done"
    except Exception as e:
        _rag_log(f"❌ {path.name} 失败：{e}")
        _rag_state["status"] = "error"


def _do_ingest_dir(directory: Path, recursive: bool = False):
    """后台线程：批量入库目录下所有 PDF。"""
    _rag_state["status"] = "running"
    pattern = "**/*.pdf" if recursive else "*.pdf"
    pdfs = sorted(directory.glob(pattern))
    _rag_log(f"扫描到 {len(pdfs)} 个 PDF，开始批量入库…")
    if not pdfs:
        _rag_log("目录中没有 PDF 文件。")
        _rag_state["status"] = "done"
        return
    ok = skipped = 0
    failed = []
    total = len(pdfs)
    _rag_progress(0, total)
    from x_agent.rag import ingest_pdf   # 循环外导入一次
    for idx, pdf in enumerate(pdfs, 1):
        try:
            n = ingest_pdf(str(pdf), source_type="upload")
            if n > 0:
                ok += 1
                _rag_log(f"  ✅ [{idx}/{total}] {pdf.name}  新增 {n} 块")
            else:
                skipped += 1
                _rag_log(f"  ⏭ [{idx}/{total}] {pdf.name}  已入库，跳过")
        except Exception as e:
            failed.append(pdf.name)
            _rag_log(f"  ❌ [{idx}/{total}] {pdf.name}  {e}")
        _rag_progress(idx, total)
    _rag_log(f"完成：入库 {ok} 本 / 跳过 {skipped} 本 / 失败 {len(failed)} 本")
    _rag_state["stats"] = _rag_stats()
    _rag_state["status"] = "done" if not failed else "error"


# ── 百度网盘 OAuth + 文件同步 ────────────────────────────────────────────────

BAIDU_APP_KEY    = os.environ.get("BAIDU_APP_KEY", "")
BAIDU_APP_SECRET = os.environ.get("BAIDU_APP_SECRET", "")
BAIDU_TOKEN_FILE = ROOT / "output" / "baidu_token.json"
BAIDU_REDIRECT   = "http://localhost:8765/api/baidu/callback"

# 百度网盘同步状态（与 RAG 共用 _rag_log / _rag_state）
_bdu_sync = {
    "status": "idle",   # idle | syncing | done | error
    "log":    [],
}
_bdu_lock = threading.Lock()

# 自动同步配置与状态
BAIDU_SYNCED_FILE = ROOT / "output" / "baidu_synced.json"

_bdu_auto_cfg = {
    "enabled":          False,
    "watch_dirs":       ["/"],
    "interval_minutes": 60,
}
_bdu_auto_state = {
    "last_run":    "",   # ISO 时间
    "last_new":    0,    # 上次新增文件数
    "next_run":    "",
    "running":     False,
}
_bdu_stop_event = threading.Event()


def _bdu_synced_ids() -> set:
    """读取已同步的 fs_id 集合。"""
    try:
        if BAIDU_SYNCED_FILE.exists():
            return set(json.loads(BAIDU_SYNCED_FILE.read_text()))
    except Exception:
        pass
    return set()


def _bdu_mark_synced(fs_id: int):
    """将 fs_id 写入已同步集合。"""
    ids = _bdu_synced_ids()
    ids.add(str(fs_id))
    BAIDU_SYNCED_FILE.parent.mkdir(parents=True, exist_ok=True)
    BAIDU_SYNCED_FILE.write_text(json.dumps(sorted(ids), ensure_ascii=False))


def _bdu_log(msg: str):
    with _bdu_lock:
        _bdu_sync["log"].append(msg)
        if len(_bdu_sync["log"]) > 200:
            _bdu_sync["log"] = _bdu_sync["log"][-200:]


def _bdu_load_token() -> dict:
    try:
        if BAIDU_TOKEN_FILE.exists():
            return json.loads(BAIDU_TOKEN_FILE.read_text())
    except Exception:
        pass
    return {}


def _bdu_save_token(tok: dict):
    BAIDU_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    tok["saved_at"] = int(time.time())
    BAIDU_TOKEN_FILE.write_text(json.dumps(tok, ensure_ascii=False, indent=2))


def _bdu_access_token() -> str:
    """返回有效 access_token，过期则用 refresh_token 续期。"""
    tok = _bdu_load_token()
    if not tok.get("access_token"):
        return ""
    # 若剩余有效期 < 3600s 则刷新
    saved_at  = tok.get("saved_at", 0)
    expires   = tok.get("expires_in", 2592000)
    if time.time() - saved_at > expires - 3600:
        tok = _bdu_refresh_token(tok)
    return tok.get("access_token", "")


def _bdu_refresh_token(tok: dict) -> dict:
    rt = tok.get("refresh_token", "")
    if not rt or not BAIDU_APP_KEY:
        return tok
    try:
        params = urllib.parse.urlencode({
            "grant_type":    "refresh_token",
            "refresh_token": rt,
            "client_id":     BAIDU_APP_KEY,
            "client_secret": BAIDU_APP_SECRET,
        }).encode()
        req = urllib.request.Request(
            "https://openapi.baidu.com/oauth/2.0/token",
            data=params, method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            new_tok = json.loads(resp.read())
        if "access_token" in new_tok:
            _bdu_save_token(new_tok)
            return new_tok
    except Exception as e:
        print(f"[baidu] refresh token 失败: {e}")
    return tok


def _bdu_api(path: str, params: dict) -> dict:
    """调用百度网盘 REST API。"""
    at = _bdu_access_token()
    if not at:
        raise RuntimeError("未授权，请先连接百度网盘")
    params["access_token"] = at
    qs = urllib.parse.urlencode(params)
    url = f"https://pan.baidu.com/rest/2.0/xpan/{path}?{qs}"
    req = urllib.request.Request(url, headers={"User-Agent": "pan.baidu.com"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def _bdu_list_dir(dir_path: str = "/") -> list:
    """列出目录中的 PDF 文件和子目录。"""
    data = _bdu_api("file", {
        "method": "list",
        "dir":    dir_path,
        "limit":  200,
        "order":  "name",
        "web":    "1",
    })
    items = []
    for f in data.get("list", []):
        name = f.get("server_filename", "")
        is_dir = f.get("isdir", 0) == 1
        if is_dir or name.lower().endswith(".pdf"):
            items.append({
                "fs_id":    f["fs_id"],
                "name":     name,
                "path":     f["path"],
                "size":     f.get("size", 0),
                "is_dir":   is_dir,
            })
    return items


def _bdu_download_pdf(fs_id: int, filename: str) -> Path:
    """下载单个 PDF 到 books/ 目录。"""
    at = _bdu_access_token()
    # 获取真实下载链接
    qs = urllib.parse.urlencode({
        "method":       "filemetas",
        "access_token": at,
        "fsids":        json.dumps([fs_id]),
        "dlink":        1,
    })
    url = f"https://pan.baidu.com/rest/2.0/xpan/multimedia?{qs}"
    req = urllib.request.Request(url, headers={"User-Agent": "pan.baidu.com"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        metas = json.loads(resp.read()).get("list", [])
    if not metas:
        raise RuntimeError(f"无法获取 {filename} 的下载链接")

    dlink = metas[0]["dlink"] + f"&access_token={at}"
    dest  = BOOKS_DIR / filename
    req2  = urllib.request.Request(
        dlink,
        headers={"User-Agent": "pan.baidu.com"},
    )
    with urllib.request.urlopen(req2, timeout=120) as resp2:
        dest.write_bytes(resp2.read())
    return dest


def _do_baidu_sync(files: list):
    """后台线程：下载并入库勾选的百度网盘 PDF。"""
    _bdu_sync["status"] = "syncing"
    ok = failed = 0
    from x_agent.rag import ingest_pdf   # 循环外导入一次
    for f in files:
        name  = f["name"]
        fs_id = f["fs_id"]
        size_kb = f.get("size", 0) // 1024
        _bdu_log(f"⬇ 下载 {name}（{size_kb} KB）...")
        try:
            dest = _bdu_download_pdf(fs_id, name)
            _bdu_log(f"  → 已保存到 books/{name}")
            n = ingest_pdf(str(dest), source_type="netdisk")
            _bdu_log(f"  ✅ 入库 {n} 块")
            _bdu_mark_synced(fs_id)
            ok += 1
        except Exception as e:
            _bdu_log(f"  ❌ 失败：{e}")
            failed += 1
    _bdu_log(f"同步完成：✅ {ok} 本 / ❌ {failed} 本")
    _rag_state["stats"] = _rag_stats()
    _bdu_sync["status"] = "done" if not failed else "error"


def _bdu_scan_pdfs(watch_dirs: list) -> list:
    """递归扫描 watch_dirs，返回所有 PDF 文件列表（去重）。"""
    seen = set()
    results = []
    queue = list(watch_dirs)
    while queue:
        d = queue.pop(0)
        try:
            items = _bdu_list_dir(d)
        except Exception as e:
            _bdu_log(f"[自动同步] 扫描 {d} 失败：{e}")
            continue
        for item in items:
            if item["is_dir"]:
                queue.append(item["path"])
            elif str(item["fs_id"]) not in seen:
                seen.add(str(item["fs_id"]))
                results.append(item)
    return results


def _do_auto_sync():
    """执行一次增量同步：扫描目录，只下载未同步的新文件。"""
    import datetime as _dt
    _bdu_auto_state["running"] = True
    _bdu_auto_state["last_run"] = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        if not _bdu_access_token():
            _bdu_log("[自动同步] 未授权，跳过本轮")
            return
        watch_dirs = _bdu_auto_cfg["watch_dirs"]
        _bdu_log(f"[自动同步] 开始扫描 {watch_dirs}...")
        all_pdfs  = _bdu_scan_pdfs(watch_dirs)
        synced    = _bdu_synced_ids()
        new_files = [f for f in all_pdfs if str(f["fs_id"]) not in synced]
        _bdu_log(f"[自动同步] 共 {len(all_pdfs)} 个 PDF，{len(new_files)} 个新文件")
        if new_files:
            _do_baidu_sync(new_files)
        _bdu_auto_state["last_new"] = len(new_files)
    except Exception as e:
        _bdu_log(f"[自动同步] 异常：{e}")
    finally:
        _bdu_auto_state["running"] = False


def _bdu_auto_sync_loop():
    """后台守护线程：按间隔定期执行增量同步。"""
    import datetime as _dt
    while not _bdu_stop_event.is_set():
        if _bdu_auto_cfg["enabled"]:
            _do_auto_sync()
        interval = _bdu_auto_cfg["interval_minutes"] * 60
        next_ts = _dt.datetime.now() + _dt.timedelta(seconds=interval)
        _bdu_auto_state["next_run"] = next_ts.strftime("%Y-%m-%d %H:%M:%S")
        _bdu_stop_event.wait(timeout=interval)


# ── FastAPI ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    t = threading.Thread(target=_bdu_auto_sync_loop, daemon=True, name="bdu-auto-sync")
    t.start()
    yield
    _bdu_stop_event.set()

app = FastAPI(title="Agent 开发看板", lifespan=lifespan)


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
    if agent_id not in _AGENT_IDS:
        raise HTTPException(404)
    return {"content": _read_note(agent_id)}


@app.post("/api/notes/{agent_id}")
def save_note(agent_id: str, payload: NotePayload):
    if agent_id not in _AGENT_IDS:
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


@app.get("/api/rag/stats")
def rag_stats():
    stats = _rag_stats()
    books_dir_files = [p.name for p in BOOKS_DIR.glob("*.pdf")]
    return {
        "stats":      stats,
        "rag_status": _rag_state["status"],
        "rag_current": _rag_state["current"],
        "rag_total":   _rag_state["total"],
        "books_dir":  str(BOOKS_DIR),
        "books_pdfs": books_dir_files,
        "log":        _rag_state["log"][-50:],
    }


@app.post("/api/rag/upload")
async def rag_upload(background_tasks: BackgroundTasks,
                     file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "只支持 PDF 文件")
    if _rag_state["status"] == "running":
        raise HTTPException(409, "入库任务正在进行中，请稍候")

    dest = BOOKS_DIR / file.filename
    content = await file.read()
    dest.write_bytes(content)
    with _rag_lock:
        _rag_state["log"] = []
    _rag_log(f"已保存 {file.filename}（{len(content)//1024} KB）")
    background_tasks.add_task(_do_ingest_file, dest)
    return {"ok": True, "filename": file.filename, "size": len(content)}


@app.post("/api/rag/ingest-dir")
def rag_ingest_dir(background_tasks: BackgroundTasks,
                   recursive: bool = False):
    if _rag_state["status"] == "running":
        raise HTTPException(409, "入库任务正在进行中，请稍候")
    with _rag_lock:
        _rag_state["log"] = []
    background_tasks.add_task(_do_ingest_dir, BOOKS_DIR, recursive)
    return {"ok": True, "directory": str(BOOKS_DIR)}


@app.get("/api/rag/log/stream")
def rag_log_stream():
    """SSE：实时推送 RAG 入库日志。"""
    def gen():
        sent = 0
        while True:
            with _rag_lock:
                lines = _rag_state["log"]
                new   = lines[sent:]
                sent  = len(lines)
            for line in new:
                yield f"data: {json.dumps(line, ensure_ascii=False)}\n\n"
            # 入库结束后推送状态变化
            yield f"data: {json.dumps('__status__:' + _rag_state['status'], ensure_ascii=False)}\n\n"
            time.sleep(0.5)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/baidu/status")
def baidu_status():
    tok = _bdu_load_token()
    connected = bool(tok.get("access_token"))
    return {
        "connected":   connected,
        "netdisk_name": tok.get("netdisk_name", ""),
        "avatar_url":  tok.get("avatar_url", ""),
        "app_key_set": bool(BAIDU_APP_KEY),
        "sync_status": _bdu_sync["status"],
        "sync_log":    _bdu_sync["log"][-30:],
    }


@app.get("/api/baidu/auth-url")
def baidu_auth_url():
    if not BAIDU_APP_KEY:
        raise HTTPException(400, "请先在环境变量中设置 BAIDU_APP_KEY 和 BAIDU_APP_SECRET")
    url = (
        "https://openapi.baidu.com/oauth/2.0/authorize"
        f"?response_type=code&client_id={BAIDU_APP_KEY}"
        f"&redirect_uri={urllib.parse.quote(BAIDU_REDIRECT)}"
        "&scope=basic,netdisk&display=popup"
    )
    return {"url": url}


@app.get("/api/baidu/callback")
async def baidu_callback(request: Request):
    """OAuth 回调：用 code 换 token，保存后重定向回看板。"""
    code = request.query_params.get("code", "")
    if not code:
        return HTMLResponse("<h3>授权失败：未收到 code</h3>", status_code=400)
    params = urllib.parse.urlencode({
        "grant_type":   "authorization_code",
        "code":          code,
        "client_id":     BAIDU_APP_KEY,
        "client_secret": BAIDU_APP_SECRET,
        "redirect_uri":  BAIDU_REDIRECT,
    }).encode()
    try:
        req = urllib.request.Request(
            "https://openapi.baidu.com/oauth/2.0/token",
            data=params, method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            tok = json.loads(resp.read())
        if "access_token" not in tok:
            return HTMLResponse(f"<h3>授权失败：{tok}</h3>", status_code=400)

        # 获取用户网盘信息
        try:
            qs = urllib.parse.urlencode({"access_token": tok["access_token"]})
            info_req = urllib.request.Request(
                f"https://pan.baidu.com/rest/2.0/xpan/nas?method=uinfo&{qs}",
                headers={"User-Agent": "pan.baidu.com"},
            )
            with urllib.request.urlopen(info_req, timeout=10) as r2:
                uinfo = json.loads(r2.read())
            tok["netdisk_name"] = uinfo.get("netdisk_name", "")
            tok["avatar_url"]   = uinfo.get("avatar_url", "")
        except Exception:
            pass

        _bdu_save_token(tok)
        return HTMLResponse("""
            <html><body style="font-family:sans-serif;text-align:center;padding:60px">
            <h2>✅ 百度网盘授权成功！</h2>
            <p>正在返回看板...</p>
            <script>setTimeout(()=>window.close(),1500)</script>
            </body></html>
        """)
    except Exception as e:
        return HTMLResponse(f"<h3>授权失败：{e}</h3>", status_code=500)


@app.get("/api/baidu/files")
def baidu_files(dir: str = "/"):
    try:
        files = _bdu_list_dir(dir)
        return {"files": files, "dir": dir}
    except Exception as e:
        raise HTTPException(400, str(e))


class BaiduSyncPayload(BaseModel):
    files: list   # [{fs_id, name, size}, ...]


@app.post("/api/baidu/sync")
def baidu_sync(payload: BaiduSyncPayload, background_tasks: BackgroundTasks):
    if _bdu_sync["status"] == "syncing":
        raise HTTPException(409, "同步任务正在进行中")
    if not payload.files:
        raise HTTPException(400, "未选择文件")
    with _bdu_lock:
        _bdu_sync["log"] = []
    background_tasks.add_task(_do_baidu_sync, payload.files)
    return {"ok": True, "count": len(payload.files)}


@app.get("/api/baidu/sync/log/stream")
def baidu_sync_log_stream():
    def gen():
        sent = 0
        while True:
            with _bdu_lock:
                lines = _bdu_sync["log"]
                new   = lines[sent:]
                sent  = len(lines)
            for line in new:
                yield f"data: {json.dumps(line, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps('__status__:' + _bdu_sync['status'])}\n\n"
            time.sleep(0.5)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.delete("/api/baidu/disconnect")
def baidu_disconnect():
    if BAIDU_TOKEN_FILE.exists():
        BAIDU_TOKEN_FILE.unlink()
    return {"ok": True}


@app.get("/api/baidu/auto-sync")
def baidu_auto_sync_status():
    return {
        "config":      _bdu_auto_cfg,
        "state":       _bdu_auto_state,
        "synced_count": len(_bdu_synced_ids()),
    }


class BaiduAutoSyncConfig(BaseModel):
    enabled:          Optional[bool] = None
    watch_dirs:       Optional[list] = None
    interval_minutes: Optional[int]  = None


@app.post("/api/baidu/auto-sync/config")
def baidu_auto_sync_config(payload: BaiduAutoSyncConfig):
    if payload.enabled is not None:
        _bdu_auto_cfg["enabled"] = payload.enabled
    if payload.watch_dirs is not None:
        _bdu_auto_cfg["watch_dirs"] = payload.watch_dirs
    if payload.interval_minutes is not None:
        if payload.interval_minutes < 5:
            raise HTTPException(400, "最小间隔 5 分钟")
        _bdu_auto_cfg["interval_minutes"] = payload.interval_minutes
    # 立即唤醒循环线程重新计算下次运行时间
    _bdu_stop_event.set()
    _bdu_stop_event.clear()
    return {"ok": True, "config": _bdu_auto_cfg}


@app.post("/api/baidu/auto-sync/run-now")
def baidu_auto_sync_run_now(background_tasks: BackgroundTasks):
    """立即触发一次增量同步（不等待定时器）。"""
    if _bdu_auto_state["running"]:
        raise HTTPException(409, "自动同步正在运行中")
    background_tasks.add_task(_do_auto_sync)
    return {"ok": True}


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

/* RAG 面板 */
.rag-panel {
  background:#1e2130; border:1px solid #2d3348; border-radius:12px;
  margin-bottom:24px; overflow:hidden;
}
.rag-header {
  display:flex; align-items:center; justify-content:space-between;
  padding:14px 20px; border-bottom:1px solid #2d3348;
  background:#111827; gap:12px; flex-wrap:wrap;
}
.rag-title { font-size:15px; font-weight:600; color:#f8fafc; }
.rag-actions { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
.rag-body { padding:16px 20px; display:flex; flex-direction:column; gap:14px; }
.rag-stats { display:flex; gap:16px; flex-wrap:wrap; }
.rag-stat {
  background:#0f1117; border:1px solid #2d3348; border-radius:8px;
  padding:10px 16px; min-width:100px;
}
.rag-stat-val { font-size:22px; font-weight:700; color:#60a5fa; }
.rag-stat-lbl { font-size:11px; color:#64748b; margin-top:2px; }
.rag-type-row { display:flex; gap:8px; flex-wrap:wrap; }
.rag-type-tag {
  font-size:11px; padding:3px 10px; border-radius:12px;
  background:#1e293b; color:#94a3b8;
}

/* 拖拽上传区域 */
.drop-zone {
  border:2px dashed #2d3348; border-radius:10px;
  padding:28px 20px; text-align:center; cursor:pointer;
  transition:border-color .2s, background .2s; position:relative;
}
.drop-zone:hover, .drop-zone.drag-over {
  border-color:#3b82f6; background:#0f1825;
}
.drop-zone input[type=file] {
  position:absolute; inset:0; opacity:0; cursor:pointer; width:100%; height:100%;
}
.drop-label { font-size:13px; color:#64748b; pointer-events:none; }
.drop-label strong { color:#94a3b8; }

/* RAG 日志 */
.rag-log {
  font-family:"SF Mono","Fira Code",monospace; font-size:11.5px; line-height:1.6;
  background:#0a0c10; border:1px solid #2d3348; border-radius:8px;
  padding:10px 14px; max-height:180px; overflow-y:auto;
  color:#a3e635; white-space:pre-wrap; word-break:break-all;
}
.rag-log .ok   { color:#4ade80; }
.rag-log .skip { color:#64748b; }
.rag-log .fail { color:#f87171; }

.btn-sm {
  background:#334155; color:#cbd5e1; border:none; border-radius:6px;
  padding:6px 14px; font-size:12px; cursor:pointer;
  transition:background .15s; white-space:nowrap;
}
.btn-sm:hover { background:#475569; }
.btn-sm:disabled { opacity:.4; cursor:default; }
.btn-sm.primary { background:#2563eb; color:#fff; }
.btn-sm.primary:hover { background:#1d4ed8; }
.bdu-file-item {
  display:flex; align-items:center; gap:8px;
  padding:7px 14px; border-bottom:1px solid #1a1f2e; cursor:pointer;
}
.bdu-file-item:hover { background:#111827; }
.bdu-file-item:last-child { border-bottom:none; }
.bdu-file-item input[type=checkbox] { cursor:pointer; accent-color:#3b82f6; }
.bdu-file-name { flex:1; color:#cbd5e1; }
.bdu-file-size { font-size:10px; color:#475569; }
.rag-status-badge {
  font-size:11px; font-weight:600; padding:3px 10px; border-radius:12px;
}
.rag-idle    { background:#1e293b; color:#64748b; }
.rag-running { background:#451a03; color:#fb923c; }
.rag-done    { background:#14532d; color:#4ade80; }
.rag-error   { background:#450a0a; color:#f87171; }

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

<!-- ── 知识库管理面板 ── -->
<div class="rag-panel">
  <div class="rag-header">
    <span class="rag-title">📚 RAG 知识库管理</span>
    <div class="rag-actions">
      <span class="rag-status-badge rag-idle" id="rag-badge">空闲</span>
      <button class="btn-sm primary" onclick="ragIngestDir(false)" id="btn-ingest-dir">
        🔄 扫描 books/ 入库
      </button>
      <button class="btn-sm" onclick="refreshRagStats()">刷新统计</button>
    </div>
  </div>
  <div class="rag-body">
    <!-- 统计 -->
    <div class="rag-stats" id="rag-stats">
      <div class="rag-stat"><div class="rag-stat-val" id="stat-chunks">—</div>
        <div class="rag-stat-lbl">知识块</div></div>
      <div class="rag-stat"><div class="rag-stat-val" id="stat-books">—</div>
        <div class="rag-stat-lbl">书籍</div></div>
      <div class="rag-stat"><div class="rag-stat-val" id="stat-pdfs">—</div>
        <div class="rag-stat-lbl">books/ 中 PDF</div></div>
    </div>
    <div class="rag-type-row" id="rag-types"></div>

    <!-- 拖拽上传 -->
    <div class="drop-zone" id="drop-zone"
         ondragover="event.preventDefault();this.classList.add('drag-over')"
         ondragleave="this.classList.remove('drag-over')"
         ondrop="handleDrop(event)">
      <input type="file" accept=".pdf" multiple id="file-input"
             onchange="handleFileSelect(event)">
      <div class="drop-label">
        📄 拖拽 PDF 到此处，或 <strong>点击选择文件</strong><br>
        <span style="font-size:11px;color:#475569">支持多选，文件将保存到 books/ 并自动入库</span>
      </div>
    </div>

    <!-- 百度网盘同步 -->
    <div style="border-top:1px solid #2d3348;padding-top:14px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;flex-wrap:wrap;gap:8px">
        <span style="font-size:13px;font-weight:600;color:#94a3b8">☁️ 百度网盘同步</span>
        <div style="display:flex;gap:8px;align-items:center" id="bdu-controls">
          <span id="bdu-badge" style="font-size:11px;padding:3px 10px;border-radius:12px;background:#1e293b;color:#64748b">未连接</span>
          <button class="btn-sm" id="btn-bdu-auth" onclick="bduAuth()">🔑 授权连接</button>
          <button class="btn-sm" id="btn-bdu-disconnect" onclick="bduDisconnect()" style="display:none;color:#f87171">断开</button>
        </div>
      </div>

      <!-- 已连接：文件浏览 -->
      <div id="bdu-browser" style="display:none">
        <div style="display:flex;gap:8px;margin-bottom:10px;align-items:center">
          <input id="bdu-dir" value="/" style="flex:1;background:#0f1117;border:1px solid #2d3348;border-radius:6px;color:#e2e8f0;padding:6px 10px;font-size:12px;outline:none"
                 placeholder="/我的文档/books" onkeydown="if(event.key==='Enter')bduListDir()">
          <button class="btn-sm" onclick="bduListDir()">📂 浏览</button>
          <button class="btn-sm primary" id="btn-bdu-sync" onclick="bduSync()">⬇ 同步选中</button>
        </div>
        <div id="bdu-file-list" style="background:#0a0c10;border:1px solid #2d3348;border-radius:8px;max-height:200px;overflow-y:auto;font-size:12px">
          <div style="color:#475569;padding:12px 14px">点击「浏览」查看目录中的 PDF 文件</div>
        </div>
      </div>

      <!-- 同步日志 -->
      <div class="rag-log" id="bdu-log" style="margin-top:10px"><span style="color:#475569">等待同步...</span></div>

      <!-- 自动同步配置 -->
      <div id="bdu-auto-section" style="display:none;margin-top:14px;padding:10px 12px;background:#0a0c10;border:1px solid #2d3348;border-radius:8px">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
          <span style="font-size:12px;font-weight:600;color:#94a3b8">🔄 定时增量同步</span>
          <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:#94a3b8;cursor:pointer">
            <input type="checkbox" id="bdu-auto-enabled" onchange="bduAutoToggle(this.checked)">
            启用
          </label>
        </div>
        <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px;flex-wrap:wrap">
          <input id="bdu-watch-dir" value="/" placeholder="/我的文档"
                 style="flex:1;min-width:120px;background:#1e293b;border:1px solid #2d3348;border-radius:6px;color:#e2e8f0;padding:5px 8px;font-size:12px;outline:none">
          <select id="bdu-interval" style="background:#1e293b;border:1px solid #2d3348;border-radius:6px;color:#e2e8f0;padding:5px 8px;font-size:12px;outline:none">
            <option value="15">15 分钟</option>
            <option value="30">30 分钟</option>
            <option value="60" selected>1 小时</option>
            <option value="360">6 小时</option>
            <option value="1440">每天</option>
          </select>
          <button class="btn-sm" onclick="bduAutoSaveConfig()">保存</button>
          <button class="btn-sm primary" onclick="bduAutoRunNow()">立即同步</button>
        </div>
        <div id="bdu-auto-state" style="font-size:11px;color:#475569">加载中...</div>
      </div>
    </div>

    <!-- 进度条（批量入库时显示） -->
    <div id="rag-progress-wrap" style="display:none;margin-bottom:8px">
      <div style="display:flex;justify-content:space-between;font-size:11px;color:#64748b;margin-bottom:4px">
        <span>入库进度</span>
        <span id="rag-progress-label">0 / 0</span>
      </div>
      <div style="background:#1e293b;border-radius:6px;height:8px;overflow:hidden">
        <div id="rag-progress-bar"
             style="height:100%;width:0%;background:linear-gradient(90deg,#6366f1,#4ade80);border-radius:6px;transition:width 0.3s ease"></div>
      </div>
    </div>

    <!-- 本地入库日志 -->
    <div class="rag-log" id="rag-log"><span style="color:#475569">等待操作...</span></div>
  </div>
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

// ── RAG 知识库管理 ────────────────────────────────────────────────────────────

const RAG_STATUS_LABEL = {idle:'空闲', running:'入库中...', done:'完成', error:'出错'};
const RAG_STATUS_CLASS = {idle:'rag-idle', running:'rag-running', done:'rag-done', error:'rag-error'};
let ragLogES = null;

function updateRagBadge(status) {
  const el = document.getElementById('rag-badge');
  el.className = 'rag-status-badge ' + (RAG_STATUS_CLASS[status] || 'rag-idle');
  el.textContent = RAG_STATUS_LABEL[status] || status;
  const busy = status === 'running';
  document.getElementById('btn-ingest-dir').disabled = busy;
  document.getElementById('file-input').disabled = busy;
  document.getElementById('drop-zone').style.opacity = busy ? '0.5' : '1';
  document.getElementById('drop-zone').style.pointerEvents = busy ? 'none' : '';
}

function updateRagProgress(current, total) {
  const wrap  = document.getElementById('rag-progress-wrap');
  const bar   = document.getElementById('rag-progress-bar');
  const label = document.getElementById('rag-progress-label');
  if (total <= 0) { wrap.style.display = 'none'; return; }
  wrap.style.display = 'block';
  const pct = Math.round(current / total * 100);
  bar.style.width = pct + '%';
  label.textContent = `${current} / ${total}`;
  if (current >= total) {
    setTimeout(() => { wrap.style.display = 'none'; }, 3000);
  }
}

function appendRagLog(line) {
  if (line.startsWith('__status__:')) {
    const status = line.slice(11);
    updateRagBadge(status);
    if (status === 'idle' || status === 'done' || status === 'error') {
      updateRagProgress(0, 0);
    }
    return;
  }
  if (line.startsWith('__progress__:')) {
    const [cur, tot] = line.slice(13).split('/').map(Number);
    updateRagProgress(cur, tot);
    return;
  }
  const el = document.getElementById('rag-log');
  if (el.querySelector('span')) el.innerHTML = '';
  const div = document.createElement('div');
  if (line.includes('✅')) div.className = 'ok';
  else if (line.includes('⏭')) div.className = 'skip';
  else if (line.includes('❌')) div.className = 'fail';
  div.textContent = line;
  el.appendChild(div);
  el.scrollTop = el.scrollHeight;
}

function startRagLogStream() {
  if (ragLogES) ragLogES.close();
  ragLogES = new EventSource('/api/rag/log/stream');
  ragLogES.onmessage = e => appendRagLog(JSON.parse(e.data));
}

async function refreshRagStats() {
  try {
    const r = await fetch('/api/rag/stats');
    const d = await r.json();
    const s = d.stats || {};
    document.getElementById('stat-chunks').textContent = s.total_chunks ?? '—';
    document.getElementById('stat-books').textContent  = s.book_count   ?? '—';
    document.getElementById('stat-pdfs').textContent   = (d.books_pdfs || []).length;
    updateRagBadge(d.rag_status || 'idle');

    // 分类标签
    const types = s.by_type || {};
    const CN = {book:'微信读书', pdf:'PDF', article:'文章', report:'研报', other:'其他'};
    document.getElementById('rag-types').innerHTML =
      Object.entries(types).sort((a,b)=>b[1]-a[1]).map(([t,n]) =>
        `<span class="rag-type-tag">${CN[t]||t}：${n}</span>`
      ).join('');
  } catch(e) { console.error(e); }
}

async function uploadFile(file) {
  const fd = new FormData();
  fd.append('file', file);
  document.getElementById('rag-log').innerHTML = '';
  appendRagLog(`上传 ${file.name}（${(file.size/1024).toFixed(0)} KB）...`);
  updateRagBadge('running');
  const r = await fetch('/api/rag/upload', {method:'POST', body: fd});
  if (!r.ok) {
    const d = await r.json();
    appendRagLog('❌ 上传失败：' + (d.detail || ''));
    updateRagBadge('error');
  }
}

async function handleFileSelect(e) {
  const files = [...e.target.files];
  if (!files.length) return;
  startRagLogStream();
  for (const f of files) {
    if (!f.name.toLowerCase().endsWith('.pdf')) {
      appendRagLog(`⏭ 跳过非 PDF 文件：${f.name}`);
      continue;
    }
    await uploadFile(f);
  }
  e.target.value = '';
  setTimeout(refreshRagStats, 1000);
}

async function handleDrop(e) {
  e.preventDefault();
  document.getElementById('drop-zone').classList.remove('drag-over');
  const files = [...e.dataTransfer.files].filter(f => f.name.toLowerCase().endsWith('.pdf'));
  if (!files.length) { appendRagLog('⚠️ 请拖入 PDF 文件'); return; }
  startRagLogStream();
  for (const f of files) await uploadFile(f);
  setTimeout(refreshRagStats, 1000);
}

async function ragIngestDir(recursive) {
  startRagLogStream();
  document.getElementById('rag-log').innerHTML = '';
  updateRagBadge('running');
  updateRagProgress(0, 0);
  const r = await fetch(`/api/rag/ingest-dir?recursive=${recursive}`, {method:'POST'});
  if (!r.ok) {
    const d = await r.json();
    appendRagLog('❌ ' + (d.detail || ''));
    updateRagBadge('error');
  }
  setTimeout(refreshRagStats, 2000);
}

// ── 百度网盘 ──────────────────────────────────────────────────────────────────

let bduLogES = null;
let bduFiles = [];   // 当前目录文件列表

async function refreshBduStatus() {
  const r = await fetch('/api/baidu/status');
  const d = await r.json();
  const badge = document.getElementById('bdu-badge');
  const btnAuth = document.getElementById('btn-bdu-auth');
  const btnDis  = document.getElementById('btn-bdu-disconnect');
  const browser = document.getElementById('bdu-browser');

  if (d.connected) {
    badge.style.background = '#14532d'; badge.style.color = '#4ade80';
    badge.textContent = '✅ ' + (d.netdisk_name || '已连接');
    btnAuth.style.display = 'none';
    btnDis.style.display  = '';
    browser.style.display = '';
    document.getElementById('bdu-auto-section').style.display = '';
    refreshBduAutoState();
  } else {
    badge.style.background = '#1e293b'; badge.style.color = '#64748b';
    badge.textContent = '未连接';
    btnAuth.style.display = '';
    btnDis.style.display  = 'none';
    browser.style.display = 'none';
    document.getElementById('bdu-auto-section').style.display = 'none';
  }

  // 同步状态
  const syncStatus = d.sync_status || 'idle';
  document.getElementById('btn-bdu-sync').disabled = syncStatus === 'syncing';

  // 追加日志
  const logEl = document.getElementById('bdu-log');
  if (d.sync_log && d.sync_log.length) {
    if (logEl.querySelector('span')) logEl.innerHTML = '';
    d.sync_log.forEach(line => {
      const div = document.createElement('div');
      if (line.includes('✅')) div.className = 'ok';
      else if (line.includes('❌')) div.className = 'fail';
      div.textContent = line;
      logEl.appendChild(div);
    });
    logEl.scrollTop = logEl.scrollHeight;
  }
}

async function bduAuth() {
  try {
    const r = await fetch('/api/baidu/auth-url');
    if (!r.ok) { const d = await r.json(); alert(d.detail); return; }
    const {url} = await r.json();
    const popup = window.open(url, 'bdu_auth', 'width=600,height=700');
    // 轮询弹窗关闭后刷新状态
    const timer = setInterval(() => {
      if (popup.closed) { clearInterval(timer); refreshBduStatus(); }
    }, 1000);
  } catch(e) { alert('获取授权 URL 失败：' + e); }
}

async function bduDisconnect() {
  if (!confirm('确定断开百度网盘连接？')) return;
  await fetch('/api/baidu/disconnect', {method:'DELETE'});
  refreshBduStatus();
}

async function bduListDir() {
  const dir = document.getElementById('bdu-dir').value.trim() || '/';
  const listEl = document.getElementById('bdu-file-list');
  listEl.innerHTML = '<div style="color:#64748b;padding:12px 14px">加载中...</div>';
  try {
    const r = await fetch('/api/baidu/files?dir=' + encodeURIComponent(dir));
    if (!r.ok) { const d = await r.json(); listEl.innerHTML = `<div style="color:#f87171;padding:12px">${d.detail}</div>`; return; }
    const {files} = await r.json();
    bduFiles = files;
    if (!files.length) {
      listEl.innerHTML = '<div style="color:#475569;padding:12px 14px">目录中没有 PDF 文件或子目录</div>';
      return;
    }
    listEl.innerHTML = files.map((f, i) => {
      if (f.is_dir) {
        return `<div class="bdu-file-item" onclick="bduNavDir('${f.path.replace(/'/g,"\\'")}')">
          <span>📁</span>
          <span class="bdu-file-name">${f.name}/</span>
          <span class="bdu-file-size">目录</span>
        </div>`;
      }
      const kb = Math.round(f.size / 1024);
      const mb = (f.size / 1024 / 1024).toFixed(1);
      return `<div class="bdu-file-item">
        <input type="checkbox" data-idx="${i}" checked>
        <span>📄</span>
        <span class="bdu-file-name">${f.name}</span>
        <span class="bdu-file-size">${kb > 1024 ? mb + ' MB' : kb + ' KB'}</span>
      </div>`;
    }).join('');
  } catch(e) { listEl.innerHTML = `<div style="color:#f87171;padding:12px">${e}</div>`; }
}

function bduNavDir(path) {
  document.getElementById('bdu-dir').value = path;
  bduListDir();
}

async function bduSync() {
  const checked = [...document.querySelectorAll('#bdu-file-list input[type=checkbox]:checked')];
  if (!checked.length) { alert('请先勾选要同步的 PDF 文件'); return; }
  const selected = checked.map(cb => bduFiles[parseInt(cb.dataset.idx)]);

  const logEl = document.getElementById('bdu-log');
  logEl.innerHTML = '';
  document.getElementById('btn-bdu-sync').disabled = true;

  // 启动 SSE 日志流
  if (bduLogES) bduLogES.close();
  bduLogES = new EventSource('/api/baidu/sync/log/stream');
  bduLogES.onmessage = e => {
    const line = JSON.parse(e.data);
    if (line.startsWith('__status__:')) {
      const st = line.slice(11);
      if (st !== 'syncing') {
        document.getElementById('btn-bdu-sync').disabled = false;
        refreshRagStats();
      }
      return;
    }
    if (logEl.querySelector('span')) logEl.innerHTML = '';
    const div = document.createElement('div');
    if (line.includes('✅')) div.className = 'ok';
    else if (line.includes('❌')) div.className = 'fail';
    else if (line.includes('⬇')) div.className = 'info';
    div.textContent = line;
    logEl.appendChild(div);
    logEl.scrollTop = logEl.scrollHeight;
  };

  await fetch('/api/baidu/sync', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({files: selected})
  });
}

// ── 自动同步 ──────────────────────────────────────────────────────────────────

async function refreshBduAutoState() {
  try {
    const r = await fetch('/api/baidu/auto-sync');
    if (!r.ok) return;
    const {config, state, synced_count} = await r.json();
    document.getElementById('bdu-auto-enabled').checked = config.enabled;
    document.getElementById('bdu-watch-dir').value = (config.watch_dirs || ['/'])[0];
    const sel = document.getElementById('bdu-interval');
    sel.value = String(config.interval_minutes);
    const stEl = document.getElementById('bdu-auto-state');
    const parts = [];
    if (state.last_run) parts.push(`上次：${state.last_run}（新增 ${state.last_new} 本）`);
    if (state.next_run && config.enabled) parts.push(`下次：${state.next_run}`);
    parts.push(`已同步：${synced_count} 个文件`);
    if (state.running) parts.push('🔄 同步中...');
    stEl.textContent = parts.join('　');
  } catch(e) {}
}

async function bduAutoToggle(enabled) {
  await fetch('/api/baidu/auto-sync/config', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({enabled})
  });
  refreshBduAutoState();
}

async function bduAutoSaveConfig() {
  const watch_dirs = [document.getElementById('bdu-watch-dir').value.trim() || '/'];
  const interval_minutes = parseInt(document.getElementById('bdu-interval').value);
  const r = await fetch('/api/baidu/auto-sync/config', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({watch_dirs, interval_minutes})
  });
  if (r.ok) { refreshBduAutoState(); } else { alert('保存失败'); }
}

async function bduAutoRunNow() {
  const r = await fetch('/api/baidu/auto-sync/run-now', {method:'POST'});
  if (!r.ok) { const d = await r.json(); alert(d.detail); return; }
  const logEl = document.getElementById('bdu-log');
  logEl.innerHTML = '';
  if (bduLogES) bduLogES.close();
  bduLogES = new EventSource('/api/baidu/sync/log/stream');
  bduLogES.onmessage = e => {
    const line = JSON.parse(e.data);
    if (line.startsWith('__status__:')) { refreshBduAutoState(); refreshRagStats(); return; }
    if (logEl.querySelector('span')) logEl.innerHTML = '';
    const div = document.createElement('div');
    if (line.includes('✅')) div.className = 'ok';
    else if (line.includes('❌')) div.className = 'fail';
    else if (line.includes('⬇')) div.className = 'info';
    div.textContent = line;
    logEl.appendChild(div);
    logEl.scrollTop = logEl.scrollHeight;
  };
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
startRagLogStream();
refreshCards();
refreshRagStats();
pollRunStatus();
</script>
</body>
</html>"""

if __name__ == "__main__":
    print("看板启动中... 访问 http://localhost:8765")
    uvicorn.run(app, host="0.0.0.0", port=8765, log_level="warning")
