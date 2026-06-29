"""
飞书 Bot 服务。

功能：
  - 私聊：直接回复所有消息
  - 群聊：仅响应 @Bot 消息
  - 命令路由：RAG问答 / 摘要 / 恐慌指数 / 大盘 / 淘股吧 / 知识库 / 搜索
  - 回复富文本卡片（interactive card）
  - 主动推送接口：POST /push（供 main.py 调用触发预警）

配置（环境变量）：
  FEISHU_APP_ID           飞书应用 App ID
  FEISHU_APP_SECRET       飞书应用 App Secret
  FEISHU_VERIFY_TOKEN     事件订阅 Verification Token
  FEISHU_ENCRYPT_KEY      事件订阅加密 Key（可空）
  FEISHU_PUSH_CHAT_ID     主动推送目标群 chat_id（可空）
  ANTHROPIC_API_KEY       Claude API Key
  RAG_DB_PATH             RAG 数据库路径（默认 ./output/rag.db）

部署：
  pip install lark-oapi fastapi uvicorn requests pycryptodome
  uvicorn main:app --host 0.0.0.0 --port 8002

飞书后台配置：
  1. 创建应用，开启机器人能力
  2. 事件订阅 → 请求地址填 https://your-domain:8002/feishu/events
  3. 添加事件：接收消息（im.message.receive_v1）
  4. 给机器人赋权：读取消息 / 发送消息 / 获取群信息
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests as _req
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel

app = FastAPI(title="飞书 RAG Bot", version="0.2.0")

# ── 配置 ─────────────────────────────────────────────────────────────────────

APP_ID        = os.getenv("FEISHU_APP_ID", "")
APP_SECRET    = os.getenv("FEISHU_APP_SECRET", "")
VERIFY_TOKEN  = os.getenv("FEISHU_VERIFY_TOKEN", "")
ENCRYPT_KEY   = os.getenv("FEISHU_ENCRYPT_KEY", "")
PUSH_CHAT_ID  = os.getenv("FEISHU_PUSH_CHAT_ID", "")   # 主动推送的目标群

import anthropic as _anthropic
_claude: _anthropic.Anthropic | None = None

def _get_claude() -> _anthropic.Anthropic:
    global _claude
    if _claude is None:
        _claude = _anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _claude


# ── 消息去重 ──────────────────────────────────────────────────────────────────

_seen_events: set[str] = set()
_seen_ts: list[tuple[float, str]] = []

def _dedup(event_id: str) -> bool:
    now = time.time()
    while _seen_ts and now - _seen_ts[0][0] > 300:
        _, old = _seen_ts.pop(0)
        _seen_events.discard(old)
    if event_id in _seen_events:
        return True
    _seen_events.add(event_id)
    _seen_ts.append((now, event_id))
    return False


# ── 飞书 Token ────────────────────────────────────────────────────────────────

_token_cache: dict = {"token": "", "expire": 0.0}

def _get_access_token() -> str:
    if time.time() < float(_token_cache["expire"]) - 60:
        return str(_token_cache["token"])
    r = _req.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": APP_ID, "app_secret": APP_SECRET},
        timeout=10,
    )
    data = r.json()
    _token_cache["token"]  = data.get("tenant_access_token", "")
    _token_cache["expire"] = time.time() + int(data.get("expire", 3600))
    return str(_token_cache["token"])


def _bot_open_id() -> str:
    """获取 Bot 自身的 open_id（用于群消息 @过滤）。"""
    try:
        r = _req.get(
            "https://open.feishu.cn/open-apis/bot/v3/info",
            headers={"Authorization": f"Bearer {_get_access_token()}"},
            timeout=10,
        )
        return r.json().get("bot", {}).get("open_id", "")
    except Exception:
        return ""

_bot_open_id_cache: str = ""

def _get_bot_open_id() -> str:
    global _bot_open_id_cache
    if not _bot_open_id_cache:
        _bot_open_id_cache = _bot_open_id()
    return _bot_open_id_cache


# ── 卡片发送 ──────────────────────────────────────────────────────────────────

def _card(title: str, body_md: str, color: str = "blue") -> dict:
    """构建飞书 interactive card JSON。body_md 支持飞书 lark_md 语法。"""
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": color,
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": body_md[:4000]},
            }
        ],
    }


def _send_card(receive_id: str, card: dict, receive_id_type: str = "open_id") -> dict:
    token = _get_access_token()
    r = _req.post(
        f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "receive_id": receive_id,
            "msg_type":   "interactive",
            "content":    json.dumps(card),
        },
        timeout=15,
    )
    return r.json()


def _reply_card(msg_id: str, card: dict) -> dict:
    token = _get_access_token()
    r = _req.post(
        f"https://open.feishu.cn/open-apis/im/v1/messages/{msg_id}/reply",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"msg_type": "interactive", "content": json.dumps(card)},
        timeout=15,
    )
    return r.json()


def _reply_text(msg_id: str, text: str) -> dict:
    """纯文本回复（降级备用）。"""
    token = _get_access_token()
    r = _req.post(
        f"https://open.feishu.cn/open-apis/im/v1/messages/{msg_id}/reply",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"msg_type": "text", "content": json.dumps({"text": text[:4000]})},
        timeout=15,
    )
    return r.json()


# ── 命令处理 ──────────────────────────────────────────────────────────────────

HELP_TEXT = """\
**🤖 X-Agent 飞书助手**

**命令列表：**
• 直接发问题 → RAG 知识库问答
• `摘要` / `digest` → 最新行情摘要
• `恐慌指数` / `panic` → 当前 Panic Index
• `大盘` / `行情` → 市场行情快照
• `淘股吧` / `tgb` → 大V最新动态
• `知识库` / `kb` → 知识库入库统计
• `搜索 <关键词>` → 知识库检索（不生成回答）
• `状态` / `status` → 系统运行状态
• `帮助` / `help` → 本帮助
"""


def _cmd_digest() -> tuple[str, str, str]:
    """返回 (title, body_md, color)。"""
    try:
        p = Path("./output/digest.md")
        if not p.exists():
            return "摘要", "暂无摘要，请先运行 `python main.py` 生成。", "grey"
        content = p.read_text(encoding="utf-8")
        # 取前 3800 字，截断在段落边界
        if len(content) > 3800:
            content = content[:3800].rsplit("\n", 1)[0] + "\n\n…（内容过长，请查看完整 digest.md）"
        return "📊 行情摘要", content, "blue"
    except Exception as e:
        return "摘要", f"读取失败：{e}", "red"


def _cmd_panic() -> tuple[str, str, str]:
    try:
        from x_agent.storage import Store
        store = Store()
        snaps = store.recent_panic_snapshots(limit=1)
        if not snaps:
            return "Panic Index", "暂无数据，请先运行 `python main.py --source psych`。", "grey"
        s = snaps[0]
        score = s["panic_score"]
        filled = int(score / 5)
        bar = "█" * filled + "░" * (20 - filled)
        emotion_cn = {"panic": "恐慌 😱", "greed": "贪婪 🤑", "neutral": "中性 😐"}.get(
            s["dominant_emotion"], s["dominant_emotion"]
        )
        signal_cn = {"buy": "🔼 逆向买入预警", "sell": "🔽 逆向减仓预警",
                     "neutral": "— 无逆向信号"}.get(s["contrarian_signal"], "")
        color = "red" if score >= 70 else ("green" if score <= 30 else "orange")
        ts = s["computed_at"][:16].replace("T", " ")

        body = (
            f"**{score:.0f} / 100**  `[{bar}]`\n\n"
            f"情绪：{emotion_cn}　信号：{signal_cn}\n\n"
            f"恐慌帖 **{s['fear_count']}** 条 / 贪婪帖 **{s['greed_count']}** 条 "
            f"/ 共扫描 {s['total_posts']} 条\n\n"
            f"更新：{ts} UTC"
        )
        llm = s.get("llm_report") or {}
        if llm.get("crowd_psychology"):
            body += f"\n\n> {llm['crowd_psychology']}"
        if llm.get("short_term_outlook"):
            body += f"\n\n**展望**：{llm['short_term_outlook']}"
        return "🧠 Panic Index", body, color
    except Exception as e:
        return "Panic Index", f"获取失败：{e}", "red"


def _cmd_market() -> tuple[str, str, str]:
    try:
        from x_agent.storage import Store
        store = Store()
        lines = []
        for market, label in [("a_shares", "A 股"), ("us_stocks", "美 股"),
                               ("crypto", "加密"), ("index", "指数")]:
            rows = store.recent_price_bars(market, limit=5)
            if not rows:
                continue
            lines.append(f"**{label}**")
            for row in rows:
                sym, name, _, ts, _, _, _, close, _, pct = row
                arrow = "📈" if (pct or 0) >= 0 else "📉"
                pct_str = f"{pct:+.2f}%" if pct is not None else "—"
                lines.append(f"  {arrow} `{sym}` {name}  **{close:.4g}**  {pct_str}")
        if not lines:
            return "大盘行情", "暂无行情数据。", "grey"
        return "💹 市场行情", "\n".join(lines), "blue"
    except Exception as e:
        return "大盘行情", f"获取失败：{e}", "red"


def _cmd_tgb() -> tuple[str, str, str]:
    try:
        from x_agent.storage import Store
        store = Store()
        rows = store.conn.execute(
            "SELECT author, text, url, created_at, group_tag "
            "FROM tweets WHERE source='taoguba' ORDER BY created_at DESC LIMIT 15"
        ).fetchall()
        if not rows:
            return "淘股吧", "暂无淘股吧数据，请先运行 `python main.py --source tgb`。", "grey"

        posts   = [(a, t, u, c) for a, t, u, c, g in rows if g == "taoguba"]
        replies = [(a, t, u, c) for a, t, u, c, g in rows if g == "taoguba_reply"]

        lines = []
        if posts:
            lines.append("**📝 最新博文**")
            for author, text, url, created_at in posts[:5]:
                title = text.split("\n")[0][:60]
                ts = (created_at or "")[:10]
                lines.append(f"  · {ts}  {title}")
                if url:
                    lines.append(f"    [查看]({url})")
        if replies:
            lines.append("\n**💬 最新评论**")
            for author, text, url, created_at in replies[:5]:
                summary = text.replace("[评论]", "").strip()[:80]
                ts = (created_at or "")[:10]
                lines.append(f"  · {ts}  {summary}")
        return "📝 淘股吧动态", "\n".join(lines), "wathet"
    except Exception as e:
        return "淘股吧", f"获取失败：{e}", "red"


def _cmd_kb() -> tuple[str, str, str]:
    try:
        from x_agent.rag import collection_stats
        stats = collection_stats()
        if stats["total_chunks"] == 0:
            return "知识库", "知识库为空，请先入库文档。", "grey"
        by_type = stats.get("by_type", {})
        type_cn = {"book": "微信读书 📖", "pdf": "PDF 📄", "article": "文章 📰",
                   "report": "研报 📋", "other": "其他"}
        lines = [
            f"共 **{stats['total_chunks']}** 个知识块 | 书籍 **{stats['book_count']}** 本",
            "",
        ]
        for t, n in sorted(by_type.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"  {type_cn.get(t, t)}：{n} 块")
        return "📚 知识库状态", "\n".join(lines), "green"
    except Exception as e:
        return "知识库", f"获取失败：{e}", "red"


def _cmd_status() -> tuple[str, str, str]:
    """系统状态：DB 统计 + digest 最后更新时间 + RAG 知识库。"""
    import datetime as _dt
    lines = []

    # Digest 最后更新
    p = Path("./output/digest.md")
    if p.exists():
        ts = _dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        lines.append(f"📊 摘要最后更新：**{ts}**")
    else:
        lines.append("📊 摘要：尚未生成")

    # DB 推文统计
    try:
        from x_agent.storage import Store
        store = Store()
        total_tweets = store.conn.execute("SELECT COUNT(*) FROM tweets").fetchone()[0]
        tgb_count    = store.conn.execute(
            "SELECT COUNT(*) FROM tweets WHERE source='taoguba'"
        ).fetchone()[0]
        xhs_count    = store.conn.execute(
            "SELECT COUNT(*) FROM tweets WHERE source='xiaohongshu'"
        ).fetchone()[0]
        lines.append(
            f"🗃 数据库：推文 **{total_tweets}** 条"
            f"（淘股吧 {tgb_count} / 小红书 {xhs_count}）"
        )
        # 最新一条入库时间
        last_row = store.conn.execute(
            "SELECT created_at FROM tweets ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if last_row:
            lines.append(f"⏱ 最新内容：{(last_row[0] or '')[:16].replace('T',' ')}")
    except Exception as e:
        lines.append(f"🗃 数据库：读取失败 ({e})")

    # RAG
    try:
        from x_agent.rag import collection_stats
        s = collection_stats()
        lines.append(f"📚 知识库：**{s['total_chunks']}** 块 / 书籍 **{s['book_count']}** 本")
    except Exception:
        lines.append("📚 知识库：未初始化")

    return "🖥 系统状态", "\n".join(lines), "grey"


def _cmd_search(query: str) -> tuple[str, str, str]:
    try:
        from x_agent.rag import retrieve, collection_stats
        if collection_stats()["total_chunks"] == 0:
            return "搜索", "知识库为空。", "grey"
        hits = retrieve(query, top_k=5)
        if not hits:
            return "搜索", f"未找到与「{query}」相关的内容。", "grey"
        lines = [f"**搜索：{query}**  找到 {len(hits)} 条\n"]
        for i, h in enumerate(hits, 1):
            title = h["meta"].get("title", "?")[:40]
            snippet = h["content"][:120].replace("\n", " ")
            score = h["score"]
            page_info = ""
            if h["meta"].get("page_start"):
                page_info = f"  P.{h['meta']['page_start']}-{h['meta']['page_end']}"
            lines.append(f"**[{i}]** 《{title}》{page_info}  相关度 {score:.2f}")
            lines.append(f"  {snippet}…")
        return "🔍 知识库检索", "\n".join(lines), "purple"
    except Exception as e:
        return "搜索", f"检索失败：{e}", "red"


def _cmd_rag(question: str) -> tuple[str, str, str]:
    try:
        from x_agent.rag import collection_stats, ask
        if collection_stats()["total_chunks"] == 0:
            return "问答", "📚 知识库为空，请先入库书籍或文章。", "grey"
        result = ask(question=question, client=_get_claude(), top_k=5, use_rerank=True)
        answer = result["answer"]
        sources = result.get("sources", [])

        src_lines = []
        for s in sources[:3]:
            t = s.get("title", "")[:35]
            st = s.get("source_type", "")
            icon = {"book": "📖", "pdf": "📄", "article": "📰", "report": "📋"}.get(st, "📄")
            page = ""
            if s.get("page_start"):
                page = f" P.{s['page_start']}"
            if t:
                src_lines.append(f"{icon} {t}{page}")

        body = answer
        if src_lines:
            body += "\n\n**来源：**\n" + "\n".join(src_lines)
        return "🤖 RAG 回答", body, "blue"
    except Exception as e:
        return "RAG 问答", f"⚠️ 出错：{e}\n\n请检查 ANTHROPIC_API_KEY。", "red"


def _handle_text(text: str) -> tuple[str, str, str]:
    """路由文本命令，返回 (title, body_md, color)。"""
    t = text.strip()
    tl = t.lower()

    if tl in ("帮助", "help", "？", "?", "/help"):
        return "🤖 X-Agent 帮助", HELP_TEXT, "blue"

    if tl in ("摘要", "digest"):
        return _cmd_digest()

    if tl in ("恐慌指数", "panic", "恐慌", "情绪"):
        return _cmd_panic()

    if tl in ("大盘", "行情", "市场", "market"):
        return _cmd_market()

    if tl in ("淘股吧", "tgb", "大v", "大V"):
        return _cmd_tgb()

    if tl in ("知识库", "kb", "知识", "rag状态"):
        return _cmd_kb()

    if tl.startswith("搜索 ") or tl.startswith("search "):
        query = t.split(" ", 1)[1].strip()
        if query:
            return _cmd_search(query)

    if tl in ("状态", "status", "系统状态"):
        return _cmd_status()

    # 其余进 RAG 问答
    return _cmd_rag(t)


# ── 飞书事件解密 ──────────────────────────────────────────────────────────────

def _decrypt_body(encrypted: str) -> dict:
    import base64
    from Crypto.Cipher import AES
    key = hashlib.sha256(ENCRYPT_KEY.encode()).digest()
    enc = base64.b64decode(encrypted)
    iv  = enc[:16]
    cipher = AES.new(key, AES.MODE_CBC, iv)
    raw = cipher.decrypt(enc[16:])
    pad = raw[-1]
    return json.loads(raw[:-pad])


# ── Webhook ───────────────────────────────────────────────────────────────────

@app.post("/feishu/events")
async def feishu_events(request: Request):
    body = await request.json()

    if "encrypt" in body:
        if not ENCRYPT_KEY:
            raise HTTPException(400, "FEISHU_ENCRYPT_KEY not set")
        body = _decrypt_body(body["encrypt"])

    # URL 验证握手
    if "challenge" in body:
        return {"challenge": body["challenge"]}

    # Token 验证
    header = body.get("header", {})
    if VERIFY_TOKEN and header.get("token") != VERIFY_TOKEN:
        raise HTTPException(401, "Invalid token")

    event_id = header.get("event_id", "")
    if event_id and _dedup(event_id):
        return {"code": 0}

    event     = body.get("event", {})
    event_type = header.get("event_type", body.get("type", ""))

    if event_type == "im.message.receive_v1":
        msg       = event.get("message", {})
        msg_id    = msg.get("message_id", "")
        msg_type  = msg.get("message_type", "")
        chat_type = msg.get("chat_type", "")   # "p2p" | "group"

        if msg_type != "text":
            return {"code": 0}

        content_raw = msg.get("content", "{}")
        text = json.loads(content_raw).get("text", "").strip()

        # 群聊：只响应 @Bot 的消息，并去掉 @mention 前缀
        if chat_type == "group":
            mentions = msg.get("mentions", [])
            bot_oid  = _get_bot_open_id()
            mentioned = any(
                m.get("id", {}).get("open_id") == bot_oid
                for m in mentions
            )
            if not mentioned:
                return {"code": 0}
            # 去掉 @昵称 前缀（飞书格式 @_user_xxx ）
            import re as _re
            text = _re.sub(r"@\S+\s*", "", text).strip()

        if not text:
            return {"code": 0}

        title, body_md, color = _handle_text(text)
        card = _card(title, body_md, color)
        if msg_id:
            _reply_card(msg_id, card)
        else:
            sender_id = event.get("sender", {}).get("sender_id", {}).get("open_id", "")
            if sender_id:
                _send_card(sender_id, card)

    return {"code": 0}


# ── 主动推送接口 ──────────────────────────────────────────────────────────────

class PushPayload(BaseModel):
    title:    str
    body:     str
    color:    str = "orange"
    chat_id:  str = ""          # 不填则用环境变量 FEISHU_PUSH_CHAT_ID

@app.post("/push_digest")
async def push_digest(chat_id: str = ""):
    """
    读取最新 digest.md，提取关键板块后推送到飞书群。
    main.py 每次 build_digest() 后自动调用。
    """
    target = chat_id or PUSH_CHAT_ID
    if not target:
        raise HTTPException(400, "chat_id 未设置")

    try:
        p = Path("./output/digest.md")
        if not p.exists():
            raise HTTPException(404, "digest.md 不存在")
        raw = p.read_text(encoding="utf-8")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))

    # 按 ## 切分板块，挑选重要的推送
    import re as _re
    sections = _re.split(r"\n(?=## )", raw)
    priority_keywords = ["行情", "Panic", "心理", "淘股吧", "策略信号", "Web3"]
    header_line = sections[0].strip() if sections else ""

    selected = [header_line] if header_line else []
    for sec in sections[1:]:
        if any(k in sec[:30] for k in priority_keywords):
            selected.append(sec.strip())
        if sum(len(s) for s in selected) > 3600:
            selected.append("\n…（更多内容见完整 digest.md）")
            break

    body = "\n\n".join(selected)
    if not body.strip():
        body = raw[:3800]

    ts = Path("./output/digest.md").stat().st_mtime
    import datetime as _dt
    ts_str = _dt.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")

    card = _card(f"📊 行情摘要  {ts_str}", body, "blue")
    result = _send_card(target, card, receive_id_type="chat_id")
    return {"ok": True, "feishu": result}


@app.post("/push")
async def push_alert(payload: PushPayload):
    """
    主动向飞书群推送卡片。供 main.py / cron 调用。

    示例（Panic Index 预警）：
        requests.post("http://localhost:8002/push", json={
            "title": "⚠️ Panic Index 预警",
            "body":  "当前 Panic Index = 78，建议关注逆向买入机会。",
            "color": "red",
        })
    """
    target = payload.chat_id or PUSH_CHAT_ID
    if not target:
        raise HTTPException(400, "chat_id 未设置（请填写 payload.chat_id 或设置 FEISHU_PUSH_CHAT_ID）")
    card = _card(payload.title, payload.body, payload.color)
    result = _send_card(target, card, receive_id_type="chat_id")
    return {"ok": True, "feishu": result}


# ── 健康检查 ──────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    rag_chunks = -1
    try:
        from x_agent.rag import collection_stats
        rag_chunks = collection_stats()["total_chunks"]
    except Exception:
        pass
    return {
        "status": "ok",
        "rag_chunks":  rag_chunks,
        "push_target": PUSH_CHAT_ID or "(未设置)",
        "app_id":      APP_ID or "(未设置)",
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("FEISHU_BOT_PORT", "8002"))
    print(f"飞书 Bot 启动在 :{port}")
    print(f"  App ID:      {APP_ID  or '(未设置 FEISHU_APP_ID)'}")
    print(f"  Push 群:     {PUSH_CHAT_ID or '(未设置 FEISHU_PUSH_CHAT_ID)'}")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
