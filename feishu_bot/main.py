"""
飞书 Bot 服务。

功能：
  - 接收用户在飞书中发来的消息
  - 路由到 RAG 问答（问号开头）或 digest 摘要（触发词）
  - 回复富文本卡片

配置（环境变量）：
  FEISHU_APP_ID           飞书应用 App ID
  FEISHU_APP_SECRET       飞书应用 App Secret
  FEISHU_VERIFY_TOKEN     事件订阅 Verification Token
  FEISHU_ENCRYPT_KEY      事件订阅加密 Key（可空）
  ANTHROPIC_API_KEY       Claude API Key
  RAG_DB_PATH             RAG 数据库路径（默认 ./output/rag.db）

部署：
  pip install lark-oapi fastapi uvicorn
  uvicorn main:app --host 0.0.0.0 --port 8002

飞书后台配置：
  1. 创建应用，开启机器人能力
  2. 事件订阅 → 请求地址填 https://your-domain:8002/feishu/events
  3. 添加事件：接收消息（im.message.receive_v1）
  4. 给机器人赋权：读取消息、发送消息
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path

# 确保 x_agent 包可以被导入
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, Request, HTTPException, Response
import anthropic

app = FastAPI(title="飞书 RAG Bot", version="0.1.0")

# ── 配置 ─────────────────────────────────────────────────────────────────────

APP_ID       = os.getenv("FEISHU_APP_ID", "")
APP_SECRET   = os.getenv("FEISHU_APP_SECRET", "")
VERIFY_TOKEN = os.getenv("FEISHU_VERIFY_TOKEN", "")
ENCRYPT_KEY  = os.getenv("FEISHU_ENCRYPT_KEY", "")

_claude: anthropic.Anthropic | None = None

def _get_claude() -> anthropic.Anthropic:
    global _claude
    if _claude is None:
        _claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _claude


# ── 消息去重（避免飞书重推导致重复回复） ─────────────────────────────────────

_seen_events: set[str] = set()
_seen_ts: list[tuple[float, str]] = []   # (timestamp, event_id) 用于 GC

def _dedup(event_id: str) -> bool:
    """返回 True 表示已处理过（重复），False 表示新事件。"""
    now = time.time()
    # 清理 5 分钟前的记录
    while _seen_ts and now - _seen_ts[0][0] > 300:
        _, old = _seen_ts.pop(0)
        _seen_events.discard(old)
    if event_id in _seen_events:
        return True
    _seen_events.add(event_id)
    _seen_ts.append((now, event_id))
    return False


# ── 飞书消息 API ──────────────────────────────────────────────────────────────

import requests as _req

_token_cache: dict[str, str | float] = {"token": "", "expire": 0.0}

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


def _send_message(receive_id: str, content: str, receive_id_type: str = "open_id"):
    """向飞书发送文本消息。"""
    token = _get_access_token()
    payload = {
        "receive_id": receive_id,
        "msg_type": "text",
        "content": json.dumps({"text": content[:4000]}),
    }
    r = _req.post(
        f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=15,
    )
    return r.json()


def _reply_message(msg_id: str, content: str):
    """回复某条消息（保持上下文）。"""
    token = _get_access_token()
    r = _req.post(
        f"https://open.feishu.cn/open-apis/im/v1/messages/{msg_id}/reply",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"msg_type": "text", "content": json.dumps({"text": content[:4000]})},
        timeout=15,
    )
    return r.json()


# ── 消息路由 ─────────────────────────────────────────────────────────────────

HELP_TEXT = """\
🤖 X-Agent RAG 助手

使用方式：
  直接发消息 → RAG 问答（从书籍/研报/文章中检索回答）
  摘要 / digest → 查看最新行情摘要

支持来源：微信读书书籍 📚、淘股吧文章 📰、研报 📋
"""

def _handle_text(text: str, sender_id: str, msg_id: str):
    """处理用户文本消息，返回回复字符串。"""
    text = text.strip()

    # 帮助
    if text in ("帮助", "help", "？", "?", "/help"):
        return HELP_TEXT

    # 摘要
    if text.lower() in ("摘要", "digest", "行情", "市场"):
        try:
            digest_path = Path("./output/digest.md")
            if digest_path.exists():
                content = digest_path.read_text(encoding="utf-8")
                # 只取前 3000 字
                return content[:3000] + ("\n\n…（内容过长，请查看完整 digest.md）" if len(content) > 3000 else "")
            else:
                return "暂无摘要，请先运行 `python main.py` 生成。"
        except Exception as e:
            return f"读取摘要失败: {e}"

    # RAG 问答（其余消息全部进 RAG）
    try:
        from x_agent.rag import collection_stats
        stats = collection_stats()
        if stats["total_chunks"] == 0:
            return "📚 知识库为空，请先入库书籍或文章。\n\n运行：`python -m x_agent.rag ingest <文件>`"

        from x_agent.rag import ask
        result = ask(question=text, client=_get_claude(), top_k=5, use_rerank=True)
        answer = result["answer"]
        sources = result["sources"]

        # 格式化来源
        src_lines = []
        for s in sources[:3]:
            t = s.get("title", "")
            st = s.get("source_type", "")
            icon = {"book": "📖", "article": "📰", "report": "📋"}.get(st, "📄")
            if t:
                src_lines.append(f"{icon} {t[:40]}")
        src_str = "\n".join(src_lines)

        reply = f"{answer}"
        if src_str:
            reply += f"\n\n**来源：**\n{src_str}"
        return reply

    except Exception as e:
        return f"⚠️ 回答出错：{e}\n\n请检查 ANTHROPIC_API_KEY 是否已设置。"


# ── 飞书事件解密 ──────────────────────────────────────────────────────────────

def _decrypt_body(encrypted: str) -> dict:
    """AES 解密飞书事件体（如果设置了 ENCRYPT_KEY）。"""
    import base64
    from Crypto.Cipher import AES

    key = hashlib.sha256(ENCRYPT_KEY.encode()).digest()
    encrypted_bytes = base64.b64decode(encrypted)
    iv = encrypted_bytes[:16]
    cipher = AES.new(key, AES.MODE_CBC, iv)
    decrypted = cipher.decrypt(encrypted_bytes[16:])
    # PKCS7 unpad
    pad = decrypted[-1]
    decrypted = decrypted[:-pad]
    return json.loads(decrypted)


# ── Webhook 路由 ──────────────────────────────────────────────────────────────

@app.post("/feishu/events")
async def feishu_events(request: Request):
    body = await request.json()

    # 处理加密
    if "encrypt" in body:
        if not ENCRYPT_KEY:
            raise HTTPException(400, "FEISHU_ENCRYPT_KEY not set")
        body = _decrypt_body(body["encrypt"])

    # URL 验证（飞书配置事件订阅时的握手请求）
    if "challenge" in body:
        return {"challenge": body["challenge"]}

    # 验证 token
    header = body.get("header", {})
    if VERIFY_TOKEN and header.get("token") != VERIFY_TOKEN:
        raise HTTPException(401, "Invalid token")

    event_id = header.get("event_id", "")
    if event_id and _dedup(event_id):
        return {"code": 0}   # 已处理过，幂等

    event = body.get("event", {})
    event_type = header.get("event_type", body.get("type", ""))

    if event_type == "im.message.receive_v1":
        msg  = event.get("message", {})
        msg_id   = msg.get("message_id", "")
        msg_type = msg.get("message_type", "")
        chat_type = msg.get("chat_type", "")

        # 只处理文本消息
        if msg_type == "text":
            content_raw = msg.get("content", "{}")
            text = json.loads(content_raw).get("text", "").strip()

            # 群消息中需要 @bot，私聊直接回答
            sender_id = event.get("sender", {}).get("sender_id", {}).get("open_id", "")

            if text:
                reply = _handle_text(text, sender_id, msg_id)
                if msg_id:
                    _reply_message(msg_id, reply)
                elif sender_id:
                    _send_message(sender_id, reply)

    return {"code": 0}


@app.get("/health")
def health():
    return {"status": "ok", "rag_chunks": _rag_chunk_count()}


def _rag_chunk_count() -> int:
    try:
        from x_agent.rag import collection_stats
        return collection_stats()["total_chunks"]
    except Exception:
        return -1


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("FEISHU_BOT_PORT", "8002"))
    print(f"飞书 Bot 启动在 :{port}")
    print(f"  App ID: {APP_ID or '(未设置 FEISHU_APP_ID)'}")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
