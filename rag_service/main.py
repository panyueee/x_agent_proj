"""
RAG FastAPI 服务。

端点：
  POST /ingest/text     — 入库纯文本
  POST /ingest/book     — 入库整本书（章节列表）
  POST /ingest/article  — 入库单篇文章
  POST /query           — 语义检索（返回原始块）
  POST /ask             — RAG 问答（检索 + Claude 生成）
  GET  /stats           — 向量库统计
  GET  /health          — 健康检查

认证：X-Api-Key 请求头（读取 RAG_API_KEY 环境变量）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 确保 x_agent 包可以被导入
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel
from typing import Optional
import anthropic

app = FastAPI(title="X-Agent RAG Service", version="0.1.0")

_API_KEY = os.getenv("RAG_API_KEY", "")


def _auth(x_api_key: str = Header(default="")):
    if _API_KEY and x_api_key != _API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


# ── 懒加载 Anthropic 客户端 ───────────────────────────────────────────────────
_claude: Optional[anthropic.Anthropic] = None

def _get_claude():
    global _claude
    if _claude is None:
        _claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _claude


# ── 请求/响应模型 ──────────────────────────────────────────────────────────────

class IngestTextReq(BaseModel):
    text: str
    source_id: str
    source_type: str = "other"   # book | article | report | other
    title: str = ""
    author: str = ""
    extra_meta: Optional[dict] = None

class Chapter(BaseModel):
    title: str = ""
    content: str

class IngestBookReq(BaseModel):
    title: str
    chapters: list[Chapter]
    author: str = ""
    source_id: Optional[str] = None

class IngestArticleReq(BaseModel):
    url: str
    content: str
    title: str = ""
    author: str = ""
    published_at: str = ""
    source_type: str = "article"

class QueryReq(BaseModel):
    query: str
    top_k: int = 6
    source_type: Optional[str] = None

class AskReq(BaseModel):
    question: str
    top_k: int = 6
    source_type: Optional[str] = None
    model: str = "claude-sonnet-4-6"


# ── 路由 ──────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/stats", dependencies=[Depends(_auth)])
def stats():
    from x_agent.rag import collection_stats
    return collection_stats()


@app.post("/ingest/text", dependencies=[Depends(_auth)])
def ingest_text_endpoint(req: IngestTextReq):
    from x_agent.rag import ingest_text
    n = ingest_text(
        text=req.text,
        source_id=req.source_id,
        source_type=req.source_type,
        title=req.title,
        author=req.author,
        extra_meta=req.extra_meta,
    )
    return {"chunks_added": n}


@app.post("/ingest/book", dependencies=[Depends(_auth)])
def ingest_book_endpoint(req: IngestBookReq):
    from x_agent.rag import ingest_book
    n = ingest_book(
        title=req.title,
        chapters=[{"title": c.title, "content": c.content} for c in req.chapters],
        author=req.author,
        source_id=req.source_id,
    )
    return {"chunks_added": n}


@app.post("/ingest/article", dependencies=[Depends(_auth)])
def ingest_article_endpoint(req: IngestArticleReq):
    from x_agent.rag import ingest_article
    n = ingest_article(
        url=req.url,
        content=req.content,
        title=req.title,
        author=req.author,
        published_at=req.published_at,
        source_type=req.source_type,
    )
    return {"chunks_added": n}


@app.post("/query", dependencies=[Depends(_auth)])
def query_endpoint(req: QueryReq):
    from x_agent.rag import retrieve
    hits = retrieve(req.query, top_k=req.top_k, source_type=req.source_type)
    return {"results": hits}


@app.post("/ask", dependencies=[Depends(_auth)])
def ask_endpoint(req: AskReq):
    from x_agent.rag import ask
    result = ask(
        question=req.question,
        client=_get_claude(),
        top_k=req.top_k,
        source_type=req.source_type,
        model=req.model,
    )
    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("RAG_PORT", "8001")),
                reload=False, workers=1)
