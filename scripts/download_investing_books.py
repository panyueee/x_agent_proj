"""
下载 manjunath5496/Best-Investing-Books 的 25 本英文投资经典 PDF。
下载后自动重命名为书名，保存到 books/ 目录，然后批量入库 RAG。

用法：
    python scripts/download_investing_books.py           # 下载全部
    python scripts/download_investing_books.py --dry-run # 仅列出，不下载
    python scripts/download_investing_books.py --ingest  # 下载后立即入库
"""
from __future__ import annotations

import argparse
import ssl
import sys
import time
import urllib.request
from pathlib import Path

# macOS Python 3.x 常见：系统 SSL 证书未安装，跳过验证即可（仅用于下载）
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

ROOT      = Path(__file__).parent.parent
BOOKS_DIR = ROOT / "books"

# inv(N).pdf → 书名（按 README 顺序）
BOOKS = [
    (1,  "The Intelligent Investor - Benjamin Graham"),
    (2,  "One Up On Wall Street - Peter Lynch"),
    (3,  "A Random Walk Down Wall Street - Burton Malkiel"),
    (4,  "The Essays of Warren Buffett"),
    (5,  "The Little Book of Common Sense Investing - John Bogle"),
    (6,  "Common Sense on Mutual Funds - John Bogle"),
    (7,  "How to Make Money in Stocks - William O'Neil"),
    (8,  "Stocks For The Long Run - Jeremy Siegel"),
    (9,  "Common Stocks and Uncommon Profits - Philip Fisher"),
    (10, "Fooled by Randomness - Nassim Taleb"),
    (11, "Market Wizards - Jack Schwager"),
    (12, "The Millionaire Next Door"),
    (13, "The Little Book That Still Beats the Market - Joel Greenblatt"),
    (14, "The Snowball - Warren Buffett and the Business of Life"),
    (15, "The Four Pillars of Investing - William Bernstein"),
    (16, "The Richest Man In Babylon - George Clason"),
    (17, "When Genius Failed - Roger Lowenstein"),
    (18, "Flash Boys - Michael Lewis"),
    (19, "Irrational Exuberance - Robert Shiller"),
    (20, "Stock Investing For Dummies"),
    (21, "Bogleheads Guide to Investing"),
    (22, "The Big Short - Michael Lewis"),
    (23, "What Works on Wall Street - James O'Shaughnessy"),
    (24, "MONEY Master the Game - Tony Robbins"),
    (25, "Principles Life and Work - Ray Dalio"),
]

BASE_URL = (
    "https://github.com/manjunath5496/Best-Investing-Books/raw/master/inv({n}).pdf"
)


def download_book(n: int, title: str, dry_run: bool = False) -> Path | None:
    dest = BOOKS_DIR / f"{title}.pdf"
    if dest.exists():
        print(f"  ⏭  [{n:02d}] {title}.pdf  已存在，跳过")
        return dest

    url = BASE_URL.format(n=n)
    if dry_run:
        print(f"  🔍 [{n:02d}] {title}.pdf  ← {url}")
        return None

    print(f"  ⬇  [{n:02d}] {title}.pdf", end="", flush=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60, context=_SSL_CTX) as resp:
            data = resp.read()
        dest.write_bytes(data)
        print(f"  ({len(data)//1024} KB)")
        return dest
    except Exception as e:
        print(f"  ❌ 失败：{e}")
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="仅列出，不下载")
    parser.add_argument("--ingest", action="store_true", help="下载后自动入库 RAG")
    parser.add_argument("--start", type=int, default=1, help="从第 N 本开始（默认 1）")
    args = parser.parse_args()

    BOOKS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"📂 保存目录：{BOOKS_DIR}")
    print(f"📚 共 {len(BOOKS)} 本，开始{'（演习模式）' if args.dry_run else '下载'}...\n")

    downloaded: list[Path] = []
    for n, title in BOOKS:
        if n < args.start:
            continue
        path = download_book(n, title, dry_run=args.dry_run)
        if path and path.exists() and not args.dry_run:
            downloaded.append(path)
        if not args.dry_run:
            time.sleep(0.5)  # 礼貌性限速

    print(f"\n✅ 下载完成：{len(downloaded)} 本")

    if args.ingest and downloaded:
        print("\n🗄  开始入库 RAG...")
        sys.path.insert(0, str(ROOT))
        from x_agent.rag import ingest_pdf
        ok = skipped = failed = 0
        for path in downloaded:
            try:
                n = ingest_pdf(str(path))
                if n > 0:
                    print(f"  ✅ {path.name}  新增 {n} 块")
                    ok += 1
                else:
                    print(f"  ⏭  {path.name}  已入库，跳过")
                    skipped += 1
            except Exception as e:
                print(f"  ❌ {path.name}  {e}")
                failed += 1
        print(f"\n入库完成：新增 {ok} 本 / 跳过 {skipped} 本 / 失败 {failed} 本")


if __name__ == "__main__":
    main()
