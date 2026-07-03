#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从东方财富研报中心 API 抓取【行业研报】与【个股研报】PDF 并入库 RAG。

现有 ingest_eastmoney_research.py 只覆盖 report/jg 的宏观(qType=3)/策略(qType=2)。
本脚本补齐 report/list 上的行业研报与个股研报，复用同一套 PDF WAF 破解 + x_agent.rag 入库。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
调研结论（本脚本作者实测 2026-06/07）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
列表接口：GET https://reportapi.eastmoney.com/report/list
  参数：pageSize / pageNo / qType / beginTime / endTime，可选 code / industryCode
  返回：{"hits": N, "TotalPage": P, "data": [ {记录…} ]}

report/list 的 qType 语义（与 report/jg 不同！jg 上 3=宏观 2=策略）：
  qType=0 → 个股研报（记录含 stockCode/stockName，industryName 空）  ← 本脚本 gegu
  qType=1 → 行业研报（记录含 industryCode/industryName，stockCode 空） ← 本脚本 hangye
  qType=2 → 策略/晨会/日报（无股票无行业）
  qType=3/4/5/6 → 在 report/list 上均返回 0（宏观仍只在 report/jg）
  实测 2026-06 单月：个股 394、行业 1341、策略 873 篇。故务必按主题/个股 + 时间窗 scope，
  否则一个月上千篇、多年海量，会拉爆并触发限流。

过滤参数（服务端）：
  个股：qType=0 & code=<6位股票代码>  —— 正确参数是 code（不是 stockCode/stock，后两者被忽略
        返回全量）。沪深皆可：601689 拓普集团 / 000001 平安银行 均正常。
  行业：qType=1 & industryCode=<行业代码>  —— 精确服务端过滤，最省。行业代码即记录里的
        industryCode 字段（东财自有行业分类，如 481=汽车零部件、538=化学制品、1262=乘用车、
        459=元件、1033=电池、420=航空机场）。没有官方"行业代码列表"接口（试过若干 404），
        需要时从 report/list 记录里的 industryCode/industryName 现采。
  概念/主题（如"机器人"）在东财没有对应 industryCode（它是概念不是行业），只能在时间窗内
        翻 qType=1 客户端按标题关键词匹配 —— best-effort，受 --max-pages 上限约束。

PDF 地址：由 infoCode 直接拼 https://pdf.dfcfw.com/pdf/H3_<infoCode>_1.pdf（happy path）；
  若该直链真 404（非 WAF 的 <script> 200），回退抓 data.eastmoney.com/report/info/<infoCode>.html
  正文里的 pdf.dfcfw.com 链接（对任意研报类型都通用）。
  pdf.dfcfw.com 有 JS-Cookie WAF：首次返回 <script> 挑战，用 node 执行取 document.cookie 写回
  session，之后本 session 内 PDF 直接下载。—— 需要 node（本机 v22 在 nvm 下，见 _node_bin()；
  PATH 默认可能是坏掉的 v12，故显式定位 v22）。

机构覆盖（诚实记录）：report/list 是比 jg 广得多的全网收录 feed。个股/行业研报里能看到
  太平洋/中航/华龙/华金/国信/中邮/五矿/东吴/开源/国金/东兴… 具体某股/某行业收录了哪些券商，
  运行 --dry-run 看 orgSName 分布即知（拓普 601689 近 18 月命中里含哪些券商，见运行输出）。

入库：source_type="research"（与宏观策略脚本一致）；extra_meta 写 report_type(gegu/hangye)/
  org/stock_code/stock_name/industry/industry_code/date/title/researcher/rating/info_code。
  断点续传 done-list 独立于宏观策略（data/eastmoney_hangye_done.json）。
  文本 PDF 走 x_agent.rag.ingest_pdf(use_ocr=False) 不动共享逻辑，入库后用一条
  UPDATE json_patch 补 extra_meta。入库前显式跑 rag.text_quality 质检（ingest_pdf 文本路径
  本身不做质检，扫描版研报会静默入 0 块，这里提前拦下并记原因）。

环境：请用 .venv/bin/python 运行（勿用坏掉的 anaconda3.8）。
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA_DIR = ROOT / "data"
DONE_FILE = DATA_DIR / "eastmoney_hangye_done.json"   # 断点续传（独立于宏观策略）
TMP_PDF = DATA_DIR / "_hangye_tmp.pdf"

LIST_URL = "https://reportapi.eastmoney.com/report/list"
PDF_TMPL = "https://pdf.dfcfw.com/pdf/H3_{info}_1.pdf"
INFO_URL = "https://data.eastmoney.com/report/info/{info}.html"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://data.eastmoney.com/report/",
}
PAGE_SIZE = 100
SLEEP = 0.4                 # 礼貌限速
MAX_CONSEC_FAIL = 6        # 连续失败上限：达到即停下报告，不硬刚 WAF/限流


# ---------------------------------------------------------------------------
# node 定位（WAF 破解需要 v22；PATH 默认可能是坏掉的 v12）
# ---------------------------------------------------------------------------
def _node_bin() -> str:
    """优先用 nvm 下的 node v22+，否则退回 PATH 里的 node。"""
    cands = sorted(glob.glob(str(Path.home() / ".nvm/versions/node/v2*/bin/node")), reverse=True)
    for c in cands:
        if os.access(c, os.X_OK):
            return c
    return "node"


NODE_BIN = _node_bin()


# ---------------------------------------------------------------------------
# 会话 / 断点续传
# ---------------------------------------------------------------------------
def make_session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False       # 直连，忽略系统代理
    s.headers.update(HEADERS)
    return s


def load_done() -> dict:
    if DONE_FILE.exists():
        try:
            return json.loads(DONE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_done(done: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DONE_FILE.write_text(json.dumps(done, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------------------
# 列表抓取（fetch_list 是唯一网络 seam，测试时 patch 它）
# ---------------------------------------------------------------------------
def fetch_list(s: requests.Session, params: dict) -> dict:
    p = {"pageSize": PAGE_SIZE, "pageNo": 1}
    p.update(params)
    r = s.get(LIST_URL, params=p, timeout=30)
    r.raise_for_status()
    return r.json()


def iter_list(s: requests.Session, base_params: dict, max_pages: int = 0):
    """按 base_params 翻页 report/list。max_pages>0 时最多翻这么多页（防爆量）。"""
    first = fetch_list(s, {**base_params, "pageNo": 1})
    total = first.get("TotalPage") or 1
    if max_pages:
        total = min(total, max_pages)
    for rec in first.get("data") or []:
        yield rec
    for p in range(2, total + 1):
        time.sleep(SLEEP)
        try:
            j = fetch_list(s, {**base_params, "pageNo": p})
        except Exception as e:
            print(f"    [warn] 第 {p} 页抓取失败：{e}")
            break
        data = j.get("data") or []
        if not data:
            break
        for rec in data:
            yield rec


# ---------------------------------------------------------------------------
# 纯函数：参数构造 / 记录 → (source_id, pdf_url, extra_meta)（便于单测）
# ---------------------------------------------------------------------------
def stock_params(code: str, begin: str, end: str) -> dict:
    return {"qType": 0, "code": code, "beginTime": begin, "endTime": end}


def industry_params(industry_code: str, begin: str, end: str) -> dict:
    return {"qType": 1, "industryCode": industry_code, "beginTime": begin, "endTime": end}


def keyword_params(begin: str, end: str) -> dict:
    """主题关键词模式：翻全量 qType=1，客户端过滤。"""
    return {"qType": 1, "beginTime": begin, "endTime": end}


def source_id_of(rec: dict) -> str:
    return f"research:em:{rec.get('infoCode')}"


def pdf_url_of(rec: dict) -> str:
    return PDF_TMPL.format(info=rec.get("infoCode"))


def extra_meta_of(rec: dict, report_type: str) -> dict:
    pub = (rec.get("publishDate") or "")[:10]
    return {
        "source": "eastmoney",
        "report_type": report_type,           # gegu / hangye
        "org": rec.get("orgSName") or "",
        "stock_code": rec.get("stockCode") or "",
        "stock_name": rec.get("stockName") or "",
        "industry": rec.get("industryName") or "",
        "industry_code": rec.get("industryCode") or "",
        "date": pub,
        "title": rec.get("title") or "",
        "researcher": rec.get("researcher") or "",
        "rating": rec.get("emRatingName") or "",
        "info_code": rec.get("infoCode") or "",
    }


def title_matches(rec: dict, keyword: str) -> bool:
    hay = (rec.get("title") or "") + " " + (rec.get("industryName") or "")
    return keyword in hay


# ---------------------------------------------------------------------------
# PDF 解析 / 下载（含 WAF 破解）
# ---------------------------------------------------------------------------
def resolve_pdf_url_fallback(s: requests.Session, info: str):
    """直链 404 时的兜底：抓 info 页正文里的 pdf.dfcfw.com 链接（对任意研报类型通用）。"""
    try:
        r = s.get(INFO_URL.format(info=info), timeout=30)
    except Exception as e:
        print(f"    [warn] info 页请求失败：{e}")
        return None
    m = re.search(r"https?://pdf\.dfcfw\.com/[^\"'\s?]+\.pdf", r.text)
    return m.group(0) if m else None


def solve_waf(s: requests.Session, html: str) -> bool:
    """用 node 执行 pdf.dfcfw.com 的 JS-Cookie 挑战，把 cookie 写回 session。"""
    js = re.sub(r"</?script>", "", html)
    shim = (
        "var _ck=[];"
        "var document={set cookie(v){_ck.push(v)},get cookie(){return ''}};"
        "var location={href:''};function setTimeout(){};"
        + js +
        "\nconsole.log(JSON.stringify(_ck));"
    )
    try:
        p = subprocess.run([NODE_BIN, "-e", shim], capture_output=True, text=True, timeout=20)
    except FileNotFoundError:
        print(f"    [error] 未找到 node（{NODE_BIN}），无法破解 pdf.dfcfw.com 反爬挑战")
        return False
    except Exception as e:
        print(f"    [error] node 执行挑战失败：{e}")
        return False
    try:
        cookies = json.loads(p.stdout or "[]")
    except Exception:
        print(f"    [error] 解析挑战输出失败：{p.stdout[:120]!r} {p.stderr[:120]!r}")
        return False
    ok = False
    for c in cookies:
        kv = c.split(";")[0].strip().rstrip("#").strip()
        if "=" in kv:
            k, v = kv.split("=", 1)
            s.cookies.set(k.strip(), v.strip(), domain="pdf.dfcfw.com")
            ok = True
    return ok


def download_pdf(s: requests.Session, rec: dict, dest: Path) -> int:
    """下载研报 PDF 到 dest：先试直链，遇 WAF 破解重试，真 404 走 info 页兜底。返回字节数。"""
    info = rec.get("infoCode")
    url = pdf_url_of(rec)
    r = s.get(url, timeout=60)
    # WAF 挑战：200 但正文是 <script>
    if r.content[:5] != b"%PDF-" and r.text.lstrip()[:7].startswith("<script"):
        if solve_waf(s, r.text):
            time.sleep(0.3)
            r = s.get(url, timeout=60)
    # 直链真 404/失效 → info 页兜底
    if r.content[:5] != b"%PDF-" and r.status_code == 404:
        alt = resolve_pdf_url_fallback(s, info)
        if alt:
            r = s.get(alt, timeout=60)
            if r.content[:5] != b"%PDF-" and r.text.lstrip()[:7].startswith("<script"):
                if solve_waf(s, r.text):
                    time.sleep(0.3)
                    r = s.get(alt, timeout=60)
    if r.content[:5] != b"%PDF-":
        raise RuntimeError(f"下载内容非 PDF（WAF/链接失效），status={r.status_code} 前缀={r.content[:16]!r}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(r.content)
    return len(r.content)


# ---------------------------------------------------------------------------
# 质检 + 入库
# ---------------------------------------------------------------------------
def pdf_text_quality_ok(path: Path):
    """用 pypdf 抽全文跑 rag.text_quality（ingest_pdf 文本路径本身不质检）。
    返回 (ok, 原因)。扫描版/乱码/近空会被拦下。"""
    from x_agent.rag import text_quality
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        txt = "\n".join((pg.extract_text() or "") for pg in reader.pages)
    except Exception as e:
        return False, f"pdf_read_error:{e}"
    return text_quality(txt)


def patch_extra_meta(source_id_prefix: str, meta: dict) -> None:
    """入库后把行业/个股元信息补进 chunks.extra_meta（不改共享 ingest_pdf）。"""
    from x_agent.rag import _db
    db = _db()
    db.execute(
        "UPDATE chunks SET extra_meta = json_patch(COALESCE(extra_meta,'{}'), ?) "
        "WHERE source_id LIKE ?",
        (json.dumps(meta, ensure_ascii=False), source_id_prefix + "%"),
    )
    db.commit()


def ingest_records(s: requests.Session, recs: list, report_type: str,
                   done: dict, limit: int, dry_run: bool) -> int:
    from x_agent.rag import ingest_pdf

    # 新到旧，优先补最新
    recs.sort(key=lambda r: (r.get("publishDate") or ""), reverse=True)
    got, consec_fail = 0, 0
    for rec in recs:
        if limit and got >= limit:
            break
        info = rec.get("infoCode")
        if not info:
            continue
        sid = source_id_of(rec)
        if sid in done:
            continue
        pub = (rec.get("publishDate") or "")[:10]
        title = rec.get("title") or ""
        org = rec.get("orgSName") or ""
        label = rec.get("stockName") or rec.get("industryName") or ""

        if dry_run:
            print(f"  [dry] {pub} [{org}] {label} | {title[:40]}")
            got += 1
            continue

        try:
            size = download_pdf(s, rec, TMP_PDF)
        except Exception as e:
            print(f"  [skip] 下载失败：{pub} {title[:30]} -> {e}")
            consec_fail += 1
            if consec_fail >= MAX_CONSEC_FAIL:
                print(f"  [stop] 连续 {consec_fail} 次失败，疑似 WAF/限流，停下报告。")
                break
            time.sleep(SLEEP)
            continue

        ok, reason = pdf_text_quality_ok(TMP_PDF)
        if not ok:
            print(f"  [skip] 质检未过({reason})：{pub} {title[:30]}")
            time.sleep(SLEEP)
            continue

        try:
            blocks = ingest_pdf(
                str(TMP_PDF),
                title=f"{pub} {title}",
                author=f"{org}·{rec.get('researcher') or ''}",
                source_id=sid,
                source_type="research",
                use_ocr=False,        # 研报为文字型 PDF
                skip_vectors=True,    # 向量交给 embed-all 统一生成
            )
        except Exception as e:
            print(f"  [skip] 入库失败：{pub} {title[:30]} -> {e}")
            time.sleep(SLEEP)
            continue

        consec_fail = 0
        if blocks and blocks > 0:
            patch_extra_meta(sid, extra_meta_of(rec, report_type))
            done[sid] = {
                "report_type": report_type, "title": title, "date": pub,
                "org": org, "stock_code": rec.get("stockCode") or "",
                "industry": rec.get("industryName") or "",
                "blocks": blocks, "size": size,
            }
            save_done(done)
            got += 1
            print(f"  [ok] {pub} [{org}] {label} | {title[:30]} -> {blocks} 块 / {size//1024}KB")
        else:
            print(f"  [warn] 入库返回 0 块（不计入 done）：{pub} {title[:30]}")
        time.sleep(SLEEP)

    if TMP_PDF.exists():
        try:
            TMP_PDF.unlink()
        except Exception:
            pass
    return got


# ---------------------------------------------------------------------------
# 采集编排
# ---------------------------------------------------------------------------
def collect_stock(s: requests.Session, code: str, begin: str, end: str) -> list:
    recs = list(iter_list(s, stock_params(code, begin, end)))
    print(f"  个股 {code}：命中 {len(recs)} 篇（{begin}~{end}）")
    return recs


def collect_industry(s: requests.Session, value: str, begin: str, end: str, max_pages: int) -> list:
    if value.isdigit():
        recs = list(iter_list(s, industry_params(value, begin, end)))
        print(f"  行业 industryCode={value}：命中 {len(recs)} 篇（{begin}~{end}）")
        return recs
    # 主题关键词：翻 qType=1 全窗口客户端过滤（scope 靠 --limit 只下命中项，不靠截断扫描）。
    # 先探 TotalPage：若超过 max_pages 上限会截断到"最近若干页"，必须显式告警，别静默少覆盖。
    base = keyword_params(begin, end)
    total_page = (fetch_list(s, {**base, "pageNo": 1}).get("TotalPage") or 1)
    if total_page > max_pages:
        print(f"  [warn] 窗口未覆盖完：qType=1 共 {total_page} 页，--max-pages={max_pages} 只扫最近 "
              f"{max_pages} 页(≈最近 {max_pages*100} 篇)。想全覆盖请调大 --max-pages 或改用行业代码精确过滤。")
    all_recs = list(iter_list(s, base, max_pages=max_pages))
    recs = [r for r in all_recs if title_matches(r, value)]
    print(f"  主题“{value}”：扫 {len(all_recs)} 篇(≤{max_pages}页,共{total_page}页) 命中 {len(recs)} 篇"
          f"（best-effort，非行业代码精确过滤）")
    return recs


def report_org_coverage(recs: list, label: str) -> None:
    orgs = Counter((r.get("orgSName") or "?") for r in recs)
    print(f"  [机构覆盖] {label}：{'  '.join(f'{k}({v})' for k, v in orgs.most_common())}")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def cmd_status(done: dict) -> None:
    print(f"已入库行业/个股研报：{len(done)} 篇（记录文件 {DONE_FILE}）")
    by_type = Counter(v.get("report_type", "?") for v in done.values())
    for t, n in by_type.most_common():
        print(f"  {t}: {n}")


def month_window(months: int) -> tuple:
    today = date.today()
    y, m = today.year, today.month - months
    while m <= 0:
        m += 12
        y -= 1
    return f"{y:04d}-{m:02d}-01", today.isoformat()


def main() -> None:
    ap = argparse.ArgumentParser(description="东方财富研报中心：行业研报 + 个股研报 抓取入库 RAG")
    ap.add_argument("--stocks", default="", help="逗号分隔股票代码（个股研报），如 601689,000001")
    ap.add_argument("--industry", default="", help="行业代码(如 481)或主题关键词(如 机器人)")
    ap.add_argument("--months", type=int, default=12, help="时间窗：近 N 个月（默认12）")
    ap.add_argument("--limit", type=int, default=0, help="每股/每行业最多下载 N 篇（0=不限）")
    ap.add_argument("--max-pages", type=int, default=200, help="主题关键词模式最多翻 N 页(防爆量,默认200≈覆盖1年qType=1)")
    ap.add_argument("--dry-run", action="store_true", help="只列出命中记录+机构分布，不下载入库")
    ap.add_argument("--status", action="store_true", help="打印已入库统计后退出")
    args = ap.parse_args()

    done = load_done()
    if args.status:
        cmd_status(done)
        return

    if not args.stocks and not args.industry:
        ap.error("至少指定 --stocks 或 --industry 之一（务必按主题/个股 scope，勿全量）")

    begin, end = month_window(args.months)
    print(f"时间窗：{begin} ~ {end}（近 {args.months} 月）  node={NODE_BIN}\n")
    s = make_session()
    total_new = 0

    if args.industry:
        recs = collect_industry(s, args.industry.strip(), begin, end, args.max_pages)
        report_org_coverage(recs, f"行业/主题 {args.industry}")
        total_new += ingest_records(s, recs, "hangye", done, args.limit, args.dry_run)

    for code in [c.strip() for c in args.stocks.split(",") if c.strip()]:
        recs = collect_stock(s, code, begin, end)
        report_org_coverage(recs, f"个股 {code}")
        total_new += ingest_records(s, recs, "gegu", done, args.limit, args.dry_run)

    verb = "可下载" if args.dry_run else "新入库"
    print(f"\n本次{verb} {total_new} 篇。累计已入库 {len(done)} 篇（{DONE_FILE}）。")


if __name__ == "__main__":
    main()
