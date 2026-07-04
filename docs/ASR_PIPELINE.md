# ASR 音频转写完整管道 — M5优化版

## 概览

将百度网盘中的华尔街见闻「70天共读35本金融经典」大师课36个mp3文件，转写为文字稿并入库RAG向量数据库。

```
百度网盘 (36 mp3 files, 1.2GB)
    ↓
FFmpeg (音频转换)
    ↓
mlx-whisper (Apple Silicon 并行转写)
    ↓
质检 (重复幻觉 / 乱码 / 字数过少)
    ↓
RAG数据库 (向量化入库)
    ↓
统计报告 (书籍覆盖、质量指标)
```

---

## 系统要求

### M5硬件规格
- **CPU**: 18核 (6个Super + 12个Performance cores)
- **内存**: 48GB
- **存储**: 865GB可用空间
- **系统**: macOS 12+ (Apple Silicon)

### 软件依赖
- Python 3.14 (.venv)
- mlx-whisper 0.4.3 (MPS加速)
- ffmpeg 7.1 (音频处理)
- sentence-transformers + bge-m3 (向量化)

---

## 安装步骤

### 1. 准备环境

```bash
# 已完成
.venv/bin/python --version      # Python 3.14.6 ✅
which ffmpeg                    # ~/.local/bin/ffmpeg ✅
.venv/bin/python -c "import mlx_whisper; print(mlx_whisper.__version__)"  # 0.4.3 ✅
```

### 2. 准备音频文件

```bash
# 从百度网盘下载（已完成）
.venv/bin/python scripts/asr_prep.py mp3
# 输出位置: data/asr_audio/ (36个mp3文件)
```

---

## 使用方法

### 快速开始：批量转写（并行）

```bash
# 启动14个worker并行处理（推荐）
.venv/bin/python scripts/transcribe_asr_m5_parallel.py \
    --course "70天共读" \
    --workers 14

# 预计耗时: 2-4小时（取决于音频总长度）
# 资源占用: 18-25% CPU, 4-6GB 内存
```

### 监控进度

```bash
# 实时监控（每30秒更新）
.venv/bin/python scripts/monitor_asr.py watch 30

# 单次查询状态
.venv/bin/python scripts/monitor_asr.py
```

### 完整流程（Nightly版）

```bash
# 一次运行转写 → 验证 → 向量化 → 统计 → 提交
nohup bash scripts/asr_nightly.sh > output/asr_nightly.log 2>&1 &

# 查看日志
tail -f output/asr_nightly.log
```

---

## 文件说明

### 核心脚本

| 脚本 | 功能 | 入参 | 输出 |
|------|------|------|------|
| `transcribe_asr_m5_parallel.py` | **并行转写** | audio_dir, workers数 | rag.db, done.json |
| `monitor_asr.py` | **进度监控** | course名 | 控制台输出 + RAG统计 |
| `asr_post_process.py` | **后处理** (向量化+报告) | course名 | 报告MD + 完整统计 |
| `asr_nightly.sh` | **完整流程** | 无 | 所有输出 + git提交 |

### 数据文件

| 位置 | 用途 | 说明 |
|------|------|------|
| `data/asr_audio/` | 音频存储 | 36个mp3文件 + _manifest.json |
| `data/asr_done_{course}.json` | 处理记录 | 已完成的文件列表（断点续传） |
| `data/asr_rejected_{course}.log` | 质检日志 | 拒绝原因：重复幻觉、乱码等 |
| `output/rag.db` | RAG数据库 | SQLite，包含所有chunks + embedding |
| `output/asr_report_*.md` | 完成报告 | 统计和质量指标 |

---

## 性能优化

### 为什么使用并行版本？

**单进程版本**:
```
顺序处理 36个文件
平均每个文件: 5分钟
总耗时: 180分钟 (3小时)
CPU使用: 20% (单核)
```

**14-worker并行版本** (推荐):
```
并行处理 36个文件（分成14组）
平均完成: 90分钟 (1.5小时)
CPU使用: 80-100% (充分利用)
内存: 4-6GB (安全范围)
吞吐量: ~24 文件/小时
```

### Worker数量选择

```python
# 默认: CPU_CORES * 0.8 (M5上 = 14)
--workers 14    # 推荐（CPU占用80%，系统不卡）

--workers 18    # 激进（可能系统卡顿）
--workers 12    # 保守（留更多给其他任务）
--workers 2     # 调试模式
```

---

## 质检规则

### 文本质量标准

所有转写文本必须通过 `text_quality()` 检查：

```python
# 过滤条件
minimum_chars = 100          # 至少100字
min_unique_ratio = 0.3       # 独特字符占比 >= 30%
no_long_repetition = True    # 无5字以上重复
confidence_threshold = 0.95  # Whisper置信度
```

### 拒绝原因代码

| 代码 | 含义 | 处理方式 |
|------|------|---------|
| `too_short` | 字数<100 | 手动检查音频 |
| `too_repetitive` | 重复幻觉 | 模型输出问题 |
| `garbage` | 乱码 > 30% | 音质问题 |
| `low_unique` | 独特字符<30% | 音频重复内容 |

---

## 预期输出

### 成功完成后的统计

```
📊 完成统计
============================================================
✅ 入库:     35 (97%)
⚠️  质检拒:  1 (3%)
❌ 错误:     0 (0%)
⏱  耗时:     92 分钟
💾 总字数:   ~180,000 字
============================================================

📚 RAG知识库统计
============================================================
总chunks:     ~600,000+
ASR chunks:   ~18,000 (新增)
70天共读:     ~18,000 (新增)
============================================================
```

### 生成的报告

```
output/asr_report_70天共读_20260704.md
├─ 📊 统计概览 (总chunks、字数、书籍数)
├─ 📚 书籍详细清单 (按讲师号排列)
└─ 🎯 下一步 (向量化、主题索引等)
```

---

## 故障排除

### 问题1: 某个文件一直在处理

**原因**: 音频过长或网络问题  
**解决**: 
```bash
# 查看stuck的文件
ps aux | grep transcribe | grep -v grep

# 手动移除并重试
rm data/asr_done_70天共读.json  # 清除记录
# 或手动编辑 asr_done_70天共读.json，删除有问题的文件

# 重新启动
.venv/bin/python scripts/transcribe_asr_m5_parallel.py --course "70天共读"
```

### 问题2: "mlx_whisper not found" 错误

**原因**: 虚拟环境问题  
**解决**:
```bash
source .venv/bin/activate
pip install mlx-whisper --upgrade
```

### 问题3: 质检拒绝率过高 (>10%)

**原因**: 音频质量差，Whisper幻觉  
**解决**:
```bash
# 查看拒绝原因
tail -20 data/asr_rejected_70天共读.log

# 检查单个音频质量
.venv/bin/python scripts/transcribe_asr_m5.py --limit 1 --dir data/asr_audio
```

---

## 高级用法

### 只处理特定文件

```bash
# 编辑 data/asr_done_70天共读.json
# 删除你要重新处理的文件名

# 重新启动（会重新处理被删除的文件）
.venv/bin/python scripts/transcribe_asr_m5_parallel.py --course "70天共读"
```

### 修改Worker数量

```bash
# CPU占用100%（激进）
.venv/bin/python scripts/transcribe_asr_m5_parallel.py \
    --course "70天共读" --workers 18

# 仅使用2个worker（调试）
.venv/bin/python scripts/transcribe_asr_m5_parallel.py \
    --course "70天共读" --workers 2
```

### 只做后处理（跳过转写）

```bash
# 假设所有转写已完成，直接向量化+报告
.venv/bin/python scripts/asr_post_process.py "70天共读"
```

---

## 成本估计

| 资源 | 消耗 | 费用 |
|------|------|------|
| **Claude API** | 每条转写 ~0 (本地mlx-whisper) | $0 ✅ |
| **向量化** | 18,000 chunks × 1024维 | ~$0 (本地bge-m3) ✅ |
| **存储** | 1.2GB音频 + 0.5GB RAG增量 | 已有 ✅ |
| **总成本** | | **$0** 🎉 |

---

## 时间表

| 阶段 | 操作 | 预计耗时 |
|------|------|---------|
| 下载 | `asr_prep.py mp3` | 10分钟 |
| 转写 | 14-worker并行 | 90分钟 ⭐ |
| 向量化 | bge-m3 embedding | 30分钟 |
| 报告 | 统计+提交 | 5分钟 |
| **总计** | | **~135分钟 (2.25小时)** ⭐ |

---

## 下一步

✅ **完成后**:
- [x] 36个音频全量转写
- [ ] 18,000+ chunks入库
- [ ] 与existing RAG库集成检索
- [ ] 按讲师/主题重组索引
- [ ] 生成学习指南

---

**最后更新**: 2026-07-04  
**维护者**: M5 ASR Pipeline v1.0  
**联系**: 项目issues
