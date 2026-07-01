# M5 Pro 批处理任务（Apple Silicon 上跑的重活）

x86 上慢/不可行、留到 M5 Pro(48G/1T, MLX/MPS 加速)批量做的三件事。
前置：把仓库和 `output/rag.db`（含 24 万+ 已入库 chunk）、`data/` 拷到 M5。

## 1. 本地 Embedding（bge-m3，免费，不外泄）

私有付费内容（研报/知识星球/公众号）走本地 bge-m3，不发第三方。

```bash
# 装依赖（Apple Silicon 会自动用 MPS GPU）
.venv/bin/pip install sentence-transformers torch

# 切 bge 后端，给所有 skip_vectors=True 的 chunk 批量生成向量
export EMBED_BACKEND=bge          # 关键：不设则默认 voyage(付费)
.venv/bin/python -m x_agent.rag embed-all           # 全量
.venv/bin/python -m x_agent.rag embed-all --type wechat   # 只embed某来源
```

- 模型 `BAAI/bge-m3`（1024维，中文/多语检索标杆）首次自动下载 ~2GB。
- 断点续传：LanceDB 已有 `embed_model=BAAI/bge-m3` 的 chunk 会跳过，中断可重跑。
- 检索端同样要 `export EMBED_BACKEND=bge`，否则 query 用 voyage 编码、和 bge 向量对不上
  （dashboard/main 跑之前设好该环境变量即可）。
- 维度：voyage-finance-2 与 bge-m3 都是 1024 维，`embed_model` 字段区分，混存也不串。
  但若向量表曾用其它维度模型建过，需先删 `output/rag_vectors` 重建。

## 2. ASR 音频转写（mlx-whisper，仅 Apple Silicon）

见 `scripts/transcribe_asr_m5.py`。音频源：
- 70天共读35本金融经典（36 个 mp3，`scripts/asr_prep.py` 已下到 data/asr_audio/）
- 见闻大师课 154GB 音视频（网盘，按需拉）
- 小宇宙播客（后续接入，先下 mp3+shownotes）

```bash
.venv/bin/pip install mlx-whisper
.venv/bin/python scripts/transcribe_asr_m5.py --limit 1   # 必做:先验1个,肉眼看中文+过text_quality
.venv/bin/python scripts/transcribe_asr_m5.py             # 通过后全量
```

**铁律**：转写先过 `rag.text_quality`，重复幻觉/乱码只留日志不入库；验1个像样了再 fan out。

## 3. Barron 146MB 大刊 OCR（可选）

x86 上 OCR 跑了 3h 未完已跳过（记于 `data/mag_failed_Barron_s.log`）。M5 上可重试：
```bash
# 从 done 集合移除该 fs_id 后重跑 ingest_bilingual_mag.py，或单独 OCR
```

## 建议顺序
先 ①embedding（一次性，让 RAG 向量检索上线）→ ②ASR 验1个再全量 → ③Barron 可选。
①②都断点续传，可穿插。跑完 embedding 后 RAG 从"纯BM25"升级为"BM25+向量 RRF 混检"。
