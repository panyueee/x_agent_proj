#!/usr/bin/env python3
"""
财新周刊专用入库：按版面文章切分，给每篇打 section 标签（封面报道/特稿/特别报道 标为 is_feature）。

财新数字版每篇文章页首第一行是「板块｜标题」，紧跟 "CAIXIN|"/"听文章"。
据此切分文章 → 每篇一个 chunk 组，extra_meta 带 {issue, section, title, page_start/end, is_feature}。
扫描版/无法识别版面的，回退为整期入库（section="未分栏"）。

流程同 ingest_netdisk_folder：递归 /财新周刊 → 每期 下载→解析切分→入库(skip_vectors)→删临时文件；
按 fs_id 记盘断点续传。向量统一留给 embed-all。

用法：
  .venv/bin/python scripts/ingest_caixin.py                 # 全量
  .venv/bin/python scripts/ingest_caixin.py --limit 2       # 小样
  .venv/bin/python scripts/ingest_caixin.py --status
"""
from __future__ import annotations
import argparse, json, os, re, time, io, hashlib
from collections import deque
from datetime import datetime
from pathlib import Path

for k in ("http_proxy","https_proxy","all_proxy","HTTP_PROXY","HTTPS_PROXY","ALL_PROXY"):
    os.environ.pop(k, None)
os.environ["NO_PROXY"] = "*"
try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
except Exception:
    pass

import requests
ROOT = Path(__file__).parent.parent
import sys; sys.path.insert(0, str(ROOT))
TOKEN_FILE = ROOT / "output" / "baidu_token.json"
STATE_FILE = ROOT / "data" / "caixin_ingest_done.json"
UA = {"User-Agent": "pan.baidu.com"}

# section（板块名）含这些词即视为重点报道
FEATURE_KEYS = ("封面报道", "特稿", "特别报道")
# 文章页首「板块｜标题」
HEADER_RE = re.compile(r'^\s*([^\n｜丨|]{1,14}?)\s*[｜丨|]\s*(\S[^\n]{1,60})')


def _log(m): print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)
def _at(): return json.load(open(TOKEN_FILE))["access_token"]


def _list(path, at):
    r = requests.get("https://pan.baidu.com/rest/2.0/xpan/file", params={
        "method": "list", "access_token": at, "dir": path, "limit": 1000, "web": "1"},
        headers=UA, timeout=30)
    return r.json().get("list", [])


def collect_pdfs(root, at):
    q, pdfs = deque([root]), []
    while q:
        p = q.popleft()
        try:
            items = _list(p, at)
        except Exception:
            continue
        for f in items:
            if f.get("isdir"):
                q.append(f["path"])
            elif f["server_filename"].lower().endswith(".pdf"):
                pdfs.append({"path": f["path"], "fs_id": f["fs_id"],
                             "name": f["server_filename"], "size": f.get("size", 0)})
        time.sleep(0.05)
    return pdfs


def download(fs_id, at) -> bytes:
    m = requests.get("https://pan.baidu.com/rest/2.0/xpan/multimedia", params={
        "method": "filemetas", "access_token": at, "fsids": json.dumps([fs_id]),
        "dlink": "1"}, headers=UA, timeout=30).json()
    return requests.get(m["list"][0]["dlink"] + f"&access_token={at}", headers=UA, timeout=300).content


def _norm_section(s: str) -> str:
    s = s.strip().lstrip("最新").strip()
    s = re.sub(r'之[一二三四五六七八九十]+$', '', s)
    return s.strip()


def parse_articles(reader) -> list[dict]:
    """识别每篇文章的 (section, title, page_start, page_end, is_feature)。"""
    pages_text = [(p.extract_text() or "") for p in reader.pages]
    starts = []
    for i, t in enumerate(pages_text):
        head = t.strip().split("\n", 1)[0].strip() if t.strip() else ""
        if len(head) > 36:           # 文章页首是短标题行；长行多为正文
            continue
        m = HEADER_RE.match(head)
        if not m:
            continue
        sec = re.sub(r'^[^一-鿿]+', '', m.group(1)).strip()   # 去 "{{" 等噪声前缀
        cjk = sum(1 for c in sec if '一' <= c <= '鿿')
        # 真实文章头：板块名 ≤8 字且以中文为主；2024 版另有 CAIXIN/听文章 佐证
        ok = (1 <= len(sec) <= 8 and cjk >= max(1, len(sec) - 1))
        ok = ok or ("CAIXIN" in t[:200] or "听文章" in t[:200])
        if ok and len(m.group(2).strip()) >= 2:
            starts.append((i, _norm_section(sec), m.group(2).strip()))
    arts = []
    for k, (pi, sec, title) in enumerate(starts):
        pend = (starts[k + 1][0] - 1) if k + 1 < len(starts) else len(pages_text) - 1
        text = "\n\n".join(pages_text[pi:pend + 1]).strip()
        if len(text) < 50:
            continue
        arts.append({"section": sec, "title": title, "page_start": pi + 1,
                     "page_end": pend + 1, "is_feature": any(f in sec for f in FEATURE_KEYS),
                     "text": text})
    return arts


def load_done() -> set:
    return set(json.load(open(STATE_FILE))) if STATE_FILE.exists() else set()
def save_done(d): STATE_FILE.parent.mkdir(parents=True, exist_ok=True); json.dump(sorted(d), open(STATE_FILE, "w"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="/财新周刊")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    at = _at()
    _log(f"递归收集 {args.dir} ...")
    pdfs = collect_pdfs(args.dir, at)
    done = load_done()
    _log(f"共 {len(pdfs)} 期，已入库 {len([p for p in pdfs if str(p['fs_id']) in done])} 期")
    if args.status:
        return

    from x_agent.rag import ingest_text, ingest_pdf, collection_stats
    from pypdf import PdfReader
    todo = [p for p in pdfs if str(p["fs_id"]) not in done]
    if args.limit:
        todo = todo[:args.limit]
    _log(f"待处理 {len(todo)} 期")
    TMP = ROOT / "data" / "_caixin_tmp.pdf"

    ok = fail = 0
    feat_total = 0
    for i, p in enumerate(todo, 1):
        try:
            data = download(p["fs_id"], at)
            fhash = hashlib.md5(data).hexdigest()[:16]
            issue = Path(p["name"]).stem
            reader = PdfReader(io.BytesIO(data))
            arts = parse_articles(reader)
            n_chunks = 0
            if arts:
                feats = sum(1 for a in arts if a["is_feature"])
                feat_total += feats
                for k, a in enumerate(arts):
                    n_chunks += ingest_text(
                        a["text"], source_id=f"netdisk:caixin:{fhash}:a{k}",
                        source_type="netdisk", title=f"{issue} · {a['title']}",
                        author="财新周刊", skip_vectors=True,
                        extra_meta={"publication": "财新周刊", "issue": issue,
                                    "section": a["section"], "article_title": a["title"],
                                    "is_feature": a["is_feature"],
                                    "page_start": a["page_start"], "page_end": a["page_end"]})
                _log(f"[{i}/{len(todo)}] ✅ {issue}: {len(arts)}篇({feats}重点报道) {n_chunks}块")
            else:
                # 没识别出文章（扫描版/异常版面）→ 回退整期入库
                TMP.write_bytes(data)
                n_chunks = ingest_pdf(str(TMP), title=issue, author="财新周刊",
                                      source_type="netdisk", skip_vectors=True)
                _log(f"[{i}/{len(todo)}] ⚠ {issue}: 未识别分栏，整期入库 {n_chunks}块")
            done.add(str(p["fs_id"])); save_done(done); ok += 1
        except Exception as e:
            fail += 1
            _log(f"[{i}/{len(todo)}] ❌ {p['name']}: {str(e)[:80]}")
        finally:
            TMP.unlink(missing_ok=True)
        time.sleep(0.2)

    _log(f"完成：{ok} 期入库（累计 {feat_total} 篇重点报道），失败 {fail}")
    try:
        _log(f"RAG stats: {json.dumps(collection_stats(), ensure_ascii=False)}")
    except Exception:
        pass


if __name__ == "__main__":
    main()
