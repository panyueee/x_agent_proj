# 百度网盘接入配置（一步步详解）

接通后：把 PDF 书丢进你自己的百度网盘 → 在看板上一键下载并入库到 RAG 知识库（`source_type="netdisk"`）。

实现位于 `agents/dashboard.py`，用**百度官方 OpenAPI（xpan）+ OAuth**。关键参数（代码里写死/读环境变量）：
- 授权回调地址：`http://localhost:8765/api/baidu/callback`（**必须和应用里填的一致**）
- 权限范围 scope：`basic,netdisk`
- 看板端口：`8765`
- token 保存到：`output/baidu_token.json`
- 需要的环境变量：`BAIDU_APP_KEY`、`BAIDU_APP_SECRET`

---

## 第 1 步：注册百度网盘开放平台应用，拿 AppKey / SecretKey

1. 浏览器打开 <https://pan.baidu.com/union>（百度网盘开放平台），用你的百度账号登录。
2. 进入「控制台 / 应用管理」→「创建应用」。
3. 应用类型选 **「软件」**（不是网站）。填应用名称（随便，如 `x-agent-rag`）。
4. 接入能力勾选 **「网盘」**（开放网盘 / xpan 相关权限）。提交后可能需要等**审核**（个人开发者通常很快，但有时要 1～2 天）。
5. 创建成功后，在应用详情页能看到：
   - **AppKey**（也叫 API Key）→ 对应 `BAIDU_APP_KEY`
   - **SecretKey**（也叫 Secret Key）→ 对应 `BAIDU_APP_SECRET`
   （还有 SignKey，本项目用不到。）
6. 在应用的「安全设置 / 授权回调地址（redirect_uri）」里，**填入完全一致的**：
   ```
   http://localhost:8765/api/baidu/callback
   ```
   ⚠️ 一个字符都不能差（http 非 https、端口 8765、路径 /api/baidu/callback）。这是最常见的踩坑点。

---

## 第 2 步：把 AppKey/SecretKey 写进 .env

项目的 `.env` 是 `export` 格式。编辑 `.env`，在末尾加两行（把 xxx 换成你的）：

```bash
export BAIDU_APP_KEY=xxxxxxxxxxxxxxxxxxxxxxxx
export BAIDU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxx
```

> 密钥只放 `.env`、绝不进代码/git（`.env` 已被 gitignore）。

---

## 第 3 步：启动看板（务必用 .venv，并先 source .env）

```bash
cd /Users/pany19/Documents/x_agent_proj
source .env && .venv/bin/python agents/dashboard.py
```

看到 `看板启动中... 访问 http://localhost:8765` 即成功。
（注意：`dashboard.py` 不会自动读 `.env`，所以**必须先 `source .env`** 让环境变量进当前 shell。）

---

## 第 4 步：浏览器里完成 OAuth 授权（只需一次）

1. 打开 <http://localhost:8765>。
2. 找到「百度网盘」面板，点 **「连接百度网盘」**。
   - 它会跳到 `openapi.baidu.com/oauth/2.0/authorize`（百度官方授权页）。
3. 用百度账号登录 → 点「同意 / 授权」。
4. 百度会带着 `code` 跳回 `http://localhost:8765/api/baidu/callback`，看板自动用 code 换 token 并保存到 `output/baidu_token.json`，然后跳回看板。
5. 面板状态变成「已连接」+ 显示你的网盘昵称即成功。token 会自动续期（refresh_token），平时不用再授权。

---

## 第 5 步：同步书籍入库

1. 把要入库的 **PDF** 书放进你的百度网盘（任意目录；看板默认列根目录，可在面板里浏览子目录）。
2. 看板百度网盘面板里会列出网盘中的 PDF → 勾选要入库的 → 点「同步入库」。
3. 后台会逐本下载到 `books/` 并 `ingest_pdf(..., source_type="netdisk")` 入库，面板有实时日志（下载/入库块数）。
4. 入库后即可在「知识库问答」里检索到（问答默认检索全部类型，含 netdisk）。

---

## 常见坑

| 现象 | 原因 / 解决 |
|---|---|
| 点连接报 `请先设置 BAIDU_APP_KEY` | 没 `source .env` 就启动，或 .env 没加这两行。先 source 再启动。 |
| 授权回调 `redirect_uri` 不匹配/报错 | 开放平台里填的回调地址和 `http://localhost:8765/api/baidu/callback` 不完全一致。改成完全一致。 |
| 授权页报应用无网盘权限 | 应用没勾「网盘」能力或还在审核中。等审核通过。 |
| 列不出文件 | 只列 `.pdf`（代码里按后缀过滤）；非 PDF（epub/txt）不会显示。先转成 PDF，或后续可扩展支持。 |
| 别人的**分享链接**（pan.baidu.com/s/xxx）拉不进来 | 官方 API 不支持直接下分享链接。先在网盘 App 里「保存到我的网盘」(转存)，再用本流程同步。 |

---

## 关于版权
本流程是从**你自己的**网盘下载文件入库（个人使用）。注意书单里 45 本是版权书；网盘上的盗版副本与影子图书馆性质相同，是否使用由你按所在地法律判断。
