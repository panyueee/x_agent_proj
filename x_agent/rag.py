"""
RAG 引擎：BM25 混合检索 + Claude 重排 + Claude 生成回答。

依赖（均支持 Python 3.14）：
  - rank_bm25   — BM25 检索
  - jieba       — 中文分词
  - lancedb     — 向量检索（可选，需要 embedding 模型）
  - SQLite      — 文档存储（stdlib，无额外依赖）

向量检索在 Python 3.14 上受限（torch/onnxruntime 暂无 3.14 wheel），
默认使用 BM25 + Claude 重排，效果在金融/投资领域依然很好。

入库：ingest_text / ingest_book / ingest_article
检索：retrieve(query, top_k)
问答：ask(question, client)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Optional

# ── 常量 ─────────────────────────────────────────────────────────────────────

RAG_DB_PATH  = os.getenv("RAG_DB_PATH", "./output/rag.db")
CHUNK_SIZE   = 500     # 字符数
CHUNK_OVERLAP = 50
TOP_K_BM25   = 20     # BM25 候选数（再由 Claude 重排到 top_k）
TOP_K        = 6      # 默认返回条数


# ── SQLite 初始化 ─────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    Path(RAG_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(RAG_DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS chunks (
            id          TEXT PRIMARY KEY,
            source_id   TEXT NOT NULL,
            source_type TEXT NOT NULL DEFAULT 'other',
            title       TEXT DEFAULT '',
            author      TEXT DEFAULT '',
            chunk_idx   INTEGER DEFAULT 0,
            total_chunks INTEGER DEFAULT 1,
            content     TEXT NOT NULL,
            extra_meta  TEXT DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_id);
        CREATE INDEX IF NOT EXISTS idx_chunks_type   ON chunks(source_type);

        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
        USING fts5(id UNINDEXED, content, title, author, tokenize='unicode61');
    """)
    conn.commit()
    return conn


_conn: Optional[sqlite3.Connection] = None

def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = _get_conn()
    return _conn


# ── 分块工具 ─────────────────────────────────────────────────────────────────

def split_text(text: str, chunk_size: int = CHUNK_SIZE,
               overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    chunks, start = [], 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            for sep in ("。", "！", "？", "\n\n", "\n", "；"):
                pos = text.rfind(sep, start, end)
                if pos != -1:
                    end = pos + 1
                    break
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - overlap if end < len(text) else len(text)
    return chunks


def _doc_id(source_id: str, chunk_idx: int) -> str:
    h = hashlib.md5(f"{source_id}:{chunk_idx}".encode()).hexdigest()[:8]
    return f"{h}_{chunk_idx}"


# ── 入库接口 ─────────────────────────────────────────────────────────────────

def ingest_text(
    text: str,
    source_id: str,
    source_type: str = "other",
    title: str = "",
    author: str = "",
    extra_meta: Optional[dict] = None,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> int:
    """将文本分块后写入 SQLite，返回新增块数。"""
    db = _db()
    chunks = split_text(text, chunk_size, overlap)
    added = 0
    for i, chunk in enumerate(chunks):
        cid = _doc_id(source_id, i)
        exists = db.execute("SELECT 1 FROM chunks WHERE id=?", (cid,)).fetchone()
        if exists:
            continue
        db.execute(
            "INSERT INTO chunks (id,source_id,source_type,title,author,chunk_idx,total_chunks,content,extra_meta) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (cid, source_id, source_type, title[:200], author[:100],
             i, len(chunks), chunk, json.dumps(extra_meta or {}, ensure_ascii=False)),
        )
        db.execute(
            "INSERT INTO chunks_fts (id,content,title,author) VALUES (?,?,?,?)",
            (cid, chunk, title, author),
        )
        added += 1
    db.commit()
    return added


def ingest_book(
    title: str,
    chapters: list[dict],
    author: str = "",
    source_id: Optional[str] = None,
) -> int:
    if source_id is None:
        source_id = f"book:{hashlib.md5(title.encode()).hexdigest()[:12]}"
    total = 0
    for ch in chapters:
        content = ch.get("content", "")
        if not content.strip():
            continue
        ch_title = ch.get("title", "")
        total += ingest_text(
            text=content,
            source_id=f"{source_id}:{ch_title}",
            source_type="book",
            title=f"{title} — {ch_title}",
            author=author,
            extra_meta={"book_title": title, "chapter": ch_title},
        )
    return total


def ingest_article(
    url: str,
    content: str,
    title: str = "",
    author: str = "",
    published_at: str = "",
    source_type: str = "article",
) -> int:
    return ingest_text(
        text=content, source_id=url, source_type=source_type,
        title=title, author=author,
        extra_meta={"url": url, "published_at": published_at},
    )


# ── BM25 检索 ─────────────────────────────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    """jieba 分词，合并单字为双字以提升精度。"""
    try:
        import jieba
        tokens = list(jieba.cut(text))
    except ImportError:
        # 退回按字分割
        tokens = list(text)
    # 过滤空格/标点
    tokens = [t.strip() for t in tokens if t.strip() and len(t.strip()) > 0]
    return tokens


_BM25_PREFILTER = 200   # FTS5 先召回多少候选再交给 BM25 精排


def retrieve_bm25(query: str, top_k: int = TOP_K_BM25,
                  source_type: Optional[str] = None) -> list[dict]:
    """BM25 精排。先用 FTS5 召回 _BM25_PREFILTER 个候选，再做 BM25，内存用量恒定。"""
    try:
        from rank_bm25 import BM25Okapi
    except ImportError:
        return retrieve_fts(query, top_k, source_type)

    # 1. FTS5 预过滤：拿到有限候选集（不加载全表）
    candidates = retrieve_fts(query, top_k=_BM25_PREFILTER, source_type=source_type)

    # FTS5 未命中时退回全表抽样（数量有限，不影响内存安全）
    if not candidates:
        db = _db()
        if source_type:
            rows = db.execute(
                "SELECT id, source_id, source_type, title, author, content, extra_meta "
                "FROM chunks WHERE source_type=? LIMIT ?",
                (source_type, _BM25_PREFILTER),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT id, source_id, source_type, title, author, content, extra_meta "
                "FROM chunks LIMIT ?",
                (_BM25_PREFILTER,),
            ).fetchall()
        candidates = [
            {"id": r[0], "source_id": r[1], "source_type": r[2],
             "title": r[3], "author": r[4], "content": r[5],
             "meta": {"source_id": r[1], "source_type": r[2], "title": r[3],
                      "author": r[4], **(json.loads(r[6]) if r[6] else {})},
             "score": 0.0}
            for r in rows
        ]

    if not candidates:
        return []

    # 2. BM25 精排（仅对候选集）
    corpus = [_tokenize(c["content"]) for c in candidates]
    query_tokens = _tokenize(query)
    bm25 = BM25Okapi(corpus)
    scores = bm25.get_scores(query_tokens)

    for c, s in zip(candidates, scores):
        c["score"] = float(s)

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:top_k]


def retrieve_fts(query: str, top_k: int = TOP_K_BM25,
                 source_type: Optional[str] = None) -> list[dict]:
    """SQLite FTS5 全文检索（BM25 降级备用）。"""
    db = _db()
    safe_q = re.sub(r'["\'\*\-\+\(\)]', ' ', query)
    try:
        if source_type:
            rows = db.execute(
                """SELECT c.id, c.source_id, c.source_type, c.title, c.author, c.content, c.extra_meta,
                           rank
                    FROM chunks_fts f
                    JOIN chunks c ON c.id = f.id
                    WHERE f.chunks_fts MATCH ? AND c.source_type=?
                    ORDER BY rank LIMIT ?""",
                (safe_q, source_type, top_k)
            ).fetchall()
        else:
            rows = db.execute(
                """SELECT c.id, c.source_id, c.source_type, c.title, c.author, c.content, c.extra_meta,
                           rank
                    FROM chunks_fts f
                    JOIN chunks c ON c.id = f.id
                    WHERE f.chunks_fts MATCH ?
                    ORDER BY rank LIMIT ?""",
                (safe_q, top_k)
            ).fetchall()
    except Exception:
        return []

    results = []
    for r in rows:
        results.append({
            "id": r[0], "source_id": r[1], "source_type": r[2],
            "title": r[3], "author": r[4], "content": r[5],
            "meta": {"source_id": r[1], "source_type": r[2], "title": r[3],
                     "author": r[4], **(json.loads(r[6]) if r[6] else {})},
            "score": float(r[7]) if r[7] else 0.0,
        })
    return results


def retrieve(query: str, top_k: int = TOP_K,
             source_type: Optional[str] = None) -> list[dict]:
    """主检索入口：BM25 + FTS5 结果合并去重，取 top_k。"""
    bm25_hits = retrieve_bm25(query, top_k=TOP_K_BM25, source_type=source_type)
    fts_hits  = retrieve_fts(query, top_k=TOP_K_BM25 // 2, source_type=source_type)

    seen, merged = set(), []
    for h in bm25_hits + fts_hits:
        if h["id"] not in seen:
            seen.add(h["id"])
            merged.append(h)

    # 截取候选并归一化分数
    candidates = merged[:top_k * 3]
    max_s = max((h["score"] for h in candidates), default=1) or 1
    for h in candidates:
        h["score"] = round(abs(h["score"]) / max_s, 4)

    return candidates[:top_k]


# ── Claude 重排（可选） ────────────────────────────────────────────────────────

def rerank(query: str, hits: list[dict], client, top_k: int = TOP_K,
           model: str = "claude-haiku-4-5-20251001") -> list[dict]:
    """
    用 Claude Haiku 快速重排候选块，返回 top_k 个最相关的结果。
    候选数较少（<= top_k）时直接返回，不调用 API。
    """
    if len(hits) <= top_k:
        return hits

    snippets = "\n\n".join(
        f"[{i+1}] {h['meta'].get('title','?')}\n{h['content'][:300]}"
        for i, h in enumerate(hits)
    )
    prompt = (
        f"问题：{query}\n\n"
        f"以下是 {len(hits)} 个候选段落（按编号），请选出最相关的 {top_k} 个编号，"
        f"用逗号分隔，不要解释：\n\n{snippets}"
    )
    try:
        msg = client.messages.create(
            model=model, max_tokens=64,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        indices = [int(x.strip()) - 1 for x in re.findall(r"\d+", raw)]
        valid = [i for i in indices if 0 <= i < len(hits)][:top_k]
        if valid:
            return [hits[i] for i in valid]
    except Exception:
        pass
    return hits[:top_k]


# ── RAG 问答 ─────────────────────────────────────────────────────────────────

def ask(
    question: str,
    client,
    top_k: int = TOP_K,
    source_type: Optional[str] = None,
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 1024,
    use_rerank: bool = True,
) -> dict:
    """检索 → 重排 → Claude 生成回答。"""
    hits = retrieve(question, top_k=top_k * 3, source_type=source_type)
    if not hits:
        return {"answer": "知识库中暂无相关内容，请先入库文档。", "sources": []}

    if use_rerank and len(hits) > top_k:
        hits = rerank(question, hits, client, top_k=top_k)
    else:
        hits = hits[:top_k]

    context_parts = []
    for i, h in enumerate(hits, 1):
        header = f"[{i}] 《{h['meta'].get('title', '?')}》"
        if h["meta"].get("author"):
            header += f"  by {h['meta']['author']}"
        context_parts.append(f"{header}\n{h['content']}")

    context = "\n\n---\n\n".join(context_parts)
    system = (
        "你是一个金融/投资领域的专业助手。"
        "根据下方参考资料，用中文简洁准确地回答问题。"
        "如果资料中没有直接答案，请如实说明。"
    )
    user_msg = f"## 参考资料\n\n{context}\n\n## 问题\n\n{question}"

    msg = client.messages.create(
        model=model, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": user_msg}],
    )
    answer = msg.content[0].text.strip()
    sources = [
        {"title": h["meta"].get("title", ""), "score": h["score"],
         "source_type": h["meta"].get("source_type", ""),
         "url": h["meta"].get("url", "")}
        for h in hits
    ]
    return {"answer": answer, "sources": sources}


# ── 库状态 ────────────────────────────────────────────────────────────────────

def collection_stats() -> dict:
    db = _db()
    total = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    rows  = db.execute(
        "SELECT source_type, COUNT(*) FROM chunks GROUP BY source_type"
    ).fetchall()
    books = db.execute(
        "SELECT COUNT(DISTINCT source_id) FROM chunks WHERE source_type='book'"
    ).fetchone()[0]
    return {
        "total_chunks": total,
        "by_type": {r[0]: r[1] for r in rows},
        "book_count": books,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"

    if cmd == "stats":
        print(json.dumps(collection_stats(), ensure_ascii=False, indent=2))

    elif cmd == "ingest" and len(sys.argv) >= 3:
        path  = Path(sys.argv[2])
        title = next((sys.argv[i+1] for i, a in enumerate(sys.argv) if a == "--title"), path.stem)
        stype = next((sys.argv[i+1] for i, a in enumerate(sys.argv) if a == "--type"),  "other")
        n = ingest_text(path.read_text(encoding="utf-8"), source_id=str(path),
                        source_type=stype, title=title)
        print(f"入库完成：{n} 块")

    elif cmd == "query" and len(sys.argv) >= 3:
        q = " ".join(sys.argv[2:])
        for h in retrieve(q):
            print(f"\n[score={h['score']:.3f}] {h['meta'].get('title','?')}")
            print(h["content"][:200])

    else:
        print("用法: python -m x_agent.rag [stats | ingest <file> | query <问题>]")
