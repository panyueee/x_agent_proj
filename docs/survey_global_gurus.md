# 海外投资/宏观大牛文章获取渠道调研

> 调研日期：2026-07-02。所有结论均经 curl 实测（本机环境），未注册任何付费服务、未实际订阅邮件。
> 评级说明：**易** = 静态 HTML/PDF/RSS 直接可抓；**中** = 需处理 JS 分页/API/注册墙/网络绕行；**难** = 反爬拦截，需真实浏览器；**付费墙** = 核心内容收费。

## 本机抓取环境注意（实测踩坑）

1. 本机 shell 默认走 `127.0.0.1:1089` 代理。**fedguy.com、lynalden.com 走代理时 TLS 握手被掐断（HTTP 000），必须 `--noproxy '*'` 直连**；反之 blog.bitmex.com 直连不稳、走代理正常。接入时每个源要单独确认代理开关。
2. berkshirehathaway.com（Sucuri CDN）**无视 `Accept-Encoding: identity`，强制返回 brotli**。本机 curl 无 brotli 支持，返回"乱码"其实是 br 数据；用 Python `requests`/`httpx`（装 `Brotli` 包）即可，.venv 已装好。
3. 所有站点都要带浏览器 UA；实测频率下（每站 2-5 次请求）无一触发限流。

---

## 一、免费全量档案（实测确认）

### 1. Warren Buffett — 伯克希尔股东信 ★

- **档案**：https://www.berkshirehathaway.com/letters/letters.html
- **回溯**：1977 至今（实测索引共 49 封链接，2025 年信在列）
- **全文方式**：1977–2003 为纯 HTML（如 `/letters/1977.html`，实测 200），2004 起为 PDF（`/letters/2004ltr.pdf`）
- **RSS**：无（每年 2 月更新一次，无需轮询）
- **反爬**：Sucuri CDN + 强制 brotli（见上）；无验证码
- **评级**：**易**。一次性静态回填约 49 个文件即完成。

### 2. Howard Marks — Oaktree 备忘录 ★

- **档案**：https://www.oaktreecapital.com/insights/memos
- **回溯**：1990-10-12（The Route to Performance）至今，列表页一页列全
- **全文方式**：
  - 老备忘录：直链 PDF，URL 形如 `/docs/default-source/memos/1990-10-12-....pdf`，无需登录
  - 近年备忘录：站内 HTML 全文页（实测 On Bubble Watch 正文完整）
  - **彩蛋：官方提供全集单文件 `the-complete-collection.pdf`（以及精选集 the-best-of.pdf），一个 PDF 拿下全部 36 年备忘录**
- **RSS**：未发现
- **评级**：**易**。全集 PDF 一次入库 + 每月看一次列表页做增量。

### 3. Aswath Damodaran — Musings on Markets ★

- **档案**：https://aswathdamodaran.blogspot.com/（博客），https://pages.stern.nyu.edu/~adamodar/（NYU 主页，论文/数据，老式 frameset 静态页）
- **回溯**：博客 2008 至 2026（实测归档链接连续）
- **全文方式**：Blogger 官方 RSS **含全文**：`/feeds/posts/default?alt=rss&max-results=N`（实测 200），配 `start-index` 分页可一次拉完全量历史 —— Blogger feed 是最省事的全量接口
- **RSS**：有（同上）
- **评级**：**易**。

### 4. Michael Mauboussin — Counterpoint Global / Consilient Observer

- **官方档案**：morganstanley.com 相关页 **HTML 与 PDF 直链均 403（Akamai Access Denied，代理/直连都拦）**，curl 不可用
- **替代路径（实测可用）**：个人站 https://michaelmauboussin.com/writing 挂出 **1995–2020 全部研究文章的汇编 PDF 合集**（mjbaldbard.files.wordpress.com 直链，200 可下），按年代分卷约 8 个大 PDF
- **回溯**：旧文 1995–2020 全量易得；2021 至今的 Consilient Observer 在 Morgan Stanley 站内，需真实浏览器（claude-in-chrome / Playwright）逐篇取 PDF
- **RSS**：无
- **评级**：**旧文易 / 新文难**。

### 5. Cliff Asness — AQR Cliff's Perspectives

- **档案**：https://www.aqr.com/Insights/Perspectives（实测 200，标题 "Cliff's Perspectives"）
- **回溯**：列表页含近年文章（2024–2026 在列）；更早的在 aqr.com/Insights/Research 需翻页
- **全文方式**：站内 HTML 全文，文章 URL 规则清晰（`/Insights/Perspectives/<slug>`）
- **RSS**：未发现
- **评级**：**易–中**（无 RSS，靠列表页轮询）。

### 6. Jeremy Grantham / GMO — 季度信

- **档案**：https://www.gmo.com/americas/research-library/
- **回溯**：列表首屏只渲染 3 篇，历史靠 "Load More" 调 `/api/articles/getArticlesResearchLibrary`（实测裸调 500，需带页面里的 uid 等参数，要抓包补参）
- **全文方式**：**文章页与 PDF 直链均无需注册**（实测 2Q-2026 季度信 PDF 200，1.1MB 直下）；页面上的 Subscribe 弹窗只是邮件订阅入口，不是墙
- **RSS**：未发现
- **评级**：**中**（内容全免费，但列表翻页要逆向一个 API 或用浏览器）。

### 7. John Hussman — 每周/每月市场评论 ★

- **档案**：https://www.hussmanfunds.com/comment/（2017-10 至今，WordPress）+ https://www.hussmanfunds.com/market-comment-archive/（旧档案索引）
- **回溯**：实测旧档案最早 `wmc030901`（2003-09），wmc 旧页面（`../wmc/wmc171009.htm` 等）+ 新式 `/comment/mcYYMMDD/`，**2003 至今连续**
- **全文方式**：HTML；**RSS 含全文**（`/feed/` 实测 540KB / 10 条，content 完整）
- **评级**：**易**。

### 8. Joseph Wang — FedGuy

- **档案**：https://fedguy.com/（**必须 --noproxy 直连**）
- **回溯**：2020 年开博至今（分页不深，page/40 已 404）
- **全文方式**：WordPress RSS `/feed/`（实测 200，10 条）；**但站点是 freemium：相当比例深度文标记 Premium 付费**，免费文为部分
- **评级**：**中**（技术上易，免费边界窄——免费文可入库，Premium 拿不到）。

### 9. Lyn Alden — 免费 Newsletter 档案 ★

- **档案**：https://www.lynalden.com/newsletter-archives/（**必须 --noproxy 直连**）
- **回溯**：实测档案页约 90+ 期链接，**2019-04 至 2026-03 全部免费网页全文**
- **全文方式**：HTML 全文；RSS `/feed/` 正常（近期文章）
- **备注**：她的付费服务在 stockwaves 等外站，lynalden.com 上的 newsletter 本体全免费
- **评级**：**易**。

### 10. Ray Dalio

- **economicprinciples.org**：实测只是 8KB 宣传页（书籍/视频导流），**无文章档案**
- **LinkedIn 文章**：需登录，curl 301 到登录墙，不适合程序化抓取（且违反 LinkedIn 条款）
- **替代**：Dalio 长文多同步出书/YouTube；短观点在 X（可走已有 twitterapi.io 渠道盯 @RayDalio）
- **评级**：**难**（无干净的文章档案源，建议降级为 X 账号监控）。

### 11. Morgan Housel — Collab Fund Blog ★

- **档案**：https://collabfund.com/blog/
- **全文方式**：HTML；**RSS 在 `https://collabfund.com/feed.xml`**（注意不是 /feed/，实测 200 含全文 content:encoded）
- **回溯**：博客数百篇（Housel 2016 年入职起的文章都在），列表可翻页
- **评级**：**易**。

### 12. Arthur Hayes ★

- **现档案**：https://cryptohayes.substack.com/archive
  - RSS `/feed` 实测 253KB **含全文**（他的文章 `audience: everyone` 全免费）
  - **Substack 通用档案 API 可拉全量**：`/api/v1/archive?sort=new&offset=N&limit=M`（实测 200 返回 JSON，含 slug/日期/canonical_url），逐条再取正文即可回填
- **旧文**：https://blog.bitmex.com/category/crypto-trader-digest/（实测 200，2015–2020 的 Crypto Trader Digest；该站走代理访问正常）
- **评级**：**易**。与项目现有加密监控主线最契合。

### 13. Vitalik Buterin ★

- **档案**：https://vitalik.eth.limo/（纯静态站）
- **回溯**：实测 index 列到 `/general/2016/`–`/general/2026/`（更早 2013–2015 旧文在原 vitalik.ca 时代，部分未收录）
- **全文方式**：静态 HTML + **RSS `feed.xml`**（实测 200）
- **评级**：**易**。最好抓的一个：无任何反爬、URL 即年份目录。

---

## 二、Newsletter 型（邮件为主）

### 14. Matt Levine — Bloomberg Money Stuff

- bloomberg.com 作者页实测 **403 "Are you a robot?"**，网页存档在付费墙+反爬后面
- **免费边界**：Money Stuff 邮件订阅本身免费（bloomberg.com/account/newsletters 注册即可）
- **接入方式**：免费邮箱订阅 → 邮件转 RSS（Kill the Newsletter! 或自建 IMAP 解析，项目已有 wewe-rss 处理公众号的经验可复用思路）。**只能拿订阅日之后的增量，无法回填历史**
- **评级**：**中**（邮件路线可行；网页历史档案实际不可得）。

### 15. Torsten Slok — Apollo Daily Spark

- 原 apolloacademy.com/category/the-daily-spark/ 已 301 到 https://www.apollo.com/wealth/insights-news/insights/daily-spark
- **RSS 仍在**：https://www.apolloacademy.com/feed/ 实测 200，20 条含 content:encoded（Daily Spark 本身就是"一图一段"短文，1–3KB 即全文）
- 网站端看文需免费注册，**RSS 直接绕开注册墙**
- **评级**：**易–中**（RSS 全文可用；历史回填受 feed 长度限制，只能存增量）。

### 16. John Authers — Bloomberg Points of Return

- 同 Matt Levine：网页 403 反爬 + 付费墙，邮件订阅免费
- **评级**：**中**（邮件转 RSS，同一套设施顺带做）。

---

## 三、专栏聚合

### 17. Project Syndicate（Roubini / Rogoff / Stiglitz / El-Erian）

- 专栏页：https://www.project-syndicate.org/columnist/nouriel-roubini（实测 200）
- **RSS**：https://www.project-syndicate.org/rss（实测 200，全站最新 commentary）
- **付费边界**：metered——文章页带大量 register 提示，未登录可读少量，注册免费账户后每月限量免费读；PS OnPoint/长读为订阅内容
- **评级**：**中**（RSS 摘要 + 限量全文；不适合全量入库，适合做"标题+摘要"信号源）。

---

## 四、付费墙组（只记录，未深挖）

| 人物 | 渠道 | 实测/结论 |
|---|---|---|
| Zoltan Pozsar | Ex Uno Plures（exunoplures.com） | 实测仅 114 字节空壳页；机构付费研究，无公开档案。放弃 |
| David Rosenberg | rosenbergresearch.com | 站点 200，营销页；研究全付费（有试用）。放弃 |
| Ed Yardeni | yardeniquicktakes.com | 比预期好：**有 `/rss/` 和 `/archive/`**，QuickTakes 部分免费、深度付费（freemium）。可低成本挂个 RSS 白嫖免费部分 |
| Ben Thompson | stratechery.com | **每周免费文有公开 RSS `/feed/`**（实测 200，10 条）；Daily Update 付费。免费周文可接 |
| Doomberg | doomberg.substack.com（现迁 doomberg.com） | Substack 付费为主，免费文极少。放弃 |

---

## 五、接入建议（按性价比排序）

### Top 5 建议先接

| 优先级 | 源 | 方式 | 理由 |
|---|---|---|---|
| 1 | **Howard Marks 备忘录** | 一次性下载 the-complete-collection.pdf（或逐篇 PDF）+ 每月轮询列表页 | 一个 PDF 拿下 1990–今全集，投入产出比全场最高，与已入库投资经典互补 |
| 2 | **Buffett 股东信** | 静态爬取 49 个 HTML/PDF，一次回填，每年 2 月手动补 | 1977–今，永久档案，零维护；仅需处理 brotli |
| 3 | **Arthur Hayes（Substack + BitMEX 旧档）** | archive API 回填全量 + RSS 轮询增量 | 全免费全文、双接口稳定，与项目加密宏观主线直接相关 |
| 4 | **Damodaran 博客** | Blogger feed 分页回填 2008–今 + RSS 轮询 | 估值方法论第一人，Blogger 官方 API 级接口最不易失效 |
| 5 | **Lyn Alden + Hussman**（同为 WordPress，一套代码） | 档案页静态回填 + `/feed/` 全文 RSS 轮询 | Alden 90+ 期宏观长文全免费；Hussman 2003–今每周评论连续 23 年 |

第二梯队：Vitalik（最易，顺手接）、Collab Fund/Housel（feed.xml 全文）、Apollo Daily Spark（RSS 绕注册墙）、Mauboussin 1995–2020 汇编 PDF（一次性 8 个文件）、AQR、Stratechery 免费周文、Yardeni 免费 QuickTakes。

### 分方式汇总

- **RSS 轮询（全文）**：Damodaran、Hussman、Hayes、Lyn Alden、Collab Fund（feed.xml）、Vitalik、Apollo、FedGuy（免费部分）、Stratechery、Yardeni、PS（仅摘要）
- **静态爬取一次性回填**：Buffett、Oaktree 全集 PDF、Mauboussin 汇编 PDF、Hussman 旧 wmc 档案、Hayes BitMEX 旧文、Lyn Alden 档案页
- **邮件转 RSS**（只记录方式，未实订）：Matt Levine、John Authers（Bloomberg 免费 newsletter → Kill the Newsletter! / 自建 IMAP）
- **需真实浏览器**：Morgan Stanley 的 Mauboussin 新文（Akamai 403）；Dalio LinkedIn（建议改走 X 监控）

### 免费边界结论

**这批海外大牛的历史全量档案绝大部分是免费的**——Buffett/Marks/Damodaran/Hussman/Alden/Hayes/Vitalik/Housel/GMO 均可免费拿到全文全史，这是与国内券商研报生态最大的不同。付费边界集中在四处：① Bloomberg 专栏的**网页存档**（但邮件订阅免费，只损失历史回填）；② FedGuy 的深度文（Premium）；③ 机构级研究（Zoltan/Rosenberg/Doomberg，放弃）；④ Morgan Stanley 站点反爬（内容本身免费，是技术墙不是付费墙）。整体判断：**不花一分钱可以覆盖名单 80% 以上的核心内容**。
