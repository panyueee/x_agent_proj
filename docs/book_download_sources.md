# 投资书单缺失 49 本 — 下载来源指南

> 调研日期：2026-06-30
> 用途：本知识库为**个人单用户研究**用途。下方区分了各书的版权/获取类别。
> **公版**与**官方免费**两类可直接合法下载；**版权书**为商业出版物，是否可下载取决于你
> 所在司法辖区的版权法与合理使用规定，请自行判断后再获取。本文件只做来源调研与记录，
> 不附带任何受版权保护文件的直链，也不代为下载。

## 调研方法与结论摘要

1. **GitHub 自动扫描**（`python scripts/scan_github_pdfs.py --dry-run`，需先 `touch scripts/__init__.py`
   或设 `PYTHONPATH=.` 才能导入 `batch_ingest_books`）：
   配置的唯一仓库 `manjunath5496/Best-Investing-Books` 共 25 个 PDF，其中 23 本已在 `books/`，
   其余 2 本（*Stock Investing For Dummies*、*How to Make Money in Stocks*）不在书单。
   **对缺失的 49 本，GitHub 命中 0 本** —— GitHub 不是这 49 本的可用来源。
2. **逐本 Web 调研**确认版权状态与最佳获取路径，优先级：公版免费 > 官方免费 > GitHub > 版权书。

| 类别 | 本数 | 说明 |
|------|------|------|
| ✅ 公版免费 | 2 | Reminiscences of a Stock Operator、Think and Grow Rich |
| ✅ 官方免费 | 2 | Berkshire Hathaway Letters、Poor Charlie's Almanack |
| GitHub 托管 | 0 | 扫描无命中 |
| 版权书（需自行获取） | 45 | 商业出版物，下方给出搜索入口与合规提示 |

---

## ✅ 一、可立即免费、合法获取（4 本，优先下载）

| # | 书名 | 作者 | 类别 | 推荐下载源(URL) | 备注 |
|---|------|------|------|-----------------|------|
| 3 | Poor Charlie's Almanack | Charlie Munger (ed. Kaufman) | 官方免费 | https://stripe.press/poor-charlies-almanack | ★★★★★。Stripe Press 2023 新版**官方免费在线全文 + 有声书**（网页阅读器，非 PDF 直链；正文为 HTML，可抓取入库）。纸质/Kindle 在 Amazon 有售。 |
| 10 | Reminiscences of a Stock Operator | Edwin Lefèvre | 公版免费 | https://www.gutenberg.org/ebooks/60979 | ★★★★★。1923 年出版，美国公版。Gutenberg 提供 EPUB / TXT（无官方 PDF）。**注意：项目里 `scripts/download_free_books.py` 用的 ID `9840` 已失效（现指向另一本书 *Vivian Grey*），正确 ID 是 60979。** archive.org 备选：https://archive.org/details/reminiscencesofs0000lefe |
| 22 | Berkshire Hathaway Letters to Shareholders | Warren Buffett | 官方免费 | https://www.berkshirehathaway.com/letters/letters.html | ★★★★★。1977–2024 全部年报致股东信，官网免费（1977–2003 为 HTML，2004+ 为 PDF）。`download_free_books.py` 已会逐年下载并用 pypdf 合并成一本。 |
| 70 | Think and Grow Rich | Napoleon Hill | 公版免费 | https://archive.org/details/think-and-grow-rich-napolean-hill | ★★★。1937 年版美国公版（版权未续展，1965 年前未续 → 进入公版）。该 archive.org 条目可直接下载 PDF / EPUB / 全文 TXT（非借阅制）。原版全文也见 https://sacred-texts.com/nth/tgr/ 。注意：Napoleon Hill Foundation 持有书名**商标**，公版的是正文，不影响个人使用。 |

> 这 4 本中三本是 ★★★★★ 高优先级（#3、#10、#22），应**最先入库**。
> 项目脚本 `scripts/download_free_books.py` 已覆盖 #10 与 #22 的自动下载（#10 的 Gutenberg ID 需更正为 60979）；
> #3 为网页阅读器、#70 为 archive.org 公版，目前脚本未覆盖，可手动获取或扩展脚本。

---

## 二、版权书（46 本，商业出版物）

下列均为仍在版权保护期内的商业出版物。合规获取途径优先级：

1. **正版购买** —— 出版社官网 / Amazon 纸质或 Kindle / Apple Books / Google Play Books。
2. **图书馆电子借阅** —— Libby / OverDrive（绑定本地公共图书馆借阅卡，多数英文书可借）；
   archive.org「受控数字借阅」(Controlled Digital Lending) 可在线借阅而非下载。
3. **影子图书馆聚合检索** —— Anna's Archive (`annas-archive.org`)、Library Genesis 等会索引这些书。
   搜索入口格式：`https://annas-archive.org/search?q=<书名>`。
   ⚠️ **此途径下载受版权保护内容是否合法取决于你所在地区的法律，请仅在个人研究/合理使用范围内自行判断。** 本文件不提供已验证的直链。

为便于检索，下表「推荐下载源」列给出各书的 Anna's Archive 搜索链接（仅为检索入口，非直链）。

### 价值投资 (value)

| # | 书名 | 作者 | 类别 | Anna's Archive 检索 | 备注 |
|---|------|------|------|---------------------|------|
| 15 | Margin of Safety | Seth Klarman | 版权书 | https://annas-archive.org/search?q=Margin+of+Safety+Klarman | ★★★★★。早已绝版，二手纸质书价格极高，电子版主要靠影子库。 |
| 21 | Security Analysis | Benjamin Graham | 版权书 | https://annas-archive.org/search?q=Security+Analysis+Graham+Dodd | ★★★★★。1934 初版仍在版权期（约 2030 才进入公版），常读为 1940/1951/2008 版。 |
| 31 | Value Investing: From Graham to Buffett | Bruce Greenwald | 版权书 | https://annas-archive.org/search?q=Value+Investing+Greenwald | ★★★★ |
| 45 | The Little Book of Value Investing | Christopher Browne | 版权书 | https://annas-archive.org/search?q=Little+Book+of+Value+Investing | ★★★ |
| 46 | The Dhandho Investor | Mohnish Pabrai | 版权书 | https://annas-archive.org/search?q=Dhandho+Investor+Pabrai | ★★★ |

### 巴菲特 / 芒格 (buffett)

| # | 书名 | 作者 | 类别 | Anna's Archive 检索 | 备注 |
|---|------|------|------|---------------------|------|
| 20 | Seeking Wisdom | Peter Bevelin | 版权书 | https://annas-archive.org/search?q=Seeking+Wisdom+Bevelin | ★★★★ |
| 32 | The Warren Buffett Way | Robert Hagstrom | 版权书 | https://annas-archive.org/search?q=The+Warren+Buffett+Way | ★★★ |
| 33 | Buffett: The Making of an American Capitalist | Roger Lowenstein | 版权书 | https://annas-archive.org/search?q=Buffett+Making+American+Capitalist | ★★★ |
| 34 | Charlie Munger: The Complete Investor | Tren Griffin | 版权书 | https://annas-archive.org/search?q=Charlie+Munger+Complete+Investor | ★★★ |
| 65 | Damn Right! | Janet Lowe | 版权书 | https://annas-archive.org/search?q=Damn+Right+Janet+Lowe+Munger | ★★★ |
| 66 | The Deals of Warren Buffett | Glen Arnold | 版权书 | https://annas-archive.org/search?q=Deals+of+Warren+Buffett+Arnold | ★★★ |

### 大师 (masters)

| # | 书名 | 作者 | 类别 | Anna's Archive 检索 | 备注 |
|---|------|------|------|---------------------|------|
| 6 | The Most Important Thing | Howard Marks | 版权书 | https://annas-archive.org/search?q=The+Most+Important+Thing+Howard+Marks | ★★★★★ |
| 23 | Mastering the Market Cycle | Howard Marks | 版权书 | https://annas-archive.org/search?q=Mastering+the+Market+Cycle | ★★★★ |
| 24 | Beating the Street | Peter Lynch | 版权书 | https://annas-archive.org/search?q=Beating+the+Street+Lynch | ★★★★ |
| 25 | The Outsiders | William Thorndike | 版权书 | https://annas-archive.org/search?q=The+Outsiders+Thorndike | ★★★★ |
| 52 | Richer, Wiser, Happier | William Green | 版权书 | https://annas-archive.org/search?q=Richer+Wiser+Happier+Green | ★★★ |
| 53 | The Education of a Value Investor | Guy Spier | 版权书 | https://annas-archive.org/search?q=Education+of+a+Value+Investor+Spier | ★★★ |
| 54 | The Joys of Compounding | Gautam Baid | 版权书 | https://annas-archive.org/search?q=Joys+of+Compounding+Baid | ★★★ |

### 行为金融 (behavioral)

| # | 书名 | 作者 | 类别 | Anna's Archive 检索 | 备注 |
|---|------|------|------|---------------------|------|
| 7 | The Psychology of Money | Morgan Housel | 版权书 | https://annas-archive.org/search?q=Psychology+of+Money+Housel | ★★★★★ |
| 9 | Thinking, Fast and Slow | Daniel Kahneman | 版权书 | https://annas-archive.org/search?q=Thinking+Fast+and+Slow+Kahneman | ★★★★★ |
| 17 | The Black Swan | Nassim Taleb | 版权书 | https://annas-archive.org/search?q=The+Black+Swan+Taleb | ★★★★★ |
| 48 | Thinking in Bets | Annie Duke | 版权书 | https://annas-archive.org/search?q=Thinking+in+Bets+Duke | ★★★ |
| 49 | The Wisdom of Crowds | James Surowiecki | 版权书 | https://annas-archive.org/search?q=Wisdom+of+Crowds+Surowiecki | ★★★ |
| 50 | Influence | Robert Cialdini | 版权书 | https://annas-archive.org/search?q=Influence+Cialdini | ★★★ |
| 51 | The Little Book of Behavioral Investing | James Montier | 版权书 | https://annas-archive.org/search?q=Little+Book+of+Behavioral+Investing | ★★★ |

### 实战故事 / 金融史 (stories)

| # | 书名 | 作者 | 类别 | Anna's Archive 检索 | 备注 |
|---|------|------|------|---------------------|------|
| 35 | Liar's Poker | Michael Lewis | 版权书 | https://annas-archive.org/search?q=Liar%27s+Poker+Lewis | ★★★★ |
| 37 | Barbarians at the Gate | Burrough & Helyar | 版权书 | https://annas-archive.org/search?q=Barbarians+at+the+Gate | ★★★★ |
| 38 | Too Big to Fail | Andrew Ross Sorkin | 版权书 | https://annas-archive.org/search?q=Too+Big+to+Fail+Sorkin | ★★★ |
| 39 | Manias, Panics, and Crashes | Charles Kindleberger | 版权书 | https://annas-archive.org/search?q=Manias+Panics+and+Crashes | ★★★★ |
| 40 | New Market Wizards | Jack Schwager | 版权书 | https://annas-archive.org/search?q=New+Market+Wizards+Schwager | ★★★★ |
| 41 | Hedge Fund Market Wizards | Jack Schwager | 版权书 | https://annas-archive.org/search?q=Hedge+Fund+Market+Wizards | ★★★★ |
| 63 | Den of Thieves | James Stewart | 版权书 | https://annas-archive.org/search?q=Den+of+Thieves+Stewart | ★★★ |
| 64 | The Smartest Guys in the Room | McLean & Elkind | 版权书 | https://annas-archive.org/search?q=Smartest+Guys+in+the+Room | ★★★ |

### 量化 / 组合管理 (quant)

| # | 书名 | 作者 | 类别 | Anna's Archive 检索 | 备注 |
|---|------|------|------|---------------------|------|
| 27 | The Alchemy of Finance | George Soros | 版权书 | https://annas-archive.org/search?q=Alchemy+of+Finance+Soros | ★★★★ |
| 29 | Active Portfolio Management | Grinold & Kahn | 版权书 | https://annas-archive.org/search?q=Active+Portfolio+Management+Grinold | ★★★★ |
| 55 | Quantitative Value | Wesley Gray | 版权书 | https://annas-archive.org/search?q=Quantitative+Value+Gray | ★★★ |
| 56 | Quantitative Momentum | Wesley Gray | 版权书 | https://annas-archive.org/search?q=Quantitative+Momentum+Gray | ★★★ |
| 57 | The Complete Guide to Factor-Based Investing | Berkin & Swedroe | 版权书 | https://annas-archive.org/search?q=Complete+Guide+to+Factor-Based+Investing | ★★★ |
| 58 | The Intelligent Asset Allocator | William Bernstein | 版权书 | https://annas-archive.org/search?q=Intelligent+Asset+Allocator+Bernstein | ★★★ |

### 宏观 / 经济 (macro)

| # | 书名 | 作者 | 类别 | Anna's Archive 检索 | 备注 |
|---|------|------|------|---------------------|------|
| 42 | Against the Gods | Peter Bernstein | 版权书 | https://annas-archive.org/search?q=Against+the+Gods+Bernstein | ★★★★ |
| 43 | Capital Ideas Evolving | Peter Bernstein | 版权书 | https://annas-archive.org/search?q=Capital+Ideas+Evolving | ★★★ |
| 44 | Capital Ideas | Peter Bernstein | 版权书 | https://annas-archive.org/search?q=Capital+Ideas+Bernstein | ★★★★ |
| 61 | The Myth of the Rational Market | Justin Fox | 版权书 | https://annas-archive.org/search?q=Myth+of+the+Rational+Market+Fox | ★★★ |
| 62 | A History of Interest Rates | Sidney Homer | 版权书 | https://annas-archive.org/search?q=A+History+of+Interest+Rates+Homer | ★★★ |

### 其他 (other)

| # | 书名 | 作者 | 类别 | Anna's Archive 检索 | 备注 |
|---|------|------|------|---------------------|------|
| 69 | Where Are the Customers' Yachts? | Fred Schwed | 版权书 | https://annas-archive.org/search?q=Where+Are+the+Customers+Yachts | ★★★。1940 出版，版权已续展，尚未进入公版。 |

---

## 三、行动建议

**立刻可下载（免费合法）的 4 本：**

- `#3` **Poor Charlie's Almanack**（★★★★★，官方免费）→ stripe.press 网页阅读器
- `#10` **Reminiscences of a Stock Operator**（★★★★★，公版）→ Gutenberg #60979
- `#22` **Berkshire Hathaway Letters to Shareholders**（★★★★★，官方免费）→ berkshirehathaway.com
- `#70` **Think and Grow Rich**（★★★，公版）→ archive.org / sacred-texts

其中 #3、#10、#22 是 ★★★★★ 高优先级，应**最先入库**。

**脚本现状：** `scripts/download_free_books.py` 已自动处理公版/官方免费两类，即 #10 与 #22。
但 #10 用的 Gutenberg URL 是旧 ID `9840`（现已指向其他书），**需更正为 60979**
（如 `https://www.gutenberg.org/ebooks/60979.txt.utf-8` 或 EPUB）。
#3（Stripe Press 网页全文）与 #70（archive.org 公版）目前脚本未覆盖，可手动获取或扩展脚本。

**其余 45 本** 全部为在版权期内的商业出版物，GitHub 与公版渠道均无；
请通过正版购买 / 图书馆电子借阅，或在个人研究合理使用范围内通过 Anna's Archive 等聚合站自行检索获取。
高优先级（★★★★★）的版权书待补：#6 The Most Important Thing、
#7 The Psychology of Money、#9 Thinking, Fast and Slow、#15 Margin of Safety、
#17 The Black Swan、#21 Security Analysis。
