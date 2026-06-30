#!/usr/bin/env python3
"""
按优先级批量入库投资经典书籍。

用法：
  python scripts/batch_ingest_books.py           # 正式入库
  python scripts/batch_ingest_books.py --dry-run # 只列出匹配情况，不实际入库
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

BOOKS_DIR = ROOT / "books"
RAG_DB    = ROOT / "output" / "rag.db"

# (priority, glob_pattern, title, author, importance_stars, category)
BOOK_CATALOG = [
    (1,  "The Intelligent Investor*",          "The Intelligent Investor",                     "Benjamin Graham",        5, "value"),
    (2,  "The Essays of Warren Buffett*",      "The Essays of Warren Buffett",                 "Warren Buffett",         5, "buffett"),
    (3,  "Poor Charlie*",                      "Poor Charlie's Almanack",                      "Charlie Munger",         5, "buffett"),
    (4,  "Common Stocks*",                     "Common Stocks and Uncommon Profits",           "Philip Fisher",          5, "value"),
    (5,  "One Up On Wall Street*",             "One Up On Wall Street",                        "Peter Lynch",            5, "masters"),
    (6,  "The Most Important Thing*",          "The Most Important Thing",                     "Howard Marks",           5, "masters"),
    (7,  "The Psychology of Money*",           "The Psychology of Money",                      "Morgan Housel",          5, "behavioral"),
    (8,  "A Random Walk*",                     "A Random Walk Down Wall Street",               "Burton Malkiel",         5, "behavioral"),
    (9,  "Thinking*Fast*",                     "Thinking, Fast and Slow",                      "Daniel Kahneman",        5, "behavioral"),
    (10, "Reminiscences*",                     "Reminiscences of a Stock Operator",            "Edwin Lefèvre",          5, "stories"),
    (11, "Market Wizards*",                    "Market Wizards",                               "Jack Schwager",          5, "stories"),
    (12, "The Snowball*",                      "The Snowball",                                 "Alice Schroeder",        5, "buffett"),
    (13, "When Genius Failed*",                "When Genius Failed",                           "Roger Lowenstein",       5, "stories"),
    (14, "The Big Short*",                     "The Big Short",                                "Michael Lewis",          5, "stories"),
    (15, "Margin of Safety*",                  "Margin of Safety",                             "Seth Klarman",           5, "value"),
    (16, "Fooled by Randomness*",              "Fooled by Randomness",                         "Nassim Taleb",           5, "behavioral"),
    (17, "The Black Swan*",                    "The Black Swan",                               "Nassim Taleb",           5, "behavioral"),
    (18, "What Works on Wall Street*",         "What Works on Wall Street",                    "James O'Shaughnessy",    4, "quant"),
    (19, "The Little Book of Common Sense*",   "The Little Book of Common Sense Investing",    "John Bogle",             5, "index"),
    (20, "Seeking Wisdom*",                    "Seeking Wisdom",                               "Peter Bevelin",          4, "buffett"),
    (21, "Security Analysis*",                 "Security Analysis",                            "Benjamin Graham",        5, "value"),
    (22, "Berkshire*Letters*",                 "Berkshire Hathaway Letters to Shareholders",   "Warren Buffett",         5, "buffett"),
    (23, "Mastering the Market Cycle*",        "Mastering the Market Cycle",                   "Howard Marks",           4, "masters"),
    (24, "Beating the Street*",                "Beating the Street",                           "Peter Lynch",            4, "masters"),
    (25, "The Outsiders*",                     "The Outsiders",                                "William Thorndike",      4, "masters"),
    (26, "Irrational Exuberance*",             "Irrational Exuberance",                        "Robert Shiller",         4, "behavioral"),
    (27, "The Alchemy of Finance*",            "The Alchemy of Finance",                       "George Soros",           4, "quant"),
    (28, "Stocks*Long Run*",                   "Stocks for the Long Run",                      "Jeremy Siegel",          4, "quant"),
    (29, "Active Portfolio Management*",       "Active Portfolio Management",                  "Grinold & Kahn",         4, "quant"),
    (30, "The Four Pillars*",                  "The Four Pillars of Investing",                "William Bernstein",      4, "quant"),
    (31, "Value Investing*Graham*Buffett*",    "Value Investing: From Graham to Buffett",      "Bruce Greenwald",        4, "value"),
    (32, "The Warren Buffett Way*",            "The Warren Buffett Way",                       "Robert Hagstrom",        3, "buffett"),
    (33, "Buffett*Making*",                    "Buffett: The Making of an American Capitalist","Roger Lowenstein",       3, "buffett"),
    (34, "Charlie Munger*Complete*",           "Charlie Munger: The Complete Investor",        "Tren Griffin",           3, "buffett"),
    (35, "Liar*Poker*",                        "Liar's Poker",                                 "Michael Lewis",          4, "stories"),
    (36, "Flash Boys*",                        "Flash Boys",                                   "Michael Lewis",          4, "stories"),
    (37, "Barbarians*Gate*",                   "Barbarians at the Gate",                       "Burrough & Helyar",      4, "stories"),
    (38, "Too Big to Fail*",                   "Too Big to Fail",                              "Andrew Ross Sorkin",     3, "stories"),
    (39, "Manias*Panics*",                     "Manias, Panics, and Crashes",                  "Charles Kindleberger",   4, "stories"),
    (40, "New Market Wizards*",                "New Market Wizards",                           "Jack Schwager",          4, "stories"),
    (41, "Hedge Fund Market Wizards*",         "Hedge Fund Market Wizards",                    "Jack Schwager",          4, "stories"),
    (42, "Against the Gods*",                  "Against the Gods",                             "Peter Bernstein",        4, "macro"),
    (43, "Capital Ideas*Evolving*",            "Capital Ideas Evolving",                       "Peter Bernstein",        3, "macro"),
    (44, "Capital Ideas*",                     "Capital Ideas",                                "Peter Bernstein",        4, "macro"),
    (45, "The Little Book*Value*",             "The Little Book of Value Investing",           "Christopher Browne",     3, "value"),
    (46, "The Dhandho*",                       "The Dhandho Investor",                         "Mohnish Pabrai",         3, "value"),
    (47, "The Little Book*Beats*",             "The Little Book That Still Beats the Market",  "Joel Greenblatt",        4, "value"),
    (48, "Thinking in Bets*",                  "Thinking in Bets",                             "Annie Duke",             3, "behavioral"),
    (49, "The Wisdom of Crowds*",              "The Wisdom of Crowds",                         "James Surowiecki",       3, "behavioral"),
    (50, "Influence*",                         "Influence: The Psychology of Persuasion",      "Robert Cialdini",        3, "behavioral"),
    (51, "The Little Book*Behavioral*",        "The Little Book of Behavioral Investing",      "James Montier",          3, "behavioral"),
    (52, "Richer*Wiser*",                      "Richer, Wiser, Happier",                       "William Green",          3, "masters"),
    (53, "The Education*Value*",               "The Education of a Value Investor",            "Guy Spier",              3, "masters"),
    (54, "The Joys of Compounding*",           "The Joys of Compounding",                      "Gautam Baid",            3, "masters"),
    (55, "Quantitative Value*",                "Quantitative Value",                           "Wesley Gray",            3, "quant"),
    (56, "Quantitative Momentum*",             "Quantitative Momentum",                        "Wesley Gray",            3, "quant"),
    (57, "Factor*Investing*",                  "The Complete Guide to Factor-Based Investing", "Berkin & Swedroe",       3, "quant"),
    (58, "The Intelligent Asset*",             "The Intelligent Asset Allocator",              "William Bernstein",      3, "quant"),
    (59, "Common Sense on Mutual*",            "Common Sense on Mutual Funds",                 "John Bogle",             4, "index"),
    (60, "Bogleheads*",                        "Bogleheads' Guide to Investing",               "Larimore et al",         4, "index"),
    (61, "The Myth*Rational*",                 "The Myth of the Rational Market",              "Justin Fox",             3, "macro"),
    (62, "A History of Interest*",             "A History of Interest Rates",                  "Sidney Homer",           3, "macro"),
    (63, "Den of Thieves*",                    "Den of Thieves",                               "James Stewart",          3, "stories"),
    (64, "Smartest Guys*",                     "The Smartest Guys in the Room",                "McLean & Elkind",        3, "stories"),
    (65, "Damn Right*",                        "Damn Right!",                                  "Janet Lowe",             3, "buffett"),
    (66, "Deals of Warren*",                   "The Deals of Warren Buffett",                  "Glen Arnold",            3, "buffett"),
    (67, "Principles*",                        "Principles: Life and Work",                    "Ray Dalio",              4, "other"),
    (68, "The Richest Man*",                   "The Richest Man in Babylon",                   "George Clason",          4, "other"),
    (69, "Where Are the Customers*",           "Where Are the Customers' Yachts?",             "Fred Schwed",            3, "other"),
    (70, "Think and Grow Rich*",               "Think and Grow Rich",                          "Napoleon Hill",          3, "other"),
    (71, "MONEY Master*",                      "MONEY Master the Game",                        "Tony Robbins",           3, "other"),
    (72, "The Millionaire Next Door*",         "The Millionaire Next Door",                    "Thomas Stanley",         3, "other"),
]


def _file_hash(path: Path) -> str:
    """计算 PDF 的 MD5（与 rag.py ingest_pdf 内部算法保持一致）。"""
    md5 = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            md5.update(chunk)
    return md5.hexdigest()[:16]


def _glob_ci(directory: Path, pattern: str) -> list[Path]:
    """
    大小写不敏感 glob（macOS 文件系统默认不敏感，但 Python glob 区分大小写）。
    先尝试原始 pattern，找不到则把 pattern 全部小写后匹配小写文件名。
    """
    # 直接 glob（macOS HFS+ 默认大小写不敏感，通常能命中）
    results = sorted(directory.glob(pattern))
    if results:
        return results

    # 备选：手动大小写不敏感匹配
    pat_lower = pattern.lower().rstrip("*")
    fallback = [
        p for p in sorted(directory.iterdir())
        if p.suffix.lower() == ".pdf" and p.name.lower().startswith(pat_lower)
    ]
    return fallback


def _stars(n: int, total: int = 5) -> str:
    return "★" * n + "☆" * (total - n)


def scan_catalog() -> tuple[list[tuple], list[tuple]]:
    """扫描书目，返回 (matched, missing)。"""
    matched: list[tuple] = []   # (priority, path, title, author, importance, category)
    missing: list[tuple] = []   # (priority, title, author, importance, category)

    for priority, pattern, title, author, importance, category in BOOK_CATALOG:
        files = _glob_ci(BOOKS_DIR, pattern)
        if files:
            matched.append((priority, files[0], title, author, importance, category))
            print(f"[{priority:2d}] {_stars(importance)}  {title}")
            print(f"          -> {files[0].name}")
        else:
            missing.append((priority, title, author, importance, category))
            print(f"[{priority:2d}] {_stars(importance)}  {title} — {author}  [缺失]")

    return matched, missing


def _update_meta(conn: sqlite3.Connection, file_hash: str,
                 priority: int, importance: int, category: str) -> int:
    """
    对该书所有 chunks 的 extra_meta 追加 priority/importance/category。
    source_id 格式为 "pdf:{file_hash}:p{start}-{end}"，用 LIKE 匹配。
    """
    sid_prefix = f"pdf:{file_hash}%"
    rows = conn.execute(
        "SELECT id, extra_meta FROM chunks WHERE source_id LIKE ?",
        (sid_prefix,),
    ).fetchall()
    if not rows:
        return 0
    for chunk_id, meta_json in rows:
        meta = json.loads(meta_json or "{}")
        meta.update({"priority": priority, "importance": importance, "category": category})
        conn.execute(
            "UPDATE chunks SET extra_meta=? WHERE id=?",
            (json.dumps(meta, ensure_ascii=False), chunk_id),
        )
    conn.commit()
    return len(rows)


def main() -> None:
    dry_run = "--dry-run" in sys.argv

    if not BOOKS_DIR.exists():
        print(f"错误：books/ 目录不存在（{BOOKS_DIR}）")
        sys.exit(1)

    print(f"{'[DRY RUN] ' if dry_run else ''}扫描 books/ 目录...\n")

    matched, missing = scan_catalog()

    print(f"\n已有：{len(matched)} 本  缺失：{len(missing)} 本")

    if dry_run:
        print("\n（dry-run 模式，跳过入库）")
        return

    if not matched:
        print("\n没有找到任何书籍文件，退出。")
        return

    print("\n开始按优先级入库...\n")
    from x_agent.rag import ingest_pdf  # 延迟导入，dry-run 时不需要

    # 只有 rag.db 存在时才打开连接更新 meta；否则 ingest_pdf 会自动创建
    rag_db_exists = RAG_DB.exists()
    conn: sqlite3.Connection | None = None
    if rag_db_exists:
        conn = sqlite3.connect(str(RAG_DB))

    ok = skipped = failed = 0

    for priority, pdf_path, title, author, importance, category in matched:
        stars = _stars(importance)
        print(f"[{priority:2d}] {stars}  {title}  ({author})")
        print(f"      文件：{pdf_path.name}")
        try:
            # skip_vectors=True：只写 SQLite/FTS，向量留给最后统一 `python -m x_agent.rag embed-all`
            n = ingest_pdf(str(pdf_path), title=title, author=author,
                           source_type="book", skip_vectors=True)

            # ingest_pdf 返回 0 表示已入库（跳过）；>0 表示新增
            if n > 0:
                print(f"      入库 {n} 块", end="")
                # 更新 extra_meta：用与 rag.py 相同的算法计算 file_hash
                fh = _file_hash(pdf_path)
                # ingest_pdf 完成后 rag.db 必然存在
                if conn is None:
                    conn = sqlite3.connect(str(RAG_DB))
                updated = _update_meta(conn, fh, priority, importance, category)
                print(f"，meta 已更新 {updated} 块（priority={priority}，importance={importance}，category={category}）")
                ok += 1
            else:
                # 已存在，仍尝试补写 meta（支持重跑）
                fh = _file_hash(pdf_path)
                if conn is None and RAG_DB.exists():
                    conn = sqlite3.connect(str(RAG_DB))
                if conn is not None:
                    updated = _update_meta(conn, fh, priority, importance, category)
                    if updated:
                        print(f"      跳过（已入库），meta 补写 {updated} 块")
                    else:
                        print(f"      跳过（已入库）")
                else:
                    print(f"      跳过（已入库）")
                skipped += 1

        except Exception as e:
            print(f"      失败: {e}")
            failed += 1

        print()

    if conn is not None:
        conn.close()

    print("=" * 60)
    print(f"完成：入库 {ok} 本，跳过 {skipped} 本，失败 {failed} 本")
    if missing:
        print(f"缺失 {len(missing)} 本（运行 --dry-run 查看详细列表）")


if __name__ == "__main__":
    main()
