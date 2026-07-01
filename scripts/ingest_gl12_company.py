#!/usr/bin/env python3
"""
华尔街见闻丨公司分析（GL12）专用入库：34 张单页扫描 PDF（图片幻灯片，无内嵌文字 → 需 OCR）。

- 文件名编码 日期+公司+主题，据此提取干净元数据（不依赖 OCR 质量）：
    * 17-12-14-维他集团-为什么…            → date=2017-12-14, company=维他集团, theme=为什么…
    * 2018-04-28以微软为例，SaaS转型如何…   → date=2018-04-28, company=微软(关键词命中), theme=以微软为例…
    * 20180307如何抵抗半导体…以AMAT为例     → date=2018-03-07, company=AMAT, theme=如何抵抗半导体…
- 剥掉 [防断更微…] / （拼课会员…微信…） / 【请加微信…】 等垃圾后缀。
- 公司：破折号显式段（维他集团/宜人贷）直接取；其余用关键词表 best-effort 命中，宁缺勿错。
- 下载 PDF → 临时文件 → scripts/ocr_worker.py（macOS Vision）OCR → ingest_text(skip_vectors=True) → 删临时文件。
- 按 fs_id 记盘断点续传（data/gl12_company_done.json）；仅在入库 ≥1 块时标记 done，OCR 空/异常留待重试。

用法：
  .venv/bin/python scripts/ingest_gl12_company.py --dry-run   # 只打印文件名解析表，不下载
  .venv/bin/python scripts/ingest_gl12_company.py             # 全量入库
  .venv/bin/python scripts/ingest_gl12_company.py --limit 2   # 小样
  .venv/bin/python scripts/ingest_gl12_company.py --status    # 只看进度
"""
from __future__ import annotations
import argparse, json, os, re, subprocess, sys, time, hashlib
from datetime import datetime
from pathlib import Path

os.environ["SSL_CERT_FILE"] = __import__("certifi").where()
import requests

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
TOKEN_FILE = ROOT / "output" / "baidu_token.json"
STATE_FILE = ROOT / "data" / "gl12_company_done.json"
TARGET_DIR = "/GL12/华尔街见闻/华尔街见闻丨公司分析"
UA = {"User-Agent": "pan.baidu.com"}
PY = str(ROOT / ".venv" / "bin" / "python")
OCR_WORKER = str(ROOT / "scripts" / "ocr_worker.py")

_S = requests.Session(); _S.trust_env = False   # 直连，免受代理抖动

# 公司关键词表：仅收录能从主题里无歧义指认「被分析公司」的词。
# 刻意排除 思科(怒怼思科的是挑战者而非思科本身)、这家大蓝筹/美国第一科技牛股/TMT白马股/一家美国公司/云计算产业（无明确主体）。
COMPANY_KEYWORDS = [
    "维他集团", "宜人贷", "微软", "亚马逊", "小米", "网易", "迪士尼",
    "Facebook", "英伟达", "麦当劳", "Adobe", "B站", "AMAT", "任天堂",
    "加拿大鹅", "唯品会", "微博", "Netflix",
]


def _log(m): print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)
def _at(): return json.load(open(TOKEN_FILE))["access_token"]


def _list(path):
    return _S.get("https://pan.baidu.com/rest/2.0/xpan/file", params={
        "method": "list", "access_token": _at(), "dir": path, "limit": 1000, "web": "1"},
        headers=UA, timeout=30).json().get("list", [])


def collect_pdfs():
    """目标文件夹为单层，收集全部 .pdf（大小写不敏感）。"""
    return [{"fs_id": f["fs_id"], "name": f["server_filename"], "size": f.get("size", 0)}
            for f in _list(TARGET_DIR)
            if f["server_filename"].lower().endswith(".pdf")]


# 垃圾后缀：防断更、拼课会员/添加微信、请加微信【…】
_JUNK_BRACKET = re.compile(r"\[防断更微[^\]]*\]")
_JUNK_WECHAT_CN = re.compile(r"【[^】]*】")
_JUNK_WECHAT_PAREN = re.compile(r"（(?:拼课|请加|添加)[^）]*）?")


def clean_theme(s: str) -> str:
    s = _JUNK_BRACKET.sub("", s)
    s = _JUNK_WECHAT_CN.sub("", s)
    s = _JUNK_WECHAT_PAREN.sub("", s)
    return s.strip(" 　_-").strip()


def parse_name(name: str) -> dict:
    """文件名 → {date, company, theme}。date 归一化为 YYYY-MM-DD；无法解析则 date=None。"""
    stem = re.sub(r"\.pdf$", "", name, flags=re.I)
    date = company = None
    rest = stem

    # A: YY-MM-DD-公司-主题（早期维他/宜人贷格式）
    mA = re.match(r"^(\d{2})-(\d{1,2})-(\d{1,2})-(.+)$", stem)
    # B: YYYY-MM-DD[空格]主题
    mB = re.match(r"^(\d{4})-(\d{2})-(\d{2})\s*(.+)$", stem)
    # C: YYYYMMDD[空格]主题（紧凑）
    mC = re.match(r"^(\d{4})(\d{2})(\d{2})\s*(.+)$", stem)

    if mA:
        y, mo, d, rest = mA.groups()
        date = f"20{int(y):02d}-{int(mo):02d}-{int(d):02d}"
        # 破折号显式公司段：第一段为公司，其余为主题
        if "-" in rest:
            company, rest = rest.split("-", 1)
            company = company.strip()
        rest = rest
    elif mB:
        y, mo, d, rest = mB.groups()
        date = f"{y}-{int(mo):02d}-{int(d):02d}"
    elif mC:
        y, mo, d, rest = mC.groups()
        date = f"{y}-{int(mo):02d}-{int(d):02d}"

    theme = clean_theme(rest)

    # 公司：若尚未从破折号段取到，用关键词表 best-effort 命中主题
    if not company:
        for kw in COMPANY_KEYWORDS:
            if kw.lower() in theme.lower():
                company = kw
                break
    company = company or ""
    return {"date": date, "company": company, "theme": theme}


def download(fs_id, dest, tries=3):
    last = None
    for attempt in range(tries):
        try:
            m = _S.get("https://pan.baidu.com/rest/2.0/xpan/multimedia", params={
                "method": "filemetas", "access_token": _at(), "fsids": json.dumps([fs_id]),
                "dlink": "1"}, headers=UA, timeout=30).json()
            dlink = m["list"][0]["dlink"] + f"&access_token={_at()}"
            with _S.get(dlink, headers=UA, timeout=600, stream=True) as r:
                size = 0
                with open(dest, "wb") as f:
                    for ch in r.iter_content(1 << 20):
                        f.write(ch); size += len(ch)
            if size > 0:
                return size
        except Exception as e:
            last = e
            time.sleep(2 * (2 ** attempt))
    raise last if last else RuntimeError("下载为空")


def ocr_pdf(pdf_path: str, tries=2) -> str:
    """调用 ocr_worker 子进程 OCR 整个 PDF，拼接所有页文字。"""
    last = None
    for attempt in range(tries):
        try:
            out = subprocess.run([PY, OCR_WORKER, pdf_path, "150"],
                                 capture_output=True, text=True, timeout=300)
            texts = []
            for line in out.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    texts.append(json.loads(line).get("text", ""))
                except Exception:
                    continue
            joined = "\n".join(t for t in texts if t).strip()
            if joined:
                return joined
            last = RuntimeError(f"OCR 空文本 (stderr={out.stderr[:120]})")
        except Exception as e:
            last = e
        time.sleep(1)
    if last:
        raise last
    return ""


def load_done() -> set:
    return set(json.load(open(STATE_FILE))) if STATE_FILE.exists() else set()
def save_done(d): STATE_FILE.parent.mkdir(parents=True, exist_ok=True); json.dump(sorted(d), open(STATE_FILE, "w"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印文件名解析表")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    _log(f"列取 {TARGET_DIR} …")
    pdfs = collect_pdfs()
    pdfs.sort(key=lambda p: p["name"])
    _log(f"共 {len(pdfs)} 个 PDF")

    if args.dry_run:
        print(f"\n{'date':<12} {'company':<10} theme")
        print("-" * 100)
        unparsed = 0
        for p in pdfs:
            info = parse_name(p["name"])
            if info["date"] is None:
                unparsed += 1
            print(f"{info['date'] or '??UNPARSED':<12} {info['company']:<10} {info['theme'][:60]}")
        print("-" * 100)
        print(f"合计 {len(pdfs)} 个；未解析日期 {unparsed} 个")
        return

    done = load_done()
    _log(f"已入库 {len([p for p in pdfs if str(p['fs_id']) in done])} 个")
    if args.status:
        return

    from x_agent.rag import ingest_text, collection_stats, text_quality
    todo = [p for p in pdfs if str(p["fs_id"]) not in done]
    if args.limit:
        todo = todo[:args.limit]
    _log(f"待处理 {len(todo)} 个")
    TMP = ROOT / "data" / "_gl12_tmp.pdf"

    ok = fail = ocr_fail = 0
    for i, p in enumerate(todo, 1):
        name = p["name"]
        info = parse_name(name)
        try:
            size = download(p["fs_id"], TMP)
            fhash = hashlib.md5(open(TMP, "rb").read()).hexdigest()[:16]
            ocr = ocr_pdf(str(TMP))
            # 质检：OCR 过关才入正文；否则只留干净元数据(日期/公司/主题)保证可检索、不进垃圾
            ocr_ok, _reason = text_quality(ocr, min_chars=40)
            meta_line = " ".join(x for x in [info["date"], info["company"], info["theme"]] if x)
            text = f"{meta_line}\n\n{ocr}".strip() if ocr_ok else meta_line
            if not ocr_ok:
                ocr_fail += 1
            n = ingest_text(
                text,
                source_id=f"netdisk:gl12company:{fhash}",
                source_type="netdisk",
                title=(f"{info['company']}·{info['theme']}" if info["company"] else info["theme"])[:200],
                author="华尔街见闻·公司分析",
                skip_vectors=True,
                extra_meta={
                    "publication": "华尔街见闻",
                    "course": "公司分析",
                    "date": info["date"],
                    "company": info["company"],
                    "theme": info["theme"],
                    "ocr_quality": "ok" if ocr_ok else "poor",
                    "filename": name,
                },
            )
            if n >= 1:
                done.add(str(p["fs_id"])); save_done(done); ok += 1
                _log(f"[{i}/{len(todo)}] ✅ {info['date']} {info['company'] or '—'} | "
                     f"{info['theme'][:26]} → {n}块 ({len(text)}字, {size//1024}KB)")
            else:
                ocr_fail += 1
                _log(f"[{i}/{len(todo)}] ⚠ {name[:40]}: OCR 文本入库 0 块（{len(text)}字），留待重试")
        except Exception as e:
            fail += 1
            emsg = str(e)
            if "OCR" in emsg:
                ocr_fail += 1
            _log(f"[{i}/{len(todo)}] ❌ {name[:40]}: {emsg[:80]}")
        finally:
            TMP.unlink(missing_ok=True)
        time.sleep(0.2)

    _log(f"完成：入库 {ok}，失败 {fail}（其中 OCR 相关 {ocr_fail}）")
    try:
        _log(f"RAG stats: {json.dumps(collection_stats(), ensure_ascii=False)}")
    except Exception:
        pass


if __name__ == "__main__":
    main()
