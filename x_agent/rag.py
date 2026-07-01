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
import threading
from pathlib import Path
from typing import Optional

# ── 常量 ─────────────────────────────────────────────────────────────────────

RAG_DB_PATH    = os.getenv("RAG_DB_PATH", "./output/rag.db")
LANCE_DB_PATH  = os.getenv("LANCE_DB_PATH", "./output/rag_vectors")
CHUNK_SIZE     = 500     # 字符数
CHUNK_OVERLAP  = 50
TOP_K_BM25     = 20     # BM25 候选数（再由 Claude 重排到 top_k）
TOP_K_VECTOR   = 20     # 向量检索候选数
TOP_K          = 6      # 默认返回条数
VOYAGE_MODEL   = "voyage-finance-2"
VOYAGE_BATCH   = 128    # Voyage API 单批最大条数


# ── SQLite 初始化 ─────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    Path(RAG_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(RAG_DB_PATH, timeout=30)
    # 并发写安全：WAL 允许多读单写，busy_timeout 让写者等待而非立即报 "database is locked"
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
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

        -- content/title/author 存 jieba 预分词后的空格分隔词序列，
        -- 使 FTS5 能做中文词级别匹配（"宁德时代" → "宁德 时代"）
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
        USING fts5(id UNINDEXED, content, title, author, tokenize='unicode61');
    """)
    conn.commit()
    return conn


_local = threading.local()

def _db() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = _get_conn()
    return _local.conn


# ── 分块工具 ─────────────────────────────────────────────────────────────────

def split_text(text: str, chunk_size: int = CHUNK_SIZE,
               overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    n = len(text)
    chunks, start = [], 0
    min_chunk = chunk_size // 2   # 分隔符回退切出的块不得小于此值，否则宁可硬切
    while start < n:
        end = min(start + chunk_size, n)
        if end < n:
            # 在窗口内寻找分隔符断点；只接受不会把块切得过小的断点，
            # 避免窗口前部一个孤立 \n\n 导致切出碎块、再逐字符碾压。
            for sep in ("。", "！", "？", "\n\n", "\n", "；"):
                pos = text.rfind(sep, start, end)
                if pos != -1 and (pos + 1 - start) >= min_chunk:
                    end = pos + 1
                    break
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= n:   # 已到文末即停，否则会多切一个 overlap 长度的重复尾块
            break
        # 推进：保留 overlap，但绝不后退或停滞（end 恒 > start）
        start = end - overlap if end - overlap > start else end
    return chunks


def _doc_id(source_id: str, chunk_idx: int) -> str:
    h = hashlib.md5(f"{source_id}:{chunk_idx}".encode()).hexdigest()[:8]
    return f"{h}_{chunk_idx}"


def text_quality(text: str, min_chars: int = 40) -> tuple[bool, str]:
    """OCR/ASR 文本入库前有效性质检。返回 (是否有效, 原因)。
    拦截：过短(近空/纯乱码碎片)、字符多样性过低、连续重复子串(whisper 幻觉)。
    """
    if not text:
        return False, "empty"
    real = [c for c in text if ("一" <= c <= "鿿") or c.isalnum()]
    if len(real) < min_chars:
        return False, "too_short"
    # 字符多样性过低 → "啊啊啊"/"的的的" 之类
    if len(set(real)) / len(real) < 0.12:
        return False, "low_diversity"
    # 连续重复子串（whisper 对静音/音乐常见幻觉；OCR 偶发）
    collapsed = re.sub(r"(.{4,40}?)\1{3,}", r"\1", text)
    if len(collapsed) < len(text) * 0.5:
        return False, "repetitive"
    return True, "ok"


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
    skip_vectors: bool = False,
) -> int:
    """将文本分块后写入 SQLite，返回新增块数。"""
    db = _db()
    chunks = split_text(text, chunk_size, overlap)
    if not chunks:
        return 0

    # 批量查已存在的 id（1次查询代替 N 次）
    cids = [_doc_id(source_id, i) for i in range(len(chunks))]
    placeholders = ",".join("?" * len(cids))
    existing = {r[0] for r in db.execute(
        f"SELECT id FROM chunks WHERE id IN ({placeholders})", cids
    ).fetchall()}

    # title/author 只分词一次
    tok_title  = _tok_fts(title)
    tok_author = _tok_fts(author)
    meta_json  = json.dumps(extra_meta or {}, ensure_ascii=False)

    new_chunks: list[tuple[str, str]] = []  # (cid, content) 用于后续向量写入
    for i, chunk in enumerate(chunks):
        cid = cids[i]
        if cid in existing:
            continue
        db.execute(
            "INSERT INTO chunks (id,source_id,source_type,title,author,chunk_idx,total_chunks,content,extra_meta) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (cid, source_id, source_type, title[:200], author[:100],
             i, len(chunks), chunk, meta_json),
        )
        db.execute(
            "INSERT INTO chunks_fts (id,content,title,author) VALUES (?,?,?,?)",
            (cid, _tok_fts(chunk), tok_title, tok_author),
        )
        new_chunks.append((cid, chunk))

    db.commit()

    # 向量写入：直接用内存里的 chunks，不再回查 SQLite
    if new_chunks and not skip_vectors:
        upsert_vectors(
            chunk_ids    = [c[0] for c in new_chunks],
            texts        = [c[1] for c in new_chunks],
            source_types = [source_type] * len(new_chunks),
            titles       = [title] * len(new_chunks),
        )

    return len(new_chunks)


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


def _ingest_scanned_pdf_streaming(
    path,
    sid: str,
    file_hash: str,
    total_pages: int,
    title: str,
    author: str,
    source_type: str,
    pages_per_batch: int = 10,
    dpi: int = 100,
    ocr_batch_size: int = 50,
    skip_vectors: bool = True,
) -> int:
    """
    扫描版两步入库，彻底解耦 Vision 和 jieba：

    Step 1 — 分批起 OCR 子进程（每批 ocr_batch_size 页），每批处理完立即退出，
             Vision 内存在批次间完全释放；各批结果追加写入同一临时 JSONL 文件。
    Step 2 — 所有 OCR 子进程退出后，主进程读 JSONL，jieba 分词写 SQLite。

    内存峰值 = max(单批 Vision子进程 ~500MB, jieba写库 ~200MB)，两者永不共存。
    """
    import subprocess
    import sys
    import tempfile
    from pathlib import Path as _Path

    worker = _Path(__file__).parent.parent / "scripts" / "ocr_worker.py"
    if not worker.exists():
        raise FileNotFoundError(f"OCR worker 不存在: {worker}")

    # OCR 缓存文件按 file_hash 持久化，中途 kill 后可断点续传
    cache_dir = _Path(RAG_DB_PATH).parent / "ocr_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = str(cache_dir / f"{file_hash}.jsonl")

    # ── Step 1: 分批 OCR，追加写 JSONL（已有缓存则跳过）────────────────────────
    cached_pages: set[int] = set()
    if _Path(tmp_path).exists():
        with open(tmp_path, encoding="utf-8") as f:
            for line in f:
                try:
                    cached_pages.add(json.loads(line.strip())["page"])
                except Exception:
                    pass

    remaining_batches = [
        (b, min(b + ocr_batch_size, total_pages))
        for b in range(0, total_pages, ocr_batch_size)
        if not all(p in cached_pages for p in range(b, min(b + ocr_batch_size, total_pages)))
    ]

    if remaining_batches:
        print(f"[rag] Step1: OCR {total_pages} 页，每批 {ocr_batch_size} 页"
              + (f"（跳过已缓存 {len(cached_pages)} 页）" if cached_pages else ""))
        try:
            with open(tmp_path, "a", encoding="utf-8") as fout:
                for b_start, b_end in remaining_batches:
                    print(f"[rag] OCR 子进程 p{b_start+1}-{b_end} 启动...")
                    # stderr 写临时文件，避免管道缓冲满导致父子进程死锁
                    err_fd, err_path = tempfile.mkstemp(suffix=".err")
                    try:
                        with os.fdopen(err_fd, "w") as ferr:
                            proc = subprocess.Popen(
                                [sys.executable, str(worker), str(path),
                                 str(dpi), str(b_start), str(b_end)],
                                stdout=subprocess.PIPE,
                                stderr=ferr,
                                text=True,
                                bufsize=1,
                            )
                            for line in proc.stdout:
                                line = line.strip()
                                if not line:
                                    continue
                                try:
                                    obj = json.loads(line)
                                    pg  = obj.get("page", -1)
                                    if pg >= 0:
                                        if obj.get("text"):
                                            print(f"[rag] OCR p{pg+1} {len(obj['text'])}字")
                                        fout.write(line + "\n")
                                except json.JSONDecodeError:
                                    pass
                            proc.wait()
                        if proc.returncode != 0:
                            err_txt = open(err_path).read()
                            print(f"[rag] OCR 子进程异常(p{b_start+1}-{b_end}): {err_txt[:200]}")
                        else:
                            print(f"[rag] OCR 子进程 p{b_start+1}-{b_end} 完成退出")
                    finally:
                        _Path(err_path).unlink(missing_ok=True)
                    fout.flush()
        except Exception as e:
            print(f"[rag] OCR Step1 失败: {e}")
            return 0
    else:
        print(f"[rag] Step1: 全部 {total_pages} 页已有缓存，跳过 OCR")

    print("[rag] Step1 全部完成，Vision 内存已释放")

    # ── Step 2: 读 JSONL，jieba 分词写 SQLite ────────────────────────────────
    print("[rag] Step2: jieba 分词写库...")
    added       = 0
    batch_texts: list[str] = []
    batch_start = 0

    def _flush(b_end: int) -> None:
        nonlocal added, batch_start
        if not batch_texts:
            return
        batch_sid = f"{sid}:p{batch_start+1}-{b_end}"
        n = ingest_text(
            text="\n\n".join(batch_texts),
            source_id=batch_sid,
            source_type=source_type,
            title=title,
            author=author,
            extra_meta={
                "path":        str(path),
                "filename":    _Path(path).name,
                "file_hash":   file_hash,
                "total_pages": total_pages,
                "page_start":  batch_start + 1,
                "page_end":    b_end,
            },
            skip_vectors=skip_vectors,  # 向量留给 embed-all 单独跑，避免内存叠加
        )
        added += n
        print(f"[rag] 写库 p{batch_start+1}-{b_end}，累计 {added} 块")

    with open(tmp_path, encoding="utf-8") as fin:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            try:
                obj  = json.loads(line)
                pg   = obj.get("page", -1)
                text = obj.get("text", "").strip()
            except json.JSONDecodeError:
                continue
            if pg < 0:
                continue
            if text:
                batch_texts.append(text)
            if (pg - batch_start + 1) >= pages_per_batch:
                _flush(pg + 1)
                batch_texts = []
                batch_start = pg + 1
        _flush(total_pages)

    # OCR 缓存按 file_hash 持久化保留，不删除：
    # 换分块策略 / 换 embedding 模型时可直接复用，无需重跑 Vision OCR。
    print(f"[rag] OCR 缓存保留于 {tmp_path}")
    return added


def ingest_pdf(
    path: str,
    title: str = "",
    author: str = "",
    source_id: Optional[str] = None,
    pages_per_batch: int = 10,
    source_type: str = "pdf",
    use_ocr: bool = True,
    skip_vectors: bool = False,
) -> int:
    """
    解析 PDF 并入库。逐批（pages_per_batch 页）处理，peak 内存 = 单批文本大小。
    扫描版走流式 OCR（每批页数写完立即释放），文本版走 pypdf 批量提取。
    skip_vectors=True 时只写 SQLite/FTS，向量留给 embed-all 统一生成。
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ImportError("请先安装：pip install pypdf")

    from pathlib import Path as _Path
    p = _Path(path)
    if not p.exists():
        raise FileNotFoundError(path)

    # 分块计算 MD5，避免把整个大文件载入内存
    _md5 = hashlib.md5()
    with open(p, "rb") as _f:
        for _chunk in iter(lambda: _f.read(1 << 20), b""):
            _md5.update(_chunk)
    file_hash = _md5.hexdigest()[:16]
    sid = source_id or f"pdf:{file_hash}"

    db = _db()

    reader = PdfReader(str(p))
    total_pages = len(reader.pages)

    pdf_meta = reader.metadata or {}
    if not title:
        title = str(pdf_meta.get("/Title", p.stem)).strip() or p.stem
    if not author:
        author = str(pdf_meta.get("/Author", "")).strip()

    # 检测扫描版（前5页全部无文字）
    sample_texts = [(reader.pages[i].extract_text() or "").strip()
                    for i in range(min(5, total_pages))]
    is_scanned = use_ocr and not any(sample_texts)

    if is_scanned:
        # 扫描版交给流式 OCR：它自身可断点续传（OCR 缓存 + ingest_text 去重），
        # 故此处不按 already>0 早返回——否则中途中断过的书会永远补不全剩余页。
        print(f"[rag] 扫描版 PDF（{total_pages} 页），启动流式 OCR...")
        del reader  # 扫描版不再需要 reader，提前释放内存
        return _ingest_scanned_pdf_streaming(
            path=str(p), sid=sid, file_hash=file_hash,
            total_pages=total_pages, title=title, author=author,
            source_type=source_type, pages_per_batch=pages_per_batch,
            skip_vectors=skip_vectors,
        )

    # 文本版：已全部入库则快速跳过（文本提取本身无断点续传需求）
    already = db.execute(
        "SELECT COUNT(*) FROM chunks WHERE source_id LIKE ?", (sid + "%",)
    ).fetchone()[0]
    if already > 0:
        return 0

    # 文本版：pypdf 逐批提取
    added = 0
    for batch_start in range(0, total_pages, pages_per_batch):
        batch_end   = min(batch_start + pages_per_batch, total_pages)
        batch_texts = []
        for i in range(batch_start, batch_end):
            text = (reader.pages[i].extract_text() or "").strip()
            if text:
                batch_texts.append(text)

        batch_text  = "\n\n".join(batch_texts)
        batch_texts = None
        if not batch_text.strip():
            continue

        batch_sid = f"{sid}:p{batch_start+1}-{batch_end}"
        added += ingest_text(
            text=batch_text,
            source_id=batch_sid,
            source_type=source_type,
            title=title,
            author=author,
            extra_meta={
                "path":        str(p.resolve()),
                "filename":    p.name,
                "file_hash":   file_hash,
                "total_pages": total_pages,
                "page_start":  batch_start + 1,
                "page_end":    batch_end,
            },
            skip_vectors=skip_vectors,
        )

    return added


def ingest_pdf_dir(directory: str, recursive: bool = False) -> dict:
    """
    扫描目录下所有 PDF 并入库，返回 {"ok": n, "skipped": n, "failed": [path]} 统计。
    """
    from pathlib import Path as _Path
    d = _Path(directory)
    pattern = "**/*.pdf" if recursive else "*.pdf"
    pdfs = sorted(d.glob(pattern))
    ok, skipped, failed = 0, 0, []
    for pdf in pdfs:
        try:
            n = ingest_pdf(str(pdf))
            if n > 0:
                ok += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"[rag] PDF 入库失败 {pdf.name}: {e}")
            failed.append(str(pdf))
    return {"ok": ok, "skipped": skipped, "failed": failed}


# ── Voyage AI Embedding ───────────────────────────────────────────────────────

def _voyage_client():
    """返回 voyageai.Client 单例（线程安全，懒初始化）。"""
    if not hasattr(_local, "voyage"):
        api_key = os.getenv("VOYAGE_API_KEY", "")
        if not api_key:
            _local.voyage = None
            return None
        try:
            import voyageai
            _local.voyage = voyageai.Client(api_key=api_key)
        except ImportError:
            _local.voyage = None
    return _local.voyage


def embed_texts(texts: list[str]) -> list[list[float]] | None:
    """
    调用 Voyage AI 批量生成 embedding。texts 可任意长，内部自动分批。
    返回 None 表示 Voyage 不可用（VOYAGE_API_KEY 未设置或未安装 voyageai）。
    """
    client = _voyage_client()
    if client is None:
        return None

    all_embeddings = []
    for i in range(0, len(texts), VOYAGE_BATCH):
        batch = texts[i: i + VOYAGE_BATCH]
        try:
            result = client.embed(batch, model=VOYAGE_MODEL, input_type="document")
            all_embeddings.extend(result.embeddings)
        except Exception as e:
            print(f"[rag] Voyage embed 失败: {e}")
            return None
    return all_embeddings


def embed_query(query: str) -> list[float] | None:
    """对检索 query 生成 embedding（input_type='query'，与 document 略有区别）。"""
    client = _voyage_client()
    if client is None:
        return None
    try:
        result = client.embed([query], model=VOYAGE_MODEL, input_type="query")
        return result.embeddings[0]
    except Exception as e:
        print(f"[rag] Voyage query embed 失败: {e}")
        return None


# ── LanceDB 向量存储 ──────────────────────────────────────────────────────────

_lance_db_ref = None
_lance_lock   = threading.Lock()

def _lance_db():
    """返回 lancedb.LanceDBConnection 单例。"""
    global _lance_db_ref
    if _lance_db_ref is None:
        with _lance_lock:
            if _lance_db_ref is None:
                try:
                    import lancedb
                    Path(LANCE_DB_PATH).mkdir(parents=True, exist_ok=True)
                    _lance_db_ref = lancedb.connect(LANCE_DB_PATH)
                except ImportError:
                    _lance_db_ref = False  # 标记为不可用
    return _lance_db_ref if _lance_db_ref is not False else None


def _lance_table():
    """获取（或创建）vectors 表，返回 lancedb Table 或 None。"""
    db = _lance_db()
    if db is None:
        return None
    try:
        return db.open_table("vectors")
    except Exception:
        pass
    # 表不存在，先返回 None；写入时再按 schema 创建
    return None


def _ensure_lance_table(dim: int):
    """确保 LanceDB vectors 表存在（首次写入时按向量维度创建）。"""
    db = _lance_db()
    if db is None:
        return None
    try:
        return db.open_table("vectors")
    except Exception:
        pass
    try:
        import pyarrow as pa
        schema = pa.schema([
            pa.field("chunk_id",    pa.utf8()),
            pa.field("source_type", pa.utf8()),
            pa.field("title",       pa.utf8()),
            pa.field("embed_model", pa.utf8()),   # 换模型时靠这个字段区分
            pa.field("vector",      pa.list_(pa.float32(), dim)),
        ])
        return db.create_table("vectors", schema=schema)
    except Exception as e:
        print(f"[rag] LanceDB 建表失败: {e}")
        return None


def upsert_vectors(chunk_ids: list[str], texts: list[str],
                   source_types: list[str], titles: list[str]) -> int:
    """
    为给定 chunks 生成 embedding 并写入 LanceDB。
    VOYAGE_API_KEY 未设置时静默跳过，返回 0。
    """
    # 给每个 chunk 加书名前缀再 embed，让模型区分不同来源的相似内容
    contextualized = [
        f"书名：{ti}\n\n{tx}" if ti else tx
        for ti, tx in zip(titles, texts)
    ]
    embeddings = embed_texts(contextualized)
    if embeddings is None:
        return 0

    dim   = len(embeddings[0])
    table = _ensure_lance_table(dim)
    if table is None:
        return 0

    try:
        import pyarrow as pa
        rows = [
            {
                "chunk_id":    cid,
                "source_type": st,
                "title":       ti,
                "embed_model": VOYAGE_MODEL,
                "vector":      [float(v) for v in emb],
            }
            for cid, st, ti, emb in zip(chunk_ids, source_types, titles, embeddings)
        ]
        table.add(rows)
        return len(rows)
    except Exception as e:
        print(f"[rag] LanceDB upsert 失败: {e}")
        return 0


def retrieve_vector(query: str, top_k: int = TOP_K_VECTOR,
                    source_type: str | None = None) -> list[dict]:
    """
    向量检索：生成 query embedding，在 LanceDB 做 ANN 搜索，
    结果格式与 retrieve_bm25 一致（方便 RRF 合并）。
    """
    # query 侧不加书名前缀（用户问题是开放的），与 document 侧的 input_type 区分已足够
    qvec = embed_query(query)
    if qvec is None:
        return []

    table = _lance_table()
    if table is None:
        return []

    try:
        # embed_model 过滤保证换模型后旧向量不参与检索
        model_filter = f"embed_model = '{VOYAGE_MODEL}'"
        where = f"{model_filter} AND source_type = '{source_type}'" if source_type else model_filter
        q = table.search(qvec).where(where, prefilter=True).limit(top_k)
        rows = q.to_list()
    except Exception as e:
        print(f"[rag] LanceDB 检索失败: {e}")
        return []

    # 补全 content 和 meta（向量表只存了 chunk_id，从 SQLite 拿全量）
    if not rows:
        return []
    ids = [r["chunk_id"] for r in rows]
    db  = _db()
    placeholders = ",".join("?" * len(ids))
    sql_rows = db.execute(
        f"SELECT id,source_id,source_type,title,author,content,extra_meta "
        f"FROM chunks WHERE id IN ({placeholders})",
        ids,
    ).fetchall()
    id_to_row = {r[0]: r for r in sql_rows}

    results = []
    for r in rows:
        cid  = r["chunk_id"]
        dist = r.get("_distance", 0.0)
        sql  = id_to_row.get(cid)
        if sql is None:
            continue
        results.append({
            "id": cid,
            "source_id":   sql[1],
            "source_type": sql[2],
            "title":       sql[3],
            "author":      sql[4],
            "content":     sql[5],
            "meta": {
                "source_id":   sql[1],
                "source_type": sql[2],
                "title":       sql[3],
                "author":      sql[4],
                **(json.loads(sql[6]) if sql[6] else {}),
            },
            "score": 1.0 / (1.0 + dist),  # 距离转相似度
        })
    return results


# ── BM25 检索 ─────────────────────────────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    """jieba 分词，返回 token 列表（用于 BM25）。"""
    try:
        import jieba
        tokens = list(jieba.cut(text))
    except ImportError:
        tokens = list(text)
    return [t.strip() for t in tokens if t.strip()]


def _tok_fts(text: str) -> str:
    """将文本转为空格分隔的 jieba token 串，写入 FTS5 使其做词级别匹配。"""
    tokens = _tokenize(text)
    # 长度 ≥2 的词保留；单字也保留（用于数字/英文字母）
    return " ".join(tokens) if tokens else text


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
    # FTS5 表里存的是 jieba 分词串，查询时也要分词后再做 OR 匹配
    tokens = _tokenize(query)
    if tokens:
        safe_q = " OR ".join(re.sub(r'["\'\*\-\+\(\)]', '', t) for t in tokens if t)
    else:
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


def _rrf_merge(lists: list[list[dict]], k: int = 60) -> list[dict]:
    """
    Reciprocal Rank Fusion：将多个排名列表合并为一个。
    k=60 是标准默认值，数值越大对低排名文档越宽容。
    """
    scores: dict[str, float] = {}
    items:  dict[str, dict]  = {}
    for ranked in lists:
        for rank, hit in enumerate(ranked):
            cid = hit["id"]
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
            if cid not in items:
                items[cid] = hit
    merged = sorted(items.values(), key=lambda h: scores[h["id"]], reverse=True)
    for h in merged:
        h["score"] = round(scores[h["id"]], 6)
    return merged


def retrieve(query: str, top_k: int = TOP_K,
             source_type: Optional[str] = None) -> list[dict]:
    """
    主检索入口：向量检索 + BM25 + FTS5，RRF 融合后取 top_k。
    VOYAGE_API_KEY 未设时自动降级为纯 BM25 + FTS5。
    """
    bm25_hits   = retrieve_bm25(query, top_k=TOP_K_BM25, source_type=source_type)
    fts_hits    = retrieve_fts(query, top_k=TOP_K_BM25 // 2, source_type=source_type)
    vector_hits = retrieve_vector(query, top_k=TOP_K_VECTOR, source_type=source_type)

    candidates = _rrf_merge([vector_hits, bm25_hits, fts_hits])
    return candidates[:top_k * 3] if top_k * 3 < len(candidates) else candidates


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
        {
            "title":       h["meta"].get("title", ""),
            "score":       h["score"],
            "source_type": h["meta"].get("source_type", ""),
            "url":         h["meta"].get("url", ""),
            "page_start":  h["meta"].get("page_start"),
            "page_end":    h["meta"].get("page_end"),
        }
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

def _get_arg(argv: list[str], flag: str, default: str = "") -> str:
    try:
        return argv[argv.index(flag) + 1]
    except (ValueError, IndexError):
        return default


if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"

    if cmd == "stats":
        print(json.dumps(collection_stats(), ensure_ascii=False, indent=2))

    elif cmd == "ingest" and len(sys.argv) >= 3:
        path  = Path(sys.argv[2])
        title = _get_arg(sys.argv, "--title", path.stem)
        author = _get_arg(sys.argv, "--author", "")
        if path.suffix.lower() == ".pdf":
            n = ingest_pdf(str(path), title=title, author=author)
        else:
            stype = _get_arg(sys.argv, "--type", "other")
            n = ingest_text(path.read_text(encoding="utf-8"), source_id=str(path),
                            source_type=stype, title=title, author=author)
        print(f"入库完成：{n} 块")

    elif cmd == "ingest-dir" and len(sys.argv) >= 3:
        # python -m x_agent.rag ingest-dir ./books [--recursive]
        recursive = "--recursive" in sys.argv
        result = ingest_pdf_dir(sys.argv[2], recursive=recursive)
        print(f"入库完成：{result['ok']} 本 PDF，跳过 {result['skipped']} 本")
        if result["failed"]:
            print(f"失败 {len(result['failed'])} 本：{result['failed']}")

    elif cmd == "embed-all":
        stype = _get_arg(sys.argv, "--type", None)  # type: ignore[arg-type]
        db = _db()

        # 只读 chunk_id + embed_model 两列，不拉 vector 列（避免 400MB+ 内存）
        existing_ids: set[str] = set()
        table = _lance_table()
        if table is not None:
            try:
                import pyarrow.compute as pc
                ds  = table.to_lance()
                arr = ds.to_table(
                    columns=["chunk_id", "embed_model"],
                    filter=pc.field("embed_model") == VOYAGE_MODEL,
                )
                existing_ids = set(arr.column("chunk_id").to_pylist())
                # 统计旧模型向量（不过滤就全读 chunk_id+embed_model，两列都是字符串，开销很小）
                all_arr = ds.to_table(columns=["chunk_id", "embed_model"])
                old_count = sum(1 for v in all_arr.column("embed_model").to_pylist()
                                if v != VOYAGE_MODEL)
                if old_count:
                    print(f"[embed-all] 检测到 {old_count} 条旧模型向量，将补跑")
            except Exception as e:
                print(f"[embed-all] 读取向量表失败: {e}")

        # 游标流式扫 SQLite，不 fetchall 全量（内存恒定 = 一批 content）
        sql  = ("SELECT id,content,source_type,title FROM chunks WHERE source_type=?"
                if stype else "SELECT id,content,source_type,title FROM chunks")
        args = (stype,) if stype else ()
        cur  = db.execute(sql, args)

        written = scanned = skipped = 0
        while True:
            rows = cur.fetchmany(VOYAGE_BATCH)
            if not rows:
                break
            scanned += len(rows)
            batch = [r for r in rows if r[0] not in existing_ids]
            skipped += len(rows) - len(batch)
            if not batch:
                continue
            n = upsert_vectors(
                chunk_ids    = [r[0] for r in batch],
                texts        = [r[1] for r in batch],
                source_types = [r[2] for r in batch],
                titles       = [r[3] for r in batch],
            )
            written += n
            print(f"  embedded {written} 块（扫描 {scanned}，跳过已有 {skipped}）")

        print(f"embed-all 完成：{written} 块")

    elif cmd == "query" and len(sys.argv) >= 3:
        q = " ".join(a for a in sys.argv[2:] if not a.startswith("--"))
        stype = _get_arg(sys.argv, "--type", None)   # type: ignore[arg-type]
        for h in retrieve(q, source_type=stype or None):
            print(f"\n[score={h['score']:.3f}][{h['meta'].get('source_type','?')}] "
                  f"{h['meta'].get('title','?')}")
            print(h["content"][:200])

    else:
        print(
            "用法:\n"
            "  python -m x_agent.rag stats\n"
            "  python -m x_agent.rag ingest <file.txt|file.pdf> [--title X] [--author X] [--type X]\n"
            "  python -m x_agent.rag ingest-dir <dir> [--recursive]\n"
            "  python -m x_agent.rag embed-all [--type pdf|book|...]\n"
            "  python -m x_agent.rag query <问题> [--type pdf|book|article|report]\n"
        )
