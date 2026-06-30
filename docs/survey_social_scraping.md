# 社交平台抓取方案调研（小红书 / 淘股吧）

> 对应分支：`feature/mediacrawler`。本文调研开源抓取方案，评估是否用 MediaCrawler
> 等成熟项目替换/补强当前自研 Playwright 抓取，并给出**逐平台**建议。
> 调研时间：2026-06。所有结论附来源 URL，未安装/运行任何抓取器。

---

## 1. 当前实现（main 分支现状）

抓取栈是**两套不同思路**，不要一概而论：

| 平台 | 现状文件 | 实现方式 | 签名/反爬负担 |
|---|---|---|---|
| 小红书 | `x_agent/xhs_fetcher.py` | **外部 CLI** `xhs`（`xiaohongshu-cli`），subprocess 调用 + `--yaml`；图片走 macOS Vision OCR | **不自己处理**，签名由 CLI 内部完成 |
| 淘股吧 | `x_agent/_tgb_scraper.py` + `tgb_fetcher.py` | **自研 Playwright**，无头 Chromium 滚动取 DOM；subprocess / 远程 VPS 双模式 | 无加密签名，痛点是 **DOM 脆弱 + 浏览器稳定性** |

关键观察（与 `CLAUDE.md` 的"只走 API，绝不直接爬 x.com 网页"硬规则一致——该规则只约束 Twitter，其余平台允许抓取）：

- **小红书其实已经不是自研**。代码 shell out 到 `xhs` 命令，即 `jackwener/xiaohongshu-cli`：
  Apache-2.0、最新版 0.6.4（2026-03-13）、自带逆向签名（`x-s` / `x-s-common` / `x-t`）+
  高斯抖动反检测，输出 YAML/JSON。这点很重要：**当前 XHS 方案已经是"依赖一个活跃维护、商用友好的库"**，
  而非手写。
- **淘股吧才是真正的自研重负担**。`_tgb_scraper.py` 里的 SIGALRM 硬超时、`os._exit(2)`、
  独立线程关浏览器、预热 `about:blank`、6~15 次 `End` 滚动——这些都是在和 Playwright 的稳定性
  与反爬搏斗的症状，维护成本高。
- 两类痛点本质不同：**XHS 痛点 = 签名漂移（x-s/x-t 频繁变）**，正是库/签名服务能替你扛的；
  **淘股吧痛点 = DOM 易碎 + bot 检测**，没有任何开源库能替你解决。

> 注：`.claude/worktrees/feature-mediacrawler/` 只是一份较旧的分支快照，并未包含真正的
> MediaCrawler 集成代码，可忽略。

---

## 2. 候选方案对比

| 方案 | 平台覆盖 | 反爬/签名处理 | 维护活跃 | License | 风险 |
|---|---|---|---|---|---|
| **MediaCrawler** (NanmiCoder) | xhs / 抖音 / 快手 / B站 / 微博 / 贴吧 / 知乎（**无淘股吧**） | Playwright + 扫码登录 + 在已登录浏览器里执行 JS 取签名（`window._webmsxyw`），免 JS 逆向；带 IP 代理池 | 极活跃，54.4k★ / 11.1k fork / ~776 commits；Python 3.11 + Node≥16 | **NON-COMMERCIAL LEARNING LICENSE 1.1**（自定义，**禁商用、禁大规模爬取**） | License 与本项目"运营型"用途冲突；Playwright 重、依赖多；需登录态=封号风险 |
| **MediaCrawlerPro** | 同上 | **去 Playwright**，独立签名服务解耦；断点续爬、多账号 + IP 代理池 | 商业/知识付费版（付费） | 闭源/付费授权 | 付费；同样不覆盖淘股吧；商用授权需谈 |
| **ReaJason/xhs** | 仅小红书 | `x-s`/`x-t` 签名；需 Playwright + `stealth.min.js`，或起一个 sign server；多账号有 `sync_playwright` 单例限制 | 活跃，2.2k★ / 449 fork；PyPI `pip install xhs` | **MIT** | 仍需浏览器/签名服务；多账号支持弱 |
| **Spider_XHS** (cv-cat) | 仅小红书（采集+发布+蒲公英KOL） | JS 重度逆向签名（a1/x-s/x-t/x-s-common 等，`static/*.js`） | 活跃，6.6k★ / 1.2k fork，更新至 2026-04 | MIT，但 README **明确"禁止任何商业化行为"** | JS 栈为主（Node 99%），融进 Python 管道需起 Node 进程；声明禁商用 |
| **xiaohongshu-cli** (jackwener)＝**现用** | 仅小红书 | 自带逆向签名（x-s/x-s-common/x-t）+ 抖动反检测，无需自己起浏览器 | 活跃，0.6.4 / 2026-03 | **Apache-2.0**（商用友好） | Alpha 阶段；签名随平台更新可能阶段性失效（但由上游修，非你修） |
| **自研 Playwright**（现用淘股吧） | 任意（你写选择器就行） | 无签名；纯 DOM 解析，反爬靠 UA/节流 | 由你维护 | 你的代码（无第三方约束） | 平台改版即碎；浏览器稳定性差，需大量超时/兜底 hack |

---

## 3. 诚实评估：自研 vs 依赖快变的抓取器

**抵住分支名的诱惑。** `feature/mediacrawler` + 54k★ 很容易让人倾向"上 MediaCrawler"。
但证据指向相反结论，至少对本项目如此：

1. **License 是硬墙，不是脚注。** 本项目是**运营型金融资讯监控 Agent**（定时增量同步、入库、生成摘要、
   还接了飞书/百度网盘），不是"个人学习研究"。MediaCrawler 的
   *NON-COMMERCIAL LEARNING LICENSE 1.1* 明确禁商用、禁大规模爬取——直接采用即违背其授权。
   相比之下当前 `xiaohongshu-cli` 是 **Apache-2.0**，这反而是当前栈被低估的优点。
2. **维护权衡因平台而异，不能一刀切：**
   - **XHS = 签名漂移痛点。** 这正是库/签名服务存在的意义——你把 x-s/x-t 的逆向外包出去，
     上游平台一改、上游库修、你 `pip upgrade` 即可。当前 `xiaohongshu-cli` 已经实现了这个外包，
     且 Apache-2.0、月度更新。**继续依赖它，比换 MediaCrawler 更稳、更合规、更轻。**
   - **淘股吧 = DOM 脆弱痛点。** 没有任何开源库覆盖淘股吧（MediaCrawler 等全部 N/A）。
     这里只有一个问题：**把自研 Playwright 维护得更好**，没有"换库"的选项。
3. **重量级 Playwright 是成本不是收益。** MediaCrawler 需要 Python3.11 + Node≥16 + 完整 Chromium +
   扫码登录态。本项目 XHS 现用 CLI 不需要常驻浏览器；引入 MediaCrawler 等于在已经更轻的方案上加重。

**合规 / 封号角度（不可忽视，近年判例趋严）：**

- 小红书运营方多次维权胜诉：2025-04 杭州中院认定某厦门公司抓取小红书数据构成不正当竞争，
  终审判赔 **490 万元** 并要求删除数据；另有团伙因绕过反爬非法获取数据牟利 **650 余万元**被判刑。
- 绕过平台反爬技术措施可能触及**非法获取计算机信息系统数据罪**；小红书用户协议明确禁止抓取，
  违者可封号 + 承担法律责任。
- 对本项目的含义：**抓取规模务必克制**（关键词/关注账号增量，而非全站大规模采集）、
  尊重平台节流、登录态尽量少用/隔离专用账号、抓取所得仅供内部信号分析不得对外分发或商用倒卖。
  无论用哪个方案，这条风险都在，且与 License 无关——**"技术可行"不等于"合规可做"**。

来源：
[光大律师·爬虫合法性边界](https://www.everbrightlaw.com/CN/07/4b3df8779a975b22.aspx) ·
[安全内参·非法抓取小红书获利 650 余万被判刑](https://www.secrss.com/articles/72097) ·
[腾讯新闻·非法爬取小红书终审判赔 490 万](https://news.qq.com/rain/a/20250427A0580800)

---

## 4. 逐平台建议

### 小红书：维持现状（`xiaohongshu-cli`），不换 MediaCrawler

- **保留**当前 `xhs` CLI 方案。理由：Apache-2.0（唯一商用友好）、活跃维护、签名已外包、
  无需常驻浏览器，与现有 subprocess + YAML 解析管道完美契合。
- **风险对冲**：把"签名失效"当常态规划——`xhs_fetcher.py` 已有 CLI 返回非 YAML 时返回空 dict 的容错，
  建议再加：CLI 版本钉死 + 监控（连续 N 次空结果即告警），并保留切换到 `ReaJason/xhs`（MIT）作为
  Plan B 的能力（两者都做 x-s/x-t，接口语义相近）。
- **不建议** Spider_XHS（Node 重 + 声明禁商用）和 MediaCrawler（非商用 License）。
- 抓取量保持"按关注账号/关键词增量"，不做全站采集。

### 淘股吧：维持自研 Playwright，做"稳定性工程"而非换库

- **没有开源库可换**——MediaCrawler / xhs 全不覆盖淘股吧，这是事实，不是没调研。
- 因此重点是把 `_tgb_scraper.py` 的脆弱点工程化：
  - 选择器集中成常量表 + 改版时单点修改；解析失败要可观测（当前静默返回 `{}`，建议加计数告警）。
  - 现有"远程 VPS 常驻服务"模式（`TGB_SCRAPER_URL`）是对的方向——常驻浏览器比每次冷启更稳，
    建议作为主路径，本地 subprocess 仅作兜底。
  - 可评估引入 `playwright-stealth` 降低 bot 检测概率，但收益有限（淘股吧反爬不强），优先级低于
    DOM 解析的健壮性。
- 同样遵守节流与合规边界。

### 其他平台（抖音/B站/微博等）若未来要接

- 若**确有**接入抖音/快手/B站/微博/知乎的需求，再考虑 MediaCrawler 系——但因 License 问题，
  应优先评估 **ReaJason/xhs（MIT）类** 单平台库，或 **MediaCrawlerPro 的商用授权**，
  而非直接搬 GPL 式非商用的开源 MediaCrawler 进运营型项目。

---

## 5. 来源汇总

- MediaCrawler 仓库：<https://github.com/NanmiCoder/MediaCrawler>（54.4k★，平台覆盖、Playwright+JS取签名、IP代理池）
- MediaCrawler LICENSE：<https://github.com/NanmiCoder/MediaCrawler/blob/main/LICENSE>（NON-COMMERCIAL LEARNING LICENSE 1.1）
- MediaCrawler 源码分析（x-s/x-t、`window._webmsxyw`）：<https://segmentfault.com/a/1190000044741501>
- MediaCrawlerPro：<https://github.com/MediaCrawlerPro> · 知识付费说明 <https://nanmicoder.github.io/MediaCrawler/知识付费介绍.html>
- ReaJason/xhs：<https://github.com/ReaJason/xhs>（2.2k★，MIT，签名需 playwright+stealth/sign server）· 文档 <https://reajason.github.io/xhs/>
- Spider_XHS：<https://github.com/cv-cat/Spider_XHS>（6.6k★，MIT 但禁商用，JS 重度逆向）
- xiaohongshu-cli（现用）：<https://pypi.org/project/xiaohongshu-cli/> · <https://github.com/jackwener/xiaohongshu-cli>（Apache-2.0，0.6.4/2026-03）
- 法律/合规：<https://www.everbrightlaw.com/CN/07/4b3df8779a975b22.aspx> · <https://www.secrss.com/articles/72097> · <https://news.qq.com/rain/a/20250427A0580800>
