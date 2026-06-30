#!/usr/bin/env python3
"""
扫描 GitHub 上托管的投资书籍 PDF 仓库，自动匹配并下载到 books/ 目录。

策略：
  1. 通过 GitHub Trees API 递归列出几个公开书单仓库的所有文件
  2. 筛选出 *.pdf
  3. 跟 batch_ingest_books.BOOK_CATALOG 的标题做模糊匹配
  4. 命中后用 raw.githubusercontent.com 下载到 books/

用法：
  python scripts/scan_github_pdfs.py             # 实际下载
  python scripts/scan_github_pdfs.py --dry-run   # 只列出匹配，不下载
  GITHUB_TOKEN=ghp_xxx python scripts/...        # 提升 API 速率限制（可选）
"""
from __future__ import annotations

import os
import re
import sys
import time
import urllib.parse
from difflib import SequenceMatcher
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scripts.batch_ingest_books import BOOK_CATALOG, BOOKS_DIR  # noqa: E402


# 候选仓库：以直接托管 PDF 闻名的投资书单。
# 如果对方仓库已删/已搬迁，脚本会报 404 跳过，不会终止。
REPOS = [
    ("manjunath5496", "Best-Investing-Books"),
]

GITHUB_API = "https://api.github.com"
RAW_BASE   = "https://raw.githubusercontent.com"
UA         = "Mozilla/5.0 (x_agent_proj/scan_github_pdfs)"
FUZZY_THRESHOLD = 0.62   # SequenceMatcher 比例，> 0.62 视为命中


# ── 工具函数 ─────────────────────────────────────────────────────────────────
def _http_json(url: str) -> dict:
    headers = {"User-Agent": UA, "Accept": "application/json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token and "api.github.com" in url:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()


def _norm(s: str) -> str:
    """标题归一化：小写 + 去标点 + 折叠空白。"""
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _get_default_branch(owner: str, repo: str) -> str | None:
    try:
        data = _http_json(f"{GITHUB_API}/repos/{owner}/{repo}")
        return data.get("default_branch", "main")
    except requests.HTTPError as e:
        print(f"  ⚠️  {owner}/{repo} 仓库元数据获取失败：HTTP {e.response.status_code}")
        return None
    except Exception as e:
        print(f"  ⚠️  {owner}/{repo} 仓库元数据获取失败：{e}")
        return None


def _list_pdfs(owner: str, repo: str, branch: str) -> list[str]:
    """通过 Git Trees API 递归列出仓库内所有 .pdf 路径。"""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
    try:
        data = _http_json(url)
    except requests.HTTPError as e:
        print(f"  ⚠️  {owner}/{repo} 树列举失败：HTTP {e.response.status_code}")
        return []
    except Exception as e:
        print(f"  ⚠️  {owner}/{repo} 树列举失败：{e}")
        return []

    if data.get("truncated"):
        print(f"  ⚠️  {owner}/{repo} 文件树被 GitHub 截断，可能漏掉部分 PDF")

    return [
        node["path"]
        for node in data.get("tree", [])
        if node.get("type") == "blob" and node.get("path", "").lower().endswith(".pdf")
    ]


def _fetch_readme_titles(owner: str, repo: str, branch: str) -> dict[str, str]:
    """
    从 README 抓 {pdf 路径: 书名} 映射。
    很多仓库（如 manjunath5496/Best-Investing-Books）的 PDF 名为 inv(N).pdf，
    真名只出现在 README 的 <a href="...inv(N).pdf">真名</a> 里。
    """
    mapping: dict[str, str] = {}
    for fname in ("README.md", "Readme.md", "readme.md"):
        url = f"{RAW_BASE}/{owner}/{repo}/{branch}/{fname}"
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
            if r.status_code != 200:
                continue
            text = r.text
        except Exception:
            continue

        # 形如 <a href="...path/to/file.pdf" ...>书名</a>
        for m in re.finditer(
            r'<a[^>]*href="([^"]+\.pdf)"[^>]*>([^<]+)</a>',
            text, re.IGNORECASE,
        ):
            href, title = m.group(1), m.group(2).strip()
            # 把 href 归一化成 repo 内的相对路径
            path = href.rsplit(f"{branch}/", 1)[-1] if f"{branch}/" in href else href.lstrip("./")
            path = urllib.parse.unquote(path)
            if title and path.lower().endswith(".pdf"):
                mapping[path] = title
        break  # 找到一个 README 就够了
    return mapping


# ── 匹配 ─────────────────────────────────────────────────────────────────────
def _match_catalog(name_for_matching: str) -> tuple | None:
    """
    输入 README 提取的标题（首选）或 PDF 文件名（兜底），返回命中的 BOOK_CATALOG 元组。
    优先精确包含匹配，其次 SequenceMatcher 阈值。
    """
    fn_norm = _norm(name_for_matching)
    if not fn_norm:
        return None

    best_entry = None
    best_score = 0.0

    for entry in BOOK_CATALOG:
        _, _, title, _, _, _ = entry
        title_norm = _norm(title)
        if not title_norm:
            continue

        # 1) 文件名包含完整标题，直接命中
        if title_norm in fn_norm:
            return entry

        # 2) SequenceMatcher 比例
        score = SequenceMatcher(None, fn_norm, title_norm).ratio()
        if score > best_score:
            best_score = score
            best_entry = entry

    return best_entry if best_score >= FUZZY_THRESHOLD else None


# ── 下载 ─────────────────────────────────────────────────────────────────────
def _safe_filename(title: str, author: str) -> str:
    """生成统一命名：'<title> - <author>.pdf'，去掉文件系统不安全字符。"""
    base = f"{title} - {author}".strip(" -")
    base = re.sub(r"[\\/:*?\"<>|]", "", base)
    return base + ".pdf"


def _download(url: str, dest: Path) -> bool:
    if dest.exists():
        print(f"      ✅ 已存在：{dest.name}")
        return True
    try:
        with requests.get(url, headers={"User-Agent": UA}, stream=True, timeout=60) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length", 0))
            downloaded = 0
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = downloaded * 100 // total
                        print(f"\r      {pct}% ({downloaded//1024}KB/{total//1024}KB)",
                              end="", flush=True)
        print(f"\n      ✅ 完成：{dest.name}")
        return True
    except Exception as e:
        print(f"\n      ❌ 失败：{e}")
        if dest.exists():
            dest.unlink()
        return False


# ── 主流程 ───────────────────────────────────────────────────────────────────
def main() -> None:
    dry_run = "--dry-run" in sys.argv

    if not BOOKS_DIR.exists():
        BOOKS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"{'[DRY RUN] ' if dry_run else ''}扫描 GitHub 仓库：\n")
    for owner, repo in REPOS:
        print(f"  • {owner}/{repo}")
    print()

    # 已入库的 priority 集合（避免重复下载）
    have_priorities: set[int] = set()
    for priority, pattern, *_ in BOOK_CATALOG:
        if list(BOOKS_DIR.glob(pattern)):
            have_priorities.add(priority)

    print(f"books/ 已有 {len(have_priorities)} 本，缺失 {len(BOOK_CATALOG) - len(have_priorities)} 本\n")

    # 收集所有候选 PDF
    candidates: list[tuple[str, str, str, str, str]] = []  # (owner, repo, branch, path, display)
    for owner, repo in REPOS:
        print(f"列举 {owner}/{repo} ...")
        branch = _get_default_branch(owner, repo)
        if not branch:
            continue
        paths = _list_pdfs(owner, repo, branch)
        title_map = _fetch_readme_titles(owner, repo, branch)
        print(f"  找到 {len(paths)} 个 PDF，README 解析 {len(title_map)} 条标题")
        for p in paths:
            display = title_map.get(p) or Path(p).stem
            candidates.append((owner, repo, branch, p, display))
        time.sleep(0.3)   # GitHub API 礼貌延迟

    print(f"\n候选总数：{len(candidates)} 个 PDF\n")

    # 匹配 + 下载
    matched: list[tuple] = []
    seen_priorities: set[int] = set(have_priorities)

    skipped_have: list[tuple] = []
    unmatched: list[str] = []
    for owner, repo, branch, path, display in candidates:
        entry = _match_catalog(display)
        if entry is None:
            unmatched.append(display)
            continue
        priority, _, title, author, importance, _ = entry
        if priority in seen_priorities:
            skipped_have.append((priority, title, display))
            continue

        raw_url = f"{RAW_BASE}/{owner}/{repo}/{branch}/{urllib.parse.quote(path)}"
        dest    = BOOKS_DIR / _safe_filename(title, author)

        print(f"[{priority:2d}] {title} ← {owner}/{repo}/{path}  (README 标题: {display})")
        if dry_run:
            print(f"      （dry-run）{raw_url}\n")
            matched.append((priority, title, raw_url, dest))
            seen_priorities.add(priority)
            continue

        if _download(raw_url, dest):
            matched.append((priority, title, raw_url, dest))
            seen_priorities.add(priority)
        print()
        time.sleep(0.3)

    if skipped_have:
        print(f"\n仓库里有但 books/ 已存在的 {len(skipped_have)} 本（跳过）：")
        for pr, ti, _ in skipped_have:
            print(f"  [{pr:2d}] {ti}")

    if unmatched:
        print(f"\n仓库里有但不在书单的 {len(unmatched)} 本：")
        for d in unmatched:
            print(f"  - {d}")

    print("\n" + "=" * 60)
    print(f"本次新匹配 {len(matched)} 本，加上已有合计 {len(seen_priorities)}/{len(BOOK_CATALOG)} 本")
    if not matched:
        print("没有新命中。仓库覆盖的书你都已经有了；剩余缺口请从 Anna's Archive 手动下载。")


if __name__ == "__main__":
    main()
