#!/usr/bin/env python3
"""
华尔街见闻·见闻大师课（全年版）专用入库脚本。

数据源：百度网盘 /GL12/华尔街见闻/华尔街见闻丨见闻大师课（全年版）
共 1133 个单页扫描版 PDF（图片幻灯片，需 OCR）。文件名编码了日期 + 课节 + 主题，例如：
  20181220 2.1 宏观研究最可信的要素（2个案例）（拼课会员免费，添加微信：1318827120）[防断更微coc3678].pdf
解析：date = 开头 YYYYMMDD → YYYY-MM-DD；theme = 其余部分，剥掉「（拼课会员...微信...）」「[防断更...]」等广告尾巴；
      课节号（如 2.1）单独抽成 lesson 字段，同时保留在 theme 里。

每个 PDF：下载→临时文件→OCR(macOS Vision)→解析文件名→ingest_text(带元数据)→删临时文件。
文件名（date+theme）是可靠索引；OCR 正文作为补充（幻灯片可能模糊）。即使 OCR 为空，
也会以 "date theme" 作为正文，保证该幻灯片进入索引不丢失。

并发：rag.db 开了 WAL + busy_timeout，支持多进程并发写。用 --shard i/N 把文件按 fs_id 排序后
      切片（syms[i::N]）并行 OCR（CPU 密集，多分片提速）。
      每个分片各自维护断点文件 data/gl12_mc_done_{i}.json 与临时 PDF，互不干扰。

用法：
  .venv/bin/python scripts/ingest_gl12_masterclass.py --limit 3          # 小样验证
  .venv/bin/python scripts/ingest_gl12_masterclass.py --shard 0/6        # 分片 0（共 6 片）
  .venv/bin/python scripts/ingest_gl12_masterclass.py --status           # 汇总进度
"""
from __future__ import annotations
import argparse, json, os, re, subprocess, sys, time, hashlib
from collections import deque
from datetime import datetime
from pathlib import Path

# 走本机直连，不用系统代理
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
sys.path.insert(0, str(ROOT))
TOKEN_FILE = ROOT / "output" / "baidu_token.json"
DATA_DIR = ROOT / "data"
OCR_WORKER = ROOT / "scripts" / "ocr_worker.py"
NETDISK_DIR = "/GL12/华尔街见闻/华尔街见闻丨见闻大师课（全年版）"
UA = {"User-Agent": "pan.baidu.com"}
EXPECTED_TOTAL = 1133

_SESS = requests.Session()
_SESS.trust_env = False


def _log(m): print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)
def _at(): return json.load(open(TOKEN_FILE))["access_token"]


# ---------------- 网盘列举 / 下载 ----------------

def _list(path, at, start=0):
    r = _SESS.get("https://pan.baidu.com/rest/2.0/xpan/file", params={
        "method": "list", "access_token": at, "dir": path,
        "limit": 1000, "start": start, "web": "1"},
        headers=UA, timeout=30)
    return r.json().get("list", [])


def collect_pdfs(root, at):
    """递归收集全部 PDF；带分页（单目录 >1000 项时翻页）。"""
    q, pdfs = deque([root]), []
    while q:
        p = q.popleft()
        start = 0
        while True:
            items = _list(p, at, start)
            if not items:
                break
            for f in items:
                if f.get("isdir"):
                    q.append(f["path"])
                elif f["server_filename"].lower().endswith(".pdf"):
                    pdfs.append({"path": f["path"], "fs_id": f["fs_id"],
                                 "name": f["server_filename"], "size": f.get("size", 0)})
            if len(items) < 1000:
                break
            start += 1000
            time.sleep(0.05)
        time.sleep(0.05)
    # 稳定排序：所有分片必须用同一顺序切片，否则 [i::N] 会漏/重
    pdfs.sort(key=lambda x: str(x["fs_id"]))
    return pdfs


def download(fs_id, at) -> bytes:
    m = _SESS.get("https://pan.baidu.com/rest/2.0/xpan/multimedia", params={
        "method": "filemetas", "access_token": at, "fsids": json.dumps([fs_id]),
        "dlink": "1"}, headers=UA, timeout=30).json()
    dlink = m["list"][0]["dlink"]
    return _SESS.get(dlink + f"&access_token={at}", headers=UA, timeout=300).content


# ---------------- 文件名解析 ----------------

_JUNK_PAREN = re.compile(r"[（(][^（）()]*(?:拼课|会员|微信|添加|防断更)[^（）()]*[）)]")
_JUNK_BRACK = re.compile(r"[\[【][^\]】]*(?:防断更|微|coc|断更)[^\]】]*[\]】]")
_LESSON = re.compile(r"^(\d+(?:[.\-－]\d+)*)[\s、.．]*")


def parse_name(name: str) -> dict:
    """从文件名解析 date / theme / lesson。"""
    stem = name[:-4] if name.lower().endswith(".pdf") else name
    date = ""
    rest = stem
    m = re.match(r"^(\d{8})\s*(.*)$", stem)
    if m:
        d = m.group(1)
        date = f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
        rest = m.group(2).strip()
    # 剥广告尾巴（只删含垃圾词的括号，保留「（2个案例）」「【前言】」这类正常括注）
    rest = _JUNK_BRACK.sub("", rest)
    rest = _JUNK_PAREN.sub("", rest)
    rest = re.sub(r"\s+", " ", rest).strip()
    # 抽课节号（保留在 theme 里，也单独存 lesson）
    lesson = ""
    lm = _LESSON.match(rest)
    if lm:
        lesson = lm.group(1)
    return {"date": date, "theme": rest, "lesson": lesson}


# ---------------- OCR ----------------

def ocr_pdf(tmp_path: Path) -> str:
    """单页扫描 PDF → 文字（macOS Vision）。"""
    try:
        out = subprocess.run(
            [str(ROOT / ".venv" / "bin" / "python"), str(OCR_WORKER),
             str(tmp_path), "150", "0", "1"],
            capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return ""
    parts = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parts.append(json.loads(line).get("text", ""))
        except Exception:
            continue
    return "\n".join(t for t in parts if t).strip()


# ---------------- 断点续传（每分片独立文件，避免并发互相覆盖） ----------------

def done_file(shard_i) -> Path:
    return DATA_DIR / f"gl12_mc_done_{shard_i}.json"


def load_done(shard_i) -> set:
    f = done_file(shard_i)
    if f.exists():
        try:
            return set(json.load(open(f)))
        except Exception:
            return set()
    return set()


def save_done(shard_i, d):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = done_file(shard_i).with_suffix(".json.tmp")
    json.dump(sorted(d), open(tmp, "w"))
    os.replace(tmp, done_file(shard_i))


def all_done() -> set:
    d = set()
    for f in DATA_DIR.glob("gl12_mc_done_*.json"):
        try:
            d |= set(json.load(open(f)))
        except Exception:
            pass
    return d


# ---------------- 主流程 ----------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", default="0/1", help="i/N，按 fs_id 排序后取 syms[i::N]")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    i, N = (int(x) for x in args.shard.split("/"))

    at = _at()
    _log(f"递归收集 {NETDISK_DIR} ...")
    pdfs = collect_pdfs(NETDISK_DIR, at)
    _log(f"共收集 {len(pdfs)} 个 PDF（期望 {EXPECTED_TOTAL}）")
    if len(pdfs) != EXPECTED_TOTAL:
        _log(f"⚠ 收集数 {len(pdfs)} != {EXPECTED_TOTAL}，分片切片可能错位，请检查网盘列举是否完整！")

    if args.status:
        done = all_done()
        n_done = len([p for p in pdfs if str(p["fs_id"]) in done])
        _log(f"进度：{n_done}/{len(pdfs)} 已入库")
        try:
            from x_agent.rag import collection_stats
            _log(f"RAG stats: {json.dumps(collection_stats(), ensure_ascii=False)}")
        except Exception:
            pass
        return

    # 切片
    shard = pdfs[i::N]
    done = load_done(i)
    todo = [p for p in shard if str(p["fs_id"]) not in done]
    if args.limit:
        todo = todo[:args.limit]
    _log(f"[shard {i}/{N}] 本片 {len(shard)} 个，待处理 {len(todo)} 个")

    from x_agent.rag import ingest_text, text_quality
    TMP = DATA_DIR / f"_gl12mc_tmp_{i}.pdf"
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    ok = fail = empty_ocr = 0
    for n, p in enumerate(todo, 1):
        name = p["name"]
        try:
            data = download(p["fs_id"], at)
            fhash = hashlib.md5(data).hexdigest()[:16]
            TMP.write_bytes(data)
            ocr = ocr_pdf(TMP)
            meta = parse_name(name)
            date, theme = meta["date"], meta["theme"]
            # 质检 OCR 正文：过关才并入，否则只留干净的「日期+主题」元数据(仍可按主题检索)
            ocr_ok, _reason = text_quality(ocr, min_chars=40) if ocr else (False, "empty")
            if not ocr_ok:
                empty_ocr += 1
            body = f"{date} {theme}".strip()
            text = f"{body}\n\n{ocr}".strip() if ocr_ok else body
            n_chunks = ingest_text(
                text,
                source_id=f"netdisk:gl12mc:{fhash}",
                source_type="netdisk",
                title=f"{date} {theme}".strip(),
                author="华尔街见闻·见闻大师课",
                skip_vectors=True,
                extra_meta={"publication": "华尔街见闻", "course": "见闻大师课",
                            "date": date, "theme": theme, "lesson": meta["lesson"],
                            "ocr_quality": "ok" if ocr_ok else "poor",
                            "filename": name})
            done.add(str(p["fs_id"]))
            save_done(i, done)
            ok += 1
            flag = "（OCR空）" if not ocr else f"{len(ocr)}字"
            _log(f"[shard {i}] [{n}/{len(todo)}] ✅ {date} {theme[:28]} · {n_chunks}块 {flag}")
        except Exception as e:
            fail += 1
            _log(f"[shard {i}] [{n}/{len(todo)}] ❌ {name[:40]}: {str(e)[:80]}")
        finally:
            TMP.unlink(missing_ok=True)
        time.sleep(0.1)

    _log(f"[shard {i}/{N}] 完成：入库 {ok}，失败 {fail}，OCR空 {empty_ocr}")


if __name__ == "__main__":
    main()
