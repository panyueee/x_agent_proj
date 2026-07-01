# WeWe RSS 部署 + 微信公众号入 RAG 指南

把微信公众号文章接入本项目 RAG，链路分两段：

```
微信读书(只读token) ──▶ WeWe RSS(自建,输出全文RSS) ──▶ scripts/ingest_wechat_rss.py ──▶ RAG
        [你部署+扫码]              [Docker 一条命令]              [已写好,消费侧]
```

本项目只负责**消费侧**（读 RSS → 解析 → 质检 → 入库）。**上游 WeWe RSS 需要你自己部署并用微信读书扫码登录**，否则脚本没有真实 feed 可拉。

> ⚠️ `scripts/ingest_wechat_rss.py` 目前仅通过内置样例完成解析验证（`--selftest` 通过），
> **尚未对真实 WeWe RSS feed 实测**。完成下面步骤拿到真实 RSS URL 后，请先 `--dry-run`
> 核对解析结果，再正式入库。

---

## 1. 部署 WeWe RSS（Docker）

前置：装好 Docker 和 Docker Compose。

```bash
cd /Users/pany19/Documents/x_agent_proj
docker compose -f docker-compose.wewe-rss.yml up -d
docker compose -f docker-compose.wewe-rss.yml logs -f   # 看启动日志，Ctrl-C 退出日志
```

- 服务监听 `http://localhost:4000`。
- 数据（SQLite + 微信读书登录态）持久化在 `./wewe-rss-data/`（已随卷挂载，容器重建不丢）。
- **上线前改掉** compose 里的 `AUTH_CODE: "change_me_please"` 为你自己的强口令。

## 2. 登录管理页

浏览器打开 `http://localhost:4000`，用上一步的 `AUTH_CODE` 登录。

## 3. 微信读书扫码登录（获取只读 token）

管理页 →「账号管理」→ 添加账号 → 用**微信读书 App**扫码登录。
WeWe RSS 借此拿到只读 token 去订阅公众号，不涉及发帖/写操作。

> token 会过期，失效后回本页重新扫码即可；数据卷已持久化，一般不必频繁重登。

## 4. 添加目标公众号

管理页 →「公众号源」→ 搜索并添加：

- **一瑜中的**（张瑜 / 华创宏观）
- **李迅雷金融与投资**

添加后等首次抓取完成（可手动触发「立即更新」）。

## 5. 复制 RSS URL 填进订阅列表

每个公众号会生成一条 RSS/Atom 地址，形如：

```
http://localhost:4000/feeds/<feed-id>.atom
http://localhost:4000/feeds/<feed-id>.rss
```

把它们逐行写入 `data/wechat_feeds.txt`（每行一个，`#` 开头为注释）：

```
# 一瑜中的
http://localhost:4000/feeds/xxxx.atom
# 李迅雷金融与投资
http://localhost:4000/feeds/yyyy.atom
```

> 确保 compose 里 `FEED_MODE=fulltext`，这样 RSS 带 `<content:encoded>` 全文，
> 入库脚本才能拿到完整正文（否则只有摘要）。

## 6. 入库

```bash
# 先干跑核对解析（不入库）
.venv/bin/python scripts/ingest_wechat_rss.py --dry-run --limit 3

# 解析正常后正式入库（断点续传，重复运行只补新文章）
.venv/bin/python scripts/ingest_wechat_rss.py

# 查看进度
.venv/bin/python scripts/ingest_wechat_rss.py --status
```

- `source_type="wechat"`，`skip_vectors=True`（无 VOYAGE key，向量后续统一 `embed-all`）。
- 已入库文章记在 `data/wechat_done.json`，可反复轮询抓增量。
- 质检不过（空/乱码/重复）的文章只记日志、不入库。

## 定期抓取（可选）

WeWe RSS 侧已有 `CRON_EXPRESSION` 定时刷新 RSS；本地入库可自行挂 cron / launchd：

```bash
# 例：每天 12:00 增量入库
0 12 * * *  cd /Users/pany19/Documents/x_agent_proj && .venv/bin/python scripts/ingest_wechat_rss.py >> data/_run_wechat.log 2>&1
```

---

## 故障排查

| 现象 | 排查 |
|------|------|
| 管理页打不开 | `docker compose -f docker-compose.wewe-rss.yml ps` 看容器是否 Up；看 logs |
| 扫码后仍抓不到文章 | token 可能过期，重扫；或该公众号需等下一轮 cron |
| 入库脚本正文很短/是摘要 | 确认 `FEED_MODE=fulltext`，RSS 里应有 `<content:encoded>` |
| 脚本报代理/SSL 错误 | 脚本已 `trust_env=False` + certifi 直连；确认 `.venv/bin/python` 而非 anaconda |
