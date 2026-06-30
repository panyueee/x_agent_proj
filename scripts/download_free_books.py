"""
下载公版书 + 官方免费资料，放入 books/ 目录。

可自动下载：
  - Reminiscences of a Stock Operator（1923，公版）— Project Gutenberg
  - Berkshire Hathaway Letters to Shareholders（巴菲特官网免费）

其余版权书需手动从 Anna's Archive / Z-Library 下载。
"""
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT      = Path(__file__).parent.parent
BOOKS_DIR = ROOT / "books"
BOOKS_DIR.mkdir(exist_ok=True)


def download(url: str, dest: Path, desc: str) -> bool:
    if dest.exists():
        print(f"  ✅ 已存在，跳过：{dest.name}")
        return True
    print(f"  下载中：{desc}")
    print(f"    {url}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as f:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // total
                    print(f"\r    {pct}% ({downloaded//1024}KB/{total//1024}KB)", end="", flush=True)
        print(f"\n  ✅ 完成：{dest.name}")
        return True
    except Exception as e:
        print(f"\n  ❌ 失败：{e}")
        if dest.exists():
            dest.unlink()
        return False


# ── 1. Reminiscences of a Stock Operator（公版）────────────────────────────────
print("\n[1/2] Reminiscences of a Stock Operator（Edwin Lefèvre，1923，公版）")
download(
    url  = "https://www.gutenberg.org/files/9840/9840-pdf.pdf",
    dest = BOOKS_DIR / "Reminiscences of a Stock Operator - Edwin Lefevre.pdf",
    desc = "Reminiscences of a Stock Operator",
)

# ── 2. Berkshire Hathaway Letters（巴菲特官方免费）────────────────────────────
# 官网每封信是独立 PDF，用 pypdf 合并成一本
print("\n[2/2] Berkshire Hathaway Letters to Shareholders（1977-2023，官方免费）")

LETTER_YEARS = list(range(1977, 2024))   # 1977-2023
LETTER_URLS  = {
    **{yr: f"https://www.berkshirehathaway.com/letters/{yr}ltr.pdf"
       for yr in range(1977, 1997)},
    **{yr: f"https://www.berkshirehathaway.com/letters/{yr}ltr.pdf"
       for yr in range(1997, 2024)},
}

letters_dir = ROOT / "output" / "bh_letters_raw"
letters_dir.mkdir(parents=True, exist_ok=True)

downloaded_letters = []
for yr in LETTER_YEARS:
    url  = LETTER_URLS[yr]
    dest = letters_dir / f"{yr}.pdf"
    ok   = download(url, dest, f"  Berkshire Letter {yr}")
    if ok:
        downloaded_letters.append(dest)
    time.sleep(0.3)   # 礼貌性延迟

if downloaded_letters:
    combined = BOOKS_DIR / "Berkshire Hathaway Letters to Shareholders - Warren Buffett.pdf"
    if combined.exists():
        print(f"\n  ✅ 合并版已存在：{combined.name}")
    else:
        print(f"\n  合并 {len(downloaded_letters)} 封信...")
        try:
            from pypdf import PdfWriter
            writer = PdfWriter()
            for p in sorted(downloaded_letters):
                writer.append(str(p))
            with open(combined, "wb") as f:
                writer.write(f)
            print(f"  ✅ 合并完成：{combined.name}")
        except ImportError:
            print("  ⚠️  pypdf 未安装，单封信已下载到 output/bh_letters_raw/，请手动合并")
        except Exception as e:
            print(f"  ❌ 合并失败：{e}")

# ── 总结：其余版权书手动下载清单 ─────────────────────────────────────────────
print("""
═══════════════════════════════════════════════════════════
以下 47 本需要手动下载，推荐从 Anna's Archive 搜索书名：
  https://annas-archive.org

优先补充（★★★★★）：
  [3]  Poor Charlie's Almanack — Charlie Munger
  [6]  The Most Important Thing — Howard Marks
  [7]  The Psychology of Money — Morgan Housel
  [9]  Thinking, Fast and Slow — Daniel Kahneman
  [15] Margin of Safety — Seth Klarman
  [17] The Black Swan — Nassim Taleb
  [21] Security Analysis — Benjamin Graham

★★★★ 次优先：
  [23] Mastering the Market Cycle — Howard Marks
  [24] Beating the Street — Peter Lynch
  [25] The Outsiders — William Thorndike
  [27] The Alchemy of Finance — George Soros
  [29] Active Portfolio Management — Grinold & Kahn
  [31] Value Investing: From Graham to Buffett — Greenwald
  [35] Liar's Poker — Michael Lewis
  [37] Barbarians at the Gate — Burrough & Helyar
  [39] Manias, Panics, and Crashes — Kindleberger
  [40] New Market Wizards — Schwager
  [41] Hedge Fund Market Wizards — Schwager
  [42] Against the Gods — Peter Bernstein
  [44] Capital Ideas — Peter Bernstein
  [20] Seeking Wisdom — Peter Bevelin

下载后文件名保留英文原名放入 books/ 目录即可，
batch_ingest_books.py 会自动按 glob 匹配。
═══════════════════════════════════════════════════════════
""")
