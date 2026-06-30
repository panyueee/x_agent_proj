#!/usr/bin/env python3
"""
递归把百度网盘某目录下的所有 PDF 入库到 RAG（source_type=netdisk）。

针对大目录（如 /财新周刊 约 745 个 PDF / 30GB）设计：
  - 递归收集所有 PDF
  - 每个 PDF：下载到临时文件 → ingest_pdf(skip_vectors=True) → 删除临时文件
    （边下边删，不会占满磁盘；向量统一留给最后 embed-all）
  - 已入库的按 fs_id 记盘跳过 → 可断点续传
  - 文本版直接抽取、扫描版自动走 OCR（ingest_pdf 内部判断）

依赖 output/baidu_token.json（先在看板完成 OAuth 授权）。
务必 .venv/bin/python 运行。百度域名强制直连 + certifi 证书（见下）。

用法：
  .venv/bin/python scripts/ingest_netdisk_folder.py --dir /财新周刊
  .venv/bin/python scripts/ingest_netdisk_folder.py --dir /财新周刊 --limit 2   # 小样
  .venv/bin/python scripts/ingest_netdisk_folder.py --dir /财新周刊 --status
"""
from __future__ import annotations
import argparse, json, os, time
from collections import deque
from datetime import datetime
from pathlib import Path

# 百度国内站：强制直连 + certifi 证书（与 dashboard 同款修正）
for k in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
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
STATE_FILE = ROOT / "data" / "netdisk_ingest_done.json"
TMP = ROOT / "data" / "_netdisk_tmp.pdf"
UA = {"User-Agent": "pan.baidu.com"}


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
        except Exception as e:
            _log(f"列目录失败 {p}: {str(e)[:60]}"); continue
        for f in items:
            if f.get("isdir"):
                q.append(f["path"])
            elif f["server_filename"].lower().endswith(".pdf"):
                pdfs.append({"path": f["path"], "fs_id": f["fs_id"],
                             "name": f["server_filename"], "size": f.get("size", 0)})
        time.sleep(0.05)
    return pdfs


def download(fs_id, at) -> bytes:
    qs = {"method": "filemetas", "access_token": at, "fsids": json.dumps([fs_id]), "dlink": "1"}
    m = requests.get("https://pan.baidu.com/rest/2.0/xpan/multimedia", params=qs,
                     headers=UA, timeout=30).json()
    dlink = m["list"][0]["dlink"] + f"&access_token={at}"
    return requests.get(dlink, headers=UA, timeout=300).content


def load_done() -> set:
    if STATE_FILE.exists():
        return set(json.load(open(STATE_FILE)))
    return set()


def save_done(done: set):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    json.dump(sorted(done), open(STATE_FILE, "w"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="/财新周刊")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    at = _at()
    _log(f"递归收集 {args.dir} 下的 PDF...")
    pdfs = collect_pdfs(args.dir, at)
    done = load_done()
    tot_gb = sum(p["size"] for p in pdfs) / 1024**3
    _log(f"共 {len(pdfs)} 个 PDF（{tot_gb:.1f} GB），已入库 {len([p for p in pdfs if str(p['fs_id']) in done])} 个")
    if args.status:
        return

    from x_agent.rag import ingest_pdf, collection_stats
    todo = [p for p in pdfs if str(p["fs_id"]) not in done]
    if args.limit:
        todo = todo[:args.limit]
    _log(f"待处理 {len(todo)} 个")

    ok = fail = 0
    for i, p in enumerate(todo, 1):
        try:
            data = download(p["fs_id"], at)
            TMP.parent.mkdir(parents=True, exist_ok=True)
            TMP.write_bytes(data)
            title = Path(p["name"]).stem
            n = ingest_pdf(str(TMP), title=title, author="财新周刊",
                           source_type="netdisk", skip_vectors=True)
            done.add(str(p["fs_id"])); save_done(done); ok += 1
            _log(f"[{i}/{len(todo)}] ✅ {p['name']} → {n} 块（{p['size']//1024//1024}MB）")
        except Exception as e:
            fail += 1
            _log(f"[{i}/{len(todo)}] ❌ {p['name']}: {str(e)[:80]}")
        finally:
            TMP.unlink(missing_ok=True)
        time.sleep(0.2)

    _log(f"完成：入库 {ok}，失败 {fail}")
    try:
        _log(f"RAG stats: {json.dumps(collection_stats(), ensure_ascii=False)}")
    except Exception:
        pass


if __name__ == "__main__":
    main()
