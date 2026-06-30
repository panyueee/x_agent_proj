# RAG 书籍知识库 — 三层架构说明

> 更新日期：2026-06-30  
> 代码：`x_agent/rag.py`（核心）、`scripts/batch_ingest_books.py`（批量入库）、`scripts/ocr_worker.py`（OCR 子进程）

本知识库把「PDF → 可检索问答」拆成**三个相互独立、可分别重跑**的层。
每一层都有明确的产物和缓存，改动其中一层不必重做其它层（详见末尾「如何修改 X」）。

---

## 一、整体数据流

```
                         ┌──────────────────────────────────────────────┐
                         │  输入：books/*.pdf                              │
                         │   ├─ 文本版 PDF（有文本层，pypdf 可直接抽取）    │
                         │   └─ 扫描版 PDF（图片，需 OCR）                  │
                         └───────────────────┬──────────────────────────┘
                                             │
              ┌──────────────────────────────┴───────────────────────────┐
              │  扫描版                                       文本版        │
              ▼                                                            │
┌──────────────────────────────────────┐                                 │
│  ① OCR 缓存层（仅扫描版）             │                                 │
│  scripts/ocr_worker.py 子进程         │                                 │
│  macOS Vision 逐页识别                 │                                 │
│  → output/ocr_cache/<file_hash>.jsonl │  （每页一行 JSON，永久保留）     │
└───────────────────┬──────────────────┘                                 │
                    │  读缓存文本                          pypdf 抽取文本   │
                    ▼                                            ▼         │
┌──────────────────────────────────────────────────────────────────────┐ │
│  ② 切块 / 索引层（SQLite）                                              │ │
│  split_text(chunk_size=500, overlap=50, 分隔符感知) + jieba 分词       │ │
│  → output/rag.db                                                       │ │
│      • chunks      （正文 + 元数据 extra_meta）                         │ │
│      • chunks_fts  （FTS5 全文索引，unicode61 分词）                     │ │
│  入库时 skip_vectors=True —— 本层从不调用向量 API                       │ │
└───────────────────┬────────────────────────────────────────────────────┘ │
                    │  读 chunks                                              
                    ▼                                                        
┌──────────────────────────────────────────────────────────────────────┐  
│  ③ 向量层（Voyage + LanceDB）—— 独立步骤 `embed-all`                     │  
│  Voyage AI 嵌入（模型 voyage-finance-2）                                │  
│  → output/rag_vectors（LanceDB）                                        │  
│  幂等：已用当前模型嵌过的 chunk 自动跳过；可换模型而不动 ①②             │  
│  需要 VOYAGE_API_KEY；未设置则本层休眠，向量为空                         │  
└────────────────────────────────────────────────────────────────────────┘  

                    检索 retrieve()：向量 + BM25 + FTS5 → RRF 融合
                              → 可选 Claude Haiku 重排
                                    → ask() 用 Claude 生成回答
```

---

## 二、关键路径

| 用途 | 路径 | 说明 |
|------|------|------|
| 核心代码 | `x_agent/rag.py` | 入库 / 检索 / 嵌入 / 问答 全部逻辑 |
| 批量入库脚本 | `scripts/batch_ingest_books.py` | 按 `BOOK_CATALOG` 优先级扫描 `books/` 并入库 |
| OCR 子进程 | `scripts/ocr_worker.py` | macOS Vision 逐页 OCR，被 rag.py 以子进程调用 |
| 待入库 PDF | `books/` | 源文件目录 |
| ① OCR 缓存 | `output/ocr_cache/<file_hash>.jsonl` | 每页一行 JSON，**永久保留**供复用 |
| ② SQLite 库 | `output/rag.db` | 表 `chunks` + FTS5 表 `chunks_fts`（可由 `RAG_DB_PATH` 覆盖） |
| ③ 向量库 | `output/rag_vectors` | LanceDB（可由 `LANCE_DB_PATH` 覆盖） |

切块参数（`x_agent/rag.py` 常量）：`CHUNK_SIZE=500`、`CHUNK_OVERLAP=50`。
嵌入模型：`VOYAGE_MODEL="voyage-finance-2"`，单批 `VOYAGE_BATCH=128`。

---

## 三、各层命令

### 入库（① + ②，写 SQLite/FTS，不调用向量 API）

```bash
# 批量：按 BOOK_CATALOG 优先级入库 books/ 下命中的文件
python scripts/batch_ingest_books.py            # 正式入库
python scripts/batch_ingest_books.py --dry-run  # 只列出匹配/缺失，不入库

# 单文件（PDF 或 txt）。扫描版 PDF 会自动触发 ① OCR 缓存层
python -m x_agent.rag ingest <file.pdf> [--title X] [--author X] [--type book]

# 整个目录
python -m x_agent.rag ingest-dir <dir> [--recursive]
```

`--type`（source_type）常用取值：`book` / `pdf` / `article` / `report` / `other`。

### 向量层（③，独立步骤，需 `VOYAGE_API_KEY`）

```bash
# 读 SQLite 里的 chunks 统一嵌入到 LanceDB；幂等，已嵌过的跳过
python -m x_agent.rag embed-all [--type book]
```

### 查询 / 检索

```bash
python -m x_agent.rag query "什么是安全边际" [--type book]
```

### 库状态

```bash
python -m x_agent.rag stats
# 输出 total_chunks / by_type / book_count
```

---

## 四、环境变量与优雅降级

| 变量 | 用途 | 缺失时的行为 |
|------|------|-------------|
| `VOYAGE_API_KEY` | 向量层（③）嵌入与向量检索 | 向量层休眠：`embed-all` 静默跳过、向量检索返回空，`retrieve()` **自动降级为纯 BM25 + FTS5**，问答仍可用 |
| `ANTHROPIC_API_KEY` | Claude Haiku 重排 + 回答生成 | 重排/`ask` 生成不可用（检索本身不依赖它） |

> 当前 `VOYAGE_API_KEY` **未设置**，因此向量层处于休眠状态，向量库为空，
> 检索走 BM25 + FTS5 双路融合。设置该变量并运行 `embed-all` 后即可启用三路检索，
> 无需重新 OCR 或重新切块。

---

## 五、如何修改 X（要重跑哪一层）

- **更换 / 修复 OCR（识别效果不好、扫描件有更新）**  
  删除对应 `output/ocr_cache/<file_hash>.jsonl`，重新 `ingest` 该 PDF；
  这会重跑 ①（OCR）和 ②（切块），③ 需另跑 `embed-all`。
  只要 OCR 缓存还在，重跑入库不会再次调用 Vision。

- **修改切块策略（`CHUNK_SIZE` / `CHUNK_OVERLAP` / 分隔逻辑）**  
  只需重跑 ②：清掉 `output/rag.db`（或对应 chunks）后重新 `ingest`。
  扫描版会直接复用 ① 的 `ocr_cache`，**不必重新 OCR**。改完后再 `embed-all` 重建向量。

- **更换嵌入模型（改 `VOYAGE_MODEL`）**  
  只需重跑 ③：`python -m x_agent.rag embed-all`。它按 `embed_model` 字段判断幂等，
  会自动为旧模型的 chunk 补跑新模型向量，**完全不动 ① OCR 缓存和 ② SQLite/FTS**。

- **新增书籍**  
  放入 `books/`（命中 `BOOK_CATALOG` 则会被 `batch_ingest_books.py` 自动识别），
  入库后跑一次 `embed-all` 即可。
