# 迁移到 M5（SSH + rsync）—— 2026-07-04

把整个项目从旧机（yues-MacBook-Pro，192.168.11.91，user=pany19，Python 3.14.5）
迁到 M5。代码已 push 到 GitHub（origin main，最新 921a47d），但**大资产全被 gitignore**，
必须靠 rsync 单独搬：`output/`（rag.db 1.7G + x_agent.db 396M）、`data/`（断点/feeds/
待ASR音频 595M）、`.env`（密钥）。

## 带 / 不带 清单

| 项 | 体积 | 处理 |
|---|---|---|
| `output/`（rag.db, x_agent.db, ocr_cache, personas, factors, policy, baidu_token…） | 2.1G | **带**（皇冠资产） |
| `data/`（done-list, feeds, gurus, **asr_audio 待ASR**） | 0.65G | **带**（不含行情/临时） |
| `.env` | 1.2K | **带**（密钥） |
| 代码 + config.yaml + CLAUDE.md + `.git` | 8M | rsync 整目录会带；或 M5 上 git clone |
| `data/stock_history` 行情缓存 | 2.0G | **不带**，M5 首次跑 finance_fetcher 自动重拉 |
| `models/` whisper 权重 | 1.5G | 可选：带则 ASR 免下载；不带 M5 重下 |
| `.venv` | 1.9G | **不带**，M5 上 `pip install -r requirements.txt` 重建 |
| `bin/` 静态 ffmpeg | 77M | **不带**（x86），M5 装 arm 版 `brew install ffmpeg` |
| `data/_mag_tmp.pdf` | 146M | **不带**（临时） |

精简版合计 **≈ 2.8G**。

## 第 1 步：M5 上开 SSH（Remote Login）
系统设置 → 通用 → 共享 → 打开「远程登录」。记下 M5 的用户名和 IP（`ipconfig getifaddr en0`）。

## 第 2 步：旧机 push 到 M5（在旧机跑）
把 `M5USER`/`M5IP`/目标路径替换掉。**精简版**（不带行情/模型）：

```bash
cd ~/Documents/x_agent_proj
rsync -a --partial --info=progress2 \
  --exclude='.venv' --exclude='bin' --exclude='models' \
  --exclude='data/stock_history' \
  --exclude='data/_mag_tmp.pdf' --exclude='data/_netdisk_tmp.pdf' \
  --exclude='__pycache__' --exclude='.DS_Store' \
  ./ M5USER@M5IP:~/Documents/x_agent_proj/
```

说明：
- 局域网**不加 `-z`**（rag.db/mp3/pdf 压不动，压缩反而拖 CPU）；跨慢速外网才加。
- `--partial` 断了重跑接着传，不重来。
- 千兆网线 ~4-6 分，WiFi ~8-12 分。
- 想**连行情/模型一起带**：删掉 `--exclude='data/stock_history'` 和 `--exclude='models'`（+3.5G，多约 10-20 分）。

## 第 3 步：M5 上重建环境
```bash
cd ~/Documents/x_agent_proj
python3 --version                      # 对齐 3.14.x（差太多重建 venv）
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
brew install ffmpeg                    # arm 原生，替代旧机 bin/ 里的 x86 版
set -a; source .env; set +a            # 载入密钥
.venv/bin/python -c "import x_agent.rag as r; print('rag ok')"  # 冒烟测试
```

## 第 4 步：M5 头号杠杆——bge-m3 向量化（见 m5_boot_checklist.md）
rag.db 已 46万+分块但**从未建过向量层**，检索纯 BM25。M5 有 GPU：
```bash
export EMBED_BACKEND=bge               # bge-m3 跨语言、无需任何 API key
.venv/bin/python -m x_agent.rag embed-all   # 首次全量嵌入（跨语言检索一次补齐）
```
补完这批新入的书库(23本)+Tooze(505篇)也会一并向量化。

## 收尾：待办延续（见 memory worklog-content-sources）
- nxny 钟正生重跑（旧机限流，M5 换 IP 可跑）：
  `.venv/bin/python scripts/ingest_chief_web.py --sources nxny --authors 钟正生`
- ASR 管道、公众号/星球 OCR、见闻音频——M5 GPU 就位后启动。
