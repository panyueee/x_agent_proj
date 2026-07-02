# 乌拉邦（ulapia.com）研报数据源调研

> 调研日期：2026-07-02。全部结论基于**无账号**的 requests/curl 实测（浏览器 UA，请求间隔 ≥2s），
> 未注册、未登录、未尝试绕过登录墙。配套骨架脚本：`scripts/ingest_ulapia.py`。

## 一、站点概况

- 定位：证券研报全文搜索引擎（"乌拉邦研报"），个人/小团队运营（页脚有渝备案号）。
- 技术栈：Laravel（CSRF meta token、标准 Laravel 分页）+ jQuery + axios + pdf.js。
  列表/详情全是**服务端渲染 HTML**，没有列表类 JSON API（typeahead 自动补全用的
  `/api/v1/{brokers,industries,stocks}/all` 是仅有的 JSON 接口，且**没有 authors 版**）。
- 站点是活的：宏观研报列表页最新日期 2026-07-01。
- 无账号抓取全程 HTTP 200，无验证码、无 403（浏览器 UA 下）。

## 二、URL 结构

| 页面 | URL | 说明 |
|---|---|---|
| 分类列表 | `/reports/{macro,strategy,stock,industry,ipo}_research`、`/reports/brokerage_news` | 服务端渲染，`?page=N` 翻页 |
| 全文搜索 | `/reports/search?query=张瑜&page=N` | 按相关度排序；分页疑似封顶 83 页（"张瑜""华创"两个词都恰好 83 页） |
| 作者页 | `/authors/{slug}` | `?page=N` 翻页，每页 10 条（末页不足 10）；Laravel 分页，无 `rel="next"` 即最后一页 |
| 券商页 | `/brokers/{slug}` | 如 `/brokers/huachuangzhengquan`（华创证券，标称收录 13875 篇） |
| 详情页 | `/reports/{分类}/{拼音slug}` | 含作者链接、券商链接、日期、页数、截断摘要 |
| 作者索引 | `/authors` | 纯字母序索引，**`?query=` 服务端不生效**（返回内容与不带参数完全一致） |

**按姓名找作者 slug 的正确姿势**：作者索引搜不了 → 走 `搜索该人名 → 打开任一命中研报详情页 →
提取正文里的 `<a href="/authors/xxx" title="券商+姓名">` 链接`（title 属性带券商名，可消歧同名）。
骨架脚本的 `--resolve` 就是这么实现的。

## 三、目标作者页（已实测确认）

| 分析师 | 作者页 | 标称篇数 | 实抓清单条数（骨架脚本翻到底） | 收录区间（实抓） |
|---|---|---|---|---|
| **张瑜（华创宏观首席）** | `https://www.ulapia.com/authors/zhangyu-2` | 514 篇 | **395 条**（40 页） | 2018-10-08 ~ **2021-01-13** |
| 张宇（中金，同音撞名） | `https://www.ulapia.com/authors/zhangyu` | — | — | ⚠️ 不是目标人物，上次会话撞的就是这个 |
| **李迅雷（中泰首席经济学家）** | `https://www.ulapia.com/authors/lixunlei` | 134 篇 | **36 条**（4 页） | 2018-10-30 ~ **2023-08-21** |

⚠️ **标称篇数 ≫ 可翻页条数**（张瑜 514/395，李迅雷 134/36——上次会话记的"李迅雷 134 篇"
实际可翻页拿到的只有 36 条）。差额原因未知：可能计数含合著（署名给了主作者条目）、
已下架或未展示条目；**是否登录后能看到更多，注册后需第一时间核实**。
两人的完整清单已由骨架脚本落盘 `data/ulapia_listing.json`（共 431 条）。

消歧依据：华创某篇宏观快评详情页作者链接为
`<a href="/authors/zhangyu-2" title="华创证券张瑜">张瑜</a>`，页面 meta keywords 亦为
"华创证券张瑜,张瑜最新研报"；作者页 `<title>` 为 "华创证券_张瑜_所有已发布研究分析报告"。

**⚠️ 关键限制——收录窗口按券商掐断了**：
- 华创证券整个券商页最新一篇停在 **2021-01-14**（实测 broker 页第 1 页），
  即张瑜 2021 年之后的报告乌拉邦**没有**，站内搜索"华创"命中的也全是 2020 及以前。
- 中泰（李迅雷页）收录到 2023-08。
- 站点整体宏观 feed 是最新的（2026-07），说明是**选择性停采了部分券商**（推测是版权/来源问题）。
- 结论：乌拉邦对本项目的价值是补 **张瑜 2018-10~2021-01 的 395 篇、李迅雷 ~2023-08 的 36 篇**
  历史存量；近几年的华创/中泰报告仍需另找渠道（公众号"一瑜中的"/"lixunlei0722" 等）。

## 四、免费（无账号）可得内容边界

**✅ 无需登录即可拿全**（纯 HTML，无 JS 依赖）：
- 列表页（作者/券商/分类/搜索）逐条字段：**标题、详情 URL、券商名、作者名、页数、发布日期、
  首页缩略图**（`img.ulapia.com/thumbnails/...jpg!thumbnail_180`）。翻页无墙，可翻到底。
- 详情页元数据：标题、券商链接、作者链接（用于消歧）、日期、页数、
  `og:document:cost`（免费/收费）、`og:document:type`（pdf）、
  **截断摘要 ~110 字**（meta description，含报告开头正文）。

**❌ 完全锁登录**（游客实测）：
- 全文在线预览：pdf.js 渲染，PDF 地址来自 `GET /reports/get_attachment/{slug}`，
  游客返回 `{"err_no":-1, "msg":"该研报仅限注册用户访问，登录后即可免费下载该研报"}`。
- 登录态探针 `GET /user/is_logged?slug={slug}&uuid={uuid}` 游客返回 `{"err_no":1}`，不给 link。
- 即：**摘要之外的正文/PDF 一个字都拿不到**，没有游客试读配额（本次实测该免费报告也不给）。

## 五、反爬/风控机制（show.js 逆向阅读 + 实测）

1. **登录墙**：全文接口靠 Laravel session cookie 鉴权；页面有 CSRF token（GET 接口未见强制）。
2. **限时下载直链**：登录后 `get_attachment` 返回的 `link` 指向 `dl.ulapia.com` 的签名 URL，
   **约 3 分钟过期**（上次会话结论），必须拿到后立刻下载。
3. **设备指纹**：show.js 为每个报告页生成随机 uuid 存 localStorage（键 `__ + MD5(slug)`），
   `is_logged` 请求携带，推测服务端按 uuid 做游客/账号配额与风控。
4. **爬虫 UA 黑名单**：show.js 内置一大串 bot UA 正则（客户端判断，命中直接不发请求）；
   服务端对浏览器 UA 的 curl 未拦截，但**别用默认 curl/python UA**。
5. **付费墙**：部分报告 `og:document:cost` 非"免费"，登录后 `err_no==1` 会弹升级/付款二维码
   （`pay_qrcode`）——下载器要识别并跳过收费报告。
6. 搜索分页疑似封顶 83 页（约 830 条），拿全某作者要走作者页而非搜索。

## 六、注册后下载器实现方案

前提：**用户本人注册账号**（手机号/微信，注册流程未探——需要用户来做），浏览器登录一次。

1. **Cookie 注入**：浏览器 DevTools 导出 `ulapia.com` 的 cookie（关键是 Laravel session 那条），
   存 `data/ulapia_cookies.json`（勿入 git），脚本加载进 `requests.Session`，
   带浏览器 UA + `Referer: 详情页 URL`。
2. **登录态自检**：起跑先对任一 slug 调 `GET /user/is_logged?slug=..&uuid=..`（uuid 固定复用
   一个随机 32 位字母数字串），`err_no==0` 即登录有效；失效则提示重新导 cookie。
3. **逐篇下载**：`GET /reports/get_attachment/{slug}`：
   - `err_no==0` → 取 `link`，**立即**（3 分钟内）下载 PDF，校验 `%PDF-` 魔数；
   - `err_no==1` → 收费报告，记 skip 不重试；
   - 其他 → 记 warn，退避。
4. **限速与配额**：小站+有风控，参考 zsxq 教训：篇间隔 ≥10s、每日限量（如 100 篇）、
   凌晨跑、断点续传（`data/ulapia_done.json`，每篇成功即落盘）；
   注册用户"免费下载"是否有每日次数上限**未知，注册后先小批量实测**。
5. **入库**：复用 `x_agent.rag.ingest_pdf`，`source_id=research:ulapia:{slug}`、
   `source_type="research"`、`skip_vectors=True`（向量统一 embed-all）。骨架里只留接口。

## 七、骨架脚本用法（`scripts/ingest_ulapia.py`）

```bash
# 无账号即可跑：拉作者研报清单（标题/日期/URL/页数），写 data/ulapia_listing.json
.venv/bin/python scripts/ingest_ulapia.py --list --authors zhangyu-2,lixunlei

# 按姓名反查作者 slug（搜索→详情页→作者链接，title 属性可看券商消歧）
.venv/bin/python scripts/ingest_ulapia.py --resolve 张瑜

# 下载入库（TODO：等注册后补 cookie，当前直接报错退出）
.venv/bin/python scripts/ingest_ulapia.py --ingest --authors zhangyu-2
```
