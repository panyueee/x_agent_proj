#!/usr/bin/env python3
"""
双语周刊（彭博商业周刊 / Barron's 巴伦周刊）入库：中英双语文本 PDF。
- 流式下载到临时文件（扛 Barron's 的 GB 级巨型 PDF，不载入内存）→ 提取文本 → 删文件
- 按页批(6页)切块入库，extra_meta 带 {publication, issue, language:"zh+en", page_start/end, is_feature}
- 封面故事/Cover Story 所在页批标 is_feature=true（重要性高）；best-effort
- skip_vectors（向量留给 embed-all）；按 fs_id 记盘断点续传

用法：
  .venv/bin/python scripts/ingest_bilingual_mag.py --dir /彭博商业周刊 --pub 彭博商业周刊
  .venv/bin/python scripts/ingest_bilingual_mag.py --dir "/Barron's" --pub "Barron's" --limit 1
"""
from __future__ import annotations
import argparse, json, os, re, time, hashlib
from collections import deque
from datetime import datetime
from pathlib import Path

os.environ["SSL_CERT_FILE"] = __import__("certifi").where()
import requests
ROOT = Path(__file__).parent.parent
import sys; sys.path.insert(0, str(ROOT))
TOKEN_FILE = ROOT / "output" / "baidu_token.json"
UA = {"User-Agent": "pan.baidu.com"}
PAGES_PER_BATCH = 6
COVER_RE = re.compile(r'封面故事|封面报道|Cover Story|COVER STORY|封面文章', re.I)

_S = requests.Session(); _S.trust_env = False   # 直连，免受代理抖动


def _log(m): print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)
def _at(): return json.load(open(TOKEN_FILE))["access_token"]


def _list(path):
    return _S.get("https://pan.baidu.com/rest/2.0/xpan/file", params={
        "method": "list", "access_token": _at(), "dir": path, "limit": 1000, "web": "1"},
        headers=UA, timeout=30).json().get("list", [])


def collect_pdfs(root):
    q, pdfs = deque([root]), []
    while q:
        for f in _list(q.popleft()):
            if f.get("isdir"): q.append(f["path"])
            elif f["server_filename"].lower().endswith(".pdf"):
                pdfs.append({"fs_id": f["fs_id"], "name": f["server_filename"], "size": f.get("size", 0)})
    return pdfs


def stream_download(fs_id, dest):
    m = _S.get("https://pan.baidu.com/rest/2.0/xpan/multimedia", params={
        "method": "filemetas", "access_token": _at(), "fsids": json.dumps([fs_id]),
        "dlink": "1"}, headers=UA, timeout=30).json()
    dlink = m["list"][0]["dlink"] + f"&access_token={_at()}"
    with _S.get(dlink, headers=UA, timeout=1200, stream=True) as r:
        with open(dest, "wb") as f:
            for ch in r.iter_content(1 << 20):
                f.write(ch)


def load_done(state):
    return set(json.load(open(state))) if state.exists() else set()
def save_done(state, d): state.parent.mkdir(parents=True, exist_ok=True); json.dump(sorted(d), open(state, "w"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--pub", required=True)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    state = ROOT / "data" / f"mag_done_{re.sub(r'[^a-zA-Z0-9]','_',args.pub)}.json"
    pdfs = collect_pdfs(args.dir)
    done = load_done(state)
    _log(f"{args.pub}: {len(pdfs)} 期，已入库 {len([p for p in pdfs if str(p['fs_id']) in done])} 期")
    if args.status:
        return

    from x_agent.rag import ingest_text, collection_stats
    from pypdf import PdfReader
    todo = [p for p in pdfs if str(p["fs_id"]) not in done]
    if args.limit: todo = todo[:args.limit]
    _log(f"待处理 {len(todo)} 期")
    TMP = ROOT / "data" / "_mag_tmp.pdf"

    ok = fail = 0
    for i, p in enumerate(todo, 1):
        try:
            stream_download(p["fs_id"], TMP)
            fhash = hashlib.md5(open(TMP, "rb").read(1 << 20)).hexdigest()[:16]
            issue = Path(p["name"]).stem
            reader = PdfReader(str(TMP))
            pages = [(pg.extract_text() or "") for pg in reader.pages]
            # 找封面故事页范围（首个 marker 起，+12 页 或 到文末）
            cover_pages = set()
            for pi, t in enumerate(pages):
                if COVER_RE.search(t[:400]):
                    cover_pages.update(range(pi, min(pi + 12, len(pages))))
                    break
            n_chunks = 0
            for b in range(0, len(pages), PAGES_PER_BATCH):
                seg = "\n\n".join(pages[b:b + PAGES_PER_BATCH]).strip()
                if len(seg) < 50:
                    continue
                is_feat = any(pp in cover_pages for pp in range(b, min(b + PAGES_PER_BATCH, len(pages))))
                n_chunks += ingest_text(
                    seg, source_id=f"netdisk:mag:{fhash}:b{b}", source_type="netdisk",
                    title=f"{issue} p{b+1}-{min(b+PAGES_PER_BATCH,len(pages))}",
                    author=args.pub, skip_vectors=True,
                    extra_meta={"publication": args.pub, "issue": issue, "language": "zh+en",
                                "page_start": b + 1, "page_end": min(b + PAGES_PER_BATCH, len(pages)),
                                "is_feature": is_feat})
            done.add(str(p["fs_id"])); save_done(state, done); ok += 1
            _log(f"[{i}/{len(todo)}] ✅ {issue}: {len(pages)}页 {n_chunks}块 封面标记页{len(cover_pages)} ({p['size']//1024//1024}MB)")
        except Exception as e:
            fail += 1
            _log(f"[{i}/{len(todo)}] ❌ {p['name'][:40]}: {str(e)[:80]}")
        finally:
            TMP.unlink(missing_ok=True)
        time.sleep(0.2)

    _log(f"完成：{ok} 期，失败 {fail}")
    try: _log(f"stats: {json.dumps(collection_stats(),ensure_ascii=False)}")
    except Exception: pass


if __name__ == "__main__":
    main()
