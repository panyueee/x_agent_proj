# 投资经典书籍入库状态

> 更新日期：2026-06-30  
> books/ 目录共有 PDF：27 个（含非书单 PDF 4 本：其中《超预测》单独 OCR 入库，其余 3 本暂未入库）  
> 书单命中并已入库 RAG 知识库：23 本 ✅  缺失：49 本 ❌  
> 另：《超预测》(Superforecasting) 为扫描版中文书，正在单独走 OCR 流程入库（详见下方说明）

入库方式：`python scripts/batch_ingest_books.py` 按 `BOOK_CATALOG` 优先级把命中文件
写入 SQLite + FTS5（`skip_vectors=True`，不调用向量 API）。向量层由
`python -m x_agent.rag embed-all` 统一生成（需 `VOYAGE_API_KEY`，当前未设置，故暂未生成向量，
检索自动降级为 BM25 + FTS5）。三层架构详见 [`rag_architecture.md`](rag_architecture.md)。

---

## 已入库书籍（书单命中 23 本，已写入 RAG 知识库）

| # | 优先级 | 重要性 | 书名 | 作者 | 分类 | 文件名 |
|---|--------|--------|------|------|------|--------|
| 1 | 1 | ★★★★★ | The Intelligent Investor | Benjamin Graham | value | The Intelligent Investor - Benjamin Graham.pdf |
| 2 | 2 | ★★★★★ | The Essays of Warren Buffett | Warren Buffett | buffett | The Essays of Warren Buffett.pdf |
| 3 | 4 | ★★★★★ | Common Stocks and Uncommon Profits | Philip Fisher | value | Common Stocks and Uncommon Profits - Philip Fisher.pdf |
| 4 | 5 | ★★★★★ | One Up On Wall Street | Peter Lynch | masters | One Up On Wall Street - Peter Lynch.pdf |
| 5 | 8 | ★★★★★ | A Random Walk Down Wall Street | Burton Malkiel | behavioral | A Random Walk Down Wall Street - Burton Malkiel.pdf |
| 6 | 11 | ★★★★★ | Market Wizards | Jack Schwager | stories | Market Wizards - Jack Schwager.pdf |
| 7 | 12 | ★★★★★ | The Snowball | Alice Schroeder | buffett | The Snowball - Warren Buffett and the Business of Life.pdf |
| 8 | 13 | ★★★★★ | When Genius Failed | Roger Lowenstein | stories | When Genius Failed - Roger Lowenstein.pdf |
| 9 | 14 | ★★★★★ | The Big Short | Michael Lewis | stories | The Big Short - Michael Lewis.pdf |
| 10 | 16 | ★★★★★ | Fooled by Randomness | Nassim Taleb | behavioral | Fooled by Randomness - Nassim Taleb.pdf |
| 11 | 18 | ★★★★☆ | What Works on Wall Street | James O'Shaughnessy | quant | What Works on Wall Street - James O'Shaughnessy.pdf |
| 12 | 19 | ★★★★★ | The Little Book of Common Sense Investing | John Bogle | index | The Little Book of Common Sense Investing - John Bogle.pdf |
| 13 | 26 | ★★★★☆ | Irrational Exuberance | Robert Shiller | behavioral | Irrational Exuberance - Robert Shiller.pdf |
| 14 | 28 | ★★★★☆ | Stocks for the Long Run | Jeremy Siegel | quant | Stocks For The Long Run - Jeremy Siegel.pdf |
| 15 | 30 | ★★★★☆ | The Four Pillars of Investing | William Bernstein | quant | The Four Pillars of Investing - William Bernstein.pdf |
| 16 | 36 | ★★★★☆ | Flash Boys | Michael Lewis | stories | Flash Boys - Michael Lewis.pdf |
| 17 | 47 | ★★★★☆ | The Little Book That Still Beats the Market | Joel Greenblatt | value | The Little Book That Still Beats the Market - Joel Greenblatt.pdf |
| 18 | 59 | ★★★★☆ | Common Sense on Mutual Funds | John Bogle | index | Common Sense on Mutual Funds - John Bogle.pdf |
| 19 | 60 | ★★★★☆ | Bogleheads' Guide to Investing | Larimore et al | index | Bogleheads Guide to Investing.pdf |
| 20 | 67 | ★★★★☆ | Principles: Life and Work | Ray Dalio | other | Principles Life and Work - Ray Dalio.pdf |
| 21 | 68 | ★★★★☆ | The Richest Man in Babylon | George Clason | other | The Richest Man In Babylon - George Clason.pdf |
| 22 | 71 | ★★★☆☆ | MONEY Master the Game | Tony Robbins | other | MONEY Master the Game - Tony Robbins.pdf |
| 23 | 72 | ★★★☆☆ | The Millionaire Next Door | Thomas Stanley | other | The Millionaire Next Door.pdf |

> 注：以上 23 本均已通过 `scripts/batch_ingest_books.py` 入库（SQLite + FTS5 层）。

### 扫描版中文书（单独 OCR 入库）

- `超预测.pdf`（《超预测》/ Superforecasting，作者 Philip Tetlock）— 扫描件，无文本层，
  正走 macOS Vision OCR 流程（`scripts/ocr_worker.py` 子进程）逐页识别，结果缓存于
  `output/ocr_cache/<file_hash>.jsonl` 后分词写库。不在 72 本英文书单内，单独入库为 `source_type="book"`。

### books/ 中不在书单、暂未入库的 PDF（3 个）

> 这 3 个文件不在 `BOOK_CATALOG` 内，`batch_ingest_books.py` 不会处理；
> 如需入库可手动执行 `python -m x_agent.rag ingest <文件> --type book`。

- `How to Make Money in Stocks - William O'Neil.pdf`
- `Stock Investing For Dummies.pdf`
- `教材：A2金融数学.pdf`

---

## 缺失书籍（49 本）

推荐下载渠道：
- **Anna's Archive**（annas-archive.org）— 英文原版最全，覆盖率最高
- **Project Gutenberg**（gutenberg.org）— 公版书免费下载（Reminiscences of a Stock Operator 已是公版）
- **Z-Library 镜像**（zlibrary）— 备选
- **豆瓣读书 / 微信读书**— 中文版参考

| 优先级 | 重要性 | 书名 | 作者 | 分类 | 推荐渠道 |
|--------|--------|------|------|------|----------|
| 3 | ★★★★★ | Poor Charlie's Almanack | Charlie Munger | buffett | Anna's Archive |
| 6 | ★★★★★ | The Most Important Thing | Howard Marks | masters | Anna's Archive |
| 7 | ★★★★★ | The Psychology of Money | Morgan Housel | behavioral | Anna's Archive |
| 9 | ★★★★★ | Thinking, Fast and Slow | Daniel Kahneman | behavioral | Anna's Archive |
| 10 | ★★★★★ | Reminiscences of a Stock Operator | Edwin Lefèvre | stories | Project Gutenberg（公版） |
| 15 | ★★★★★ | Margin of Safety | Seth Klarman | value | Anna's Archive（稀有，价格极高） |
| 17 | ★★★★★ | The Black Swan | Nassim Taleb | behavioral | Anna's Archive |
| 20 | ★★★★☆ | Seeking Wisdom | Peter Bevelin | buffett | Anna's Archive |
| 21 | ★★★★★ | Security Analysis | Benjamin Graham | value | Anna's Archive |
| 22 | ★★★★★ | Berkshire Hathaway Letters to Shareholders | Warren Buffett | buffett | Berkshire 官网免费（berkshirehathaway.com） |
| 23 | ★★★★☆ | Mastering the Market Cycle | Howard Marks | masters | Anna's Archive |
| 24 | ★★★★☆ | Beating the Street | Peter Lynch | masters | Anna's Archive |
| 25 | ★★★★☆ | The Outsiders | William Thorndike | masters | Anna's Archive |
| 27 | ★★★★☆ | The Alchemy of Finance | George Soros | quant | Anna's Archive |
| 29 | ★★★★☆ | Active Portfolio Management | Grinold & Kahn | quant | Anna's Archive |
| 31 | ★★★★☆ | Value Investing: From Graham to Buffett | Bruce Greenwald | value | Anna's Archive |
| 32 | ★★★☆☆ | The Warren Buffett Way | Robert Hagstrom | buffett | Anna's Archive |
| 33 | ★★★☆☆ | Buffett: The Making of an American Capitalist | Roger Lowenstein | buffett | Anna's Archive |
| 34 | ★★★☆☆ | Charlie Munger: The Complete Investor | Tren Griffin | buffett | Anna's Archive |
| 35 | ★★★★☆ | Liar's Poker | Michael Lewis | stories | Anna's Archive |
| 37 | ★★★★☆ | Barbarians at the Gate | Burrough & Helyar | stories | Anna's Archive |
| 38 | ★★★☆☆ | Too Big to Fail | Andrew Ross Sorkin | stories | Anna's Archive |
| 39 | ★★★★☆ | Manias, Panics, and Crashes | Charles Kindleberger | stories | Anna's Archive |
| 40 | ★★★★☆ | New Market Wizards | Jack Schwager | stories | Anna's Archive |
| 41 | ★★★★☆ | Hedge Fund Market Wizards | Jack Schwager | stories | Anna's Archive |
| 42 | ★★★★☆ | Against the Gods | Peter Bernstein | macro | Anna's Archive |
| 43 | ★★★☆☆ | Capital Ideas Evolving | Peter Bernstein | macro | Anna's Archive |
| 44 | ★★★★☆ | Capital Ideas | Peter Bernstein | macro | Anna's Archive |
| 45 | ★★★☆☆ | The Little Book of Value Investing | Christopher Browne | value | Anna's Archive |
| 46 | ★★★☆☆ | The Dhandho Investor | Mohnish Pabrai | value | Anna's Archive |
| 48 | ★★★☆☆ | Thinking in Bets | Annie Duke | behavioral | Anna's Archive |
| 49 | ★★★☆☆ | The Wisdom of Crowds | James Surowiecki | behavioral | Anna's Archive |
| 50 | ★★★☆☆ | Influence: The Psychology of Persuasion | Robert Cialdini | behavioral | Anna's Archive |
| 51 | ★★★☆☆ | The Little Book of Behavioral Investing | James Montier | behavioral | Anna's Archive |
| 52 | ★★★☆☆ | Richer, Wiser, Happier | William Green | masters | Anna's Archive |
| 53 | ★★★☆☆ | The Education of a Value Investor | Guy Spier | masters | Anna's Archive |
| 54 | ★★★☆☆ | The Joys of Compounding | Gautam Baid | masters | Anna's Archive |
| 55 | ★★★☆☆ | Quantitative Value | Wesley Gray | quant | Anna's Archive |
| 56 | ★★★☆☆ | Quantitative Momentum | Wesley Gray | quant | Anna's Archive |
| 57 | ★★★☆☆ | The Complete Guide to Factor-Based Investing | Berkin & Swedroe | quant | Anna's Archive |
| 58 | ★★★☆☆ | The Intelligent Asset Allocator | William Bernstein | quant | Anna's Archive |
| 61 | ★★★☆☆ | The Myth of the Rational Market | Justin Fox | macro | Anna's Archive |
| 62 | ★★★☆☆ | A History of Interest Rates | Sidney Homer | macro | Anna's Archive |
| 63 | ★★★☆☆ | Den of Thieves | James Stewart | stories | Anna's Archive |
| 64 | ★★★☆☆ | The Smartest Guys in the Room | McLean & Elkind | stories | Anna's Archive |
| 65 | ★★★☆☆ | Damn Right! | Janet Lowe | buffett | Anna's Archive |
| 66 | ★★★☆☆ | The Deals of Warren Buffett | Glen Arnold | buffett | Anna's Archive |
| 69 | ★★★☆☆ | Where Are the Customers' Yachts? | Fred Schwed | other | Anna's Archive |
| 70 | ★★★☆☆ | Think and Grow Rich | Napoleon Hill | other | Project Gutenberg（公版） |

---

## 高优先缺书（★★★★★，priority ≤ 22）

以下是书单中重要性最高且尚未入库的书，建议优先补全：

1. **Poor Charlie's Almanack**（Charlie Munger）— 芒格智慧合集，珍稀版本
2. **The Most Important Thing**（Howard Marks）— 价值投资核心原则
3. **The Psychology of Money**（Morgan Housel）— 近年最畅销行为金融学读物
4. **Thinking, Fast and Slow**（Daniel Kahneman）— 认知心理学经典，诺贝尔奖著作
5. **Reminiscences of a Stock Operator**（Edwin Lefèvre）— 公版书，可从 Project Gutenberg 免费下载
6. **Margin of Safety**（Seth Klarman）— 价值投资必读，实体书市价数千美元
7. **The Black Swan**（Nassim Taleb）— 黑天鹅事件理论
8. **Security Analysis**（Benjamin Graham）— 价值投资圣经
9. **Berkshire Hathaway Letters to Shareholders**（Warren Buffett）— 可从 berkshirehathaway.com 官网免费下载年报合集

---

*此文件由 scripts/batch_ingest_books.py --dry-run 辅助生成，请定期更新。*
