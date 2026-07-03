# M5 启动清单（Mac M5 到货后按序执行）

Mac M5（Apple Silicon）约 2026-07-04 到。本项目大量能力被 M5 卡着——本清单按**杠杆从高到低**排好执行顺序，M5 一到照做即可，不用现想。全部用 `.venv/bin/python`（3.14），别用 anaconda。

## P0 · bge 全量向量化（第一优先，一锤解两洞）
```bash
export EMBED_BACKEND=bge          # bge-m3 本地，免费、不需要任何 API key
.venv/bin/python -m x_agent.rag embed-all   # 确认实际子命令名，rag.py 有 embed 接口
```
- **为什么第一**：rag.db 46 万+ 分块目前**从没向量化**（`output/rag_vectors` 不存在，纯 BM25+FTS）。其中约 13 万英文（海外大牛7.3万含Tooze + 英文书5.6万）**中文查询召不动**。bge-m3 跨语言，一次解决：①跨语言检索 ②让核心检索在无 key 下也能跑。
- 完成后验证：中文查"美债流动性/债务周期"能否召回 Marks/Tooze/达摩达兰的英文段落。
- 内存峰值注意（46 万分块），必要时分批。

## P1 · ASR 音频转写管道
- 启用 `scripts/asr_prep.py` / `scripts/transcribe_asr_m5.py`（whisper 类）。
- **解锁**：多平台视频账号发现 loop（XHS/B站/抖音，见 worklog）——三平台 finance 科普精华都在视频里。**B站有CC字幕可先绕过 ASR**，是三者里能最先动的。
- 见闻音频、播客类入库也靠它。
- 入库前过 `rag.text_quality()`（拦 whisper 重复幻觉）。

## P2 · 图表/图片 OCR
- 公众号/知识星球/研报里的图表（K线、数据表）OCR 入库或喂 opus 读。
- 也是 entity_resolver 扫 rag.db 提及、persona 数据接地补分行业数字的一环。

## P3 · entity_resolver 扫 rag.db（配合 P0）
- 实体主表当前只扫了 tweets/signals；**rag.db 46 万分块的研报/公众号标的提及是覆盖率金矿**（entity_resolver 的 #1 TODO）。P0 向量化后一并做，把"按标的透视"补全。

## P4 · 主数据补齐 & 宏观 parquet 重刷
- securities 主表美股(1.3万)/港股(2788)的 name/aliases 为空——补齐。
- data/macro_history 的 PPI/CPI/工业利润 parquet 停在 2025-09——用 akshare **新鲜序列**（`macro_china_ppi` 非 `_yearly`）重刷。

## P5 · persona 全量 + 政策事件全量抽取
- 有 key/本地 LLM 后，persona 画像从干净单模型（claude-sonnet-5 固定温度）重建作正式基线；政策事件库方法D 的全量预测抽取（现只 pilot）。

## 无关 M5、可随时做（不阻塞）
- Aladdin 批次二危机重放（进行中）、运维自动化定时跑批（进行中）、批次三剩余。

> 相关：Obsidian [[数据库与数据源表设计]] [[Persona 逻辑提取方法]]；记忆 worklog-content-sources / akshare-stale-series-trap。
