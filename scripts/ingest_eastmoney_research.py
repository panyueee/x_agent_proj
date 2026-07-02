#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从东方财富研报中心 API 抓取指定分析师的历史研报 PDF 并入库 RAG。

两步走：
  第一步（覆盖调查 --survey-only）：深翻东财宏观(qType=3)+策略(qType=2)，逐年往前扫，
      客户端按 researcher 精确匹配目标分析师，统计每人命中篇数 / 最早最新日期 / 机构分布。
  第二步（下载入库，默认）：对命中的分析师下载历史研报 PDF，复用 x_agent.rag.ingest_pdf 入库。

数据源要点（均已实测验证）：
  - 列表接口 GET https://reportapi.eastmoney.com/report/jg
      参数 pageSize/pageNo/qType/beginTime/endTime；返回 {"data":[...], "hits":N, "TotalPage":P}
      qType 服务端不支持按机构/研究员过滤，只能客户端按 researcher 逗号分词精确匹配。
      jg 数据最早覆盖到 2017 年（2016 及更早返回 0）。
  - PDF 地址解析：请求研报内容页 zw_macresearch.jshtml?encodeUrl=... ，正则抓取正文里的
      https://pdf.dfcfw.com/pdf/H3_APxxxx_1.pdf 链接。
  - pdf.dfcfw.com 有 JS Cookie 反爬挑战（首次返回一段 <script> 而非 PDF）：
      用 node 执行该脚本捕获 document.cookie，把 __tst_status / EO_Bot_Ssid 写回 session，
      之后本 session 内所有 PDF 均可直接下载（一次挑战，整段有效）。
      —— 依赖外部 node（本机已装 v22）。若无 node，下载会失败并明确报错。

环境：请用 .venv/bin/python 运行（勿用坏掉的 anaconda3.8）。
"""

import argparse
import json
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
DONE_FILE = DATA_DIR / "eastmoney_research_done.json"     # 断点续传：已入库 source_id
SURVEY_FILE = DATA_DIR / "eastmoney_research_survey.md"    # 覆盖报告落盘
TMP_PDF = DATA_DIR / "_research_tmp.pdf"                   # 复用临时 PDF 路径

# 默认目标分析师（张瑜/李迅雷 预期 0，华创/中泰东财不收，用于对照）
DEFAULT_ANALYSTS = [
    "郭磊", "罗志恒", "明明", "熊园", "牟一凌", "戴康", "钟正生", "董琦",
    "芦哲", "伍戈", "邵宇", "章俊", "林荣雄", "燕翔", "张瑜", "李迅雷",
]

QTYPES = {3: "宏观", 2: "策略"}
JG_URL = "https://reportapi.eastmoney.com/report/jg"
CONTENT_URL = "https://data.eastmoney.com/report/zw_macresearch.jshtml"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://data.eastmoney.com/report/",
}
DATA_FLOOR_YEAR = 2017     # jg 接口数据下限（2016 及更早无数据）
SLEEP = 0.4                # 礼貌限速：请求间隔秒


# ---------------------------------------------------------------------------
# 会话 / 断点续传
# ---------------------------------------------------------------------------
def make_session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False   # 直连，忽略系统代理
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
# 列表抓取
# ---------------------------------------------------------------------------
def fetch_page(s: requests.Session, qtype: int, begin: str, end: str, page_no: int) -> dict:
    params = {
        "pageSize": 100,
        "pageNo": page_no,
        "qType": qtype,
        "beginTime": begin,
        "endTime": end,
    }
    r = s.get(JG_URL, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def iter_records(s: requests.Session, qtype: int, begin: str, end: str):
    """翻遍某窗口(qType×时间段)的全部研报记录。"""
    first = fetch_page(s, qtype, begin, end, 1)
    total = first.get("TotalPage") or 1
    for rec in first.get("data") or []:
        yield rec
    for p in range(2, total + 1):
        time.sleep(SLEEP)
        try:
            j = fetch_page(s, qtype, begin, end, p)
        except Exception as e:
            print(f"    [warn] qType={qtype} {begin} 第 {p} 页抓取失败：{e}")
            break
        data = j.get("data") or []
        if not data:
            break
        for rec in data:
            yield rec


def match_analysts(researcher: str, targets) -> list:
    """按逗号分词后做精确 token 匹配（避免 2 字名如“明明/戴康”子串误命中）。"""
    toks = {t.strip() for t in (researcher or "").split(",") if t.strip()}
    return [a for a in targets if a in toks]


def scan(s: requests.Session, targets, start_year: int, end_year: int) -> dict:
    """
    逐年往前扫宏观+策略，客户端精确匹配目标分析师。
    返回 {analyst: {encodeUrl: record}}（按 encodeUrl 跨 qType 去重）。
    """
    matched = {a: {} for a in targets}
    for year in range(end_year, start_year - 1, -1):
        for qtype in (3, 2):
            begin, end = f"{year}-01-01", f"{year}-12-31"
            cnt_win = 0
            for rec in iter_records(s, qtype, begin, end):
                names = match_analysts(rec.get("researcher"), targets)
                if not names:
                    continue
                enc = rec.get("encodeUrl")
                if not enc:
                    continue
                rec = dict(rec)
                rec["_qtype_name"] = QTYPES[qtype]
                for a in names:
                    if enc not in matched[a]:
                        matched[a][enc] = rec
                        cnt_win += 1
            print(f"  扫描 {year} {QTYPES[qtype]}：本窗口新增命中 {cnt_win} 篇")
            time.sleep(SLEEP)
    return matched


def build_stats(matched: dict) -> dict:
    """从命中记录汇总每人统计。"""
    stats = {}
    for a, recs in matched.items():
        dates, orgs, by_type = [], Counter(), Counter()
        for rec in recs.values():
            d = (rec.get("publishDate") or "")[:10]
            if d:
                dates.append(d)
            orgs[rec.get("orgSName") or "?"] += 1
            by_type[rec.get("_qtype_name") or "?"] += 1
        stats[a] = {
            "count": len(recs),
            "earliest": min(dates) if dates else None,
            "latest": max(dates) if dates else None,
            "orgs": orgs,
            "by_type": by_type,
        }
    return stats


def render_survey(stats: dict, targets, start_year: int, end_year: int) -> str:
    lines = []
    lines.append("# 东方财富研报覆盖调查（宏观 qType=3 + 策略 qType=2）")
    lines.append("")
    lines.append(f"- 扫描区间：{start_year}-01-01 ~ {end_year}-12-31（jg 数据下限约 {DATA_FLOOR_YEAR} 年）")
    lines.append(f"- 匹配方式：researcher 逗号分词后精确等值匹配")
    lines.append("")
    lines.append("| 分析师 | 命中篇数 | 最早 | 最新 | 宏观/策略 | 主要机构(top3) |")
    lines.append("|---|---:|---|---|---|---|")
    # 按命中篇数降序
    for a in sorted(targets, key=lambda x: -stats[x]["count"]):
        st = stats[a]
        bt = " / ".join(f"{k}{v}" for k, v in st["by_type"].most_common()) or "-"
        top = " ".join(f"{k}({v})" for k, v in st["orgs"].most_common(3)) or "-"
        lines.append(
            f"| {a} | {st['count']} | {st['earliest'] or '-'} | {st['latest'] or '-'} | {bt} | {top} |"
        )
    lines.append("")
    lines.append("## 覆盖机制说明（重要）")
    lines.append("")
    lines.append(
        "东财 jg 宏观/策略 feed 是**机构级收录**，只涵盖部分券商，并非按分析师全网收录：")
    lines.append(
        "- 已收录（实测 2025 年出现）：东吴、开源、国信、民生、粤开、中银、国金、华安、太平洋 等。")
    lines.append(
        "- 未收录：广发、国盛、中信、国泰君安/海通、东方、中泰、华创、申万宏源 等（实测计数为 0）。")
    lines.append(
        "- 因此 **命中=0 多为“其东家不被东财收录”**，而非该人不写研报：")
    lines.append(
        "  郭磊/戴康(广发)、熊园(国盛)、明明(中信)、董琦(国君)、邵宇(东方)、李迅雷(中泰)、张瑜(华创,故仅剩其早年民生时期) 均属此列。")
    lines.append(
        "- **“最早日期”反映其在被收录机构的任职区间，不是东财数据下限**：")
    lines.append(
        "  如钟正生止于 2020-02（之后转平安证券，不收录）、芦哲始于 2024-11（此前中信/德邦不在此 feed）。")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# PDF 解析 / 下载（含 WAF 挑战破解）
# ---------------------------------------------------------------------------
def resolve_pdf_url(s: requests.Session, enc: str):
    """请求研报内容页，正则抓取 pdf.dfcfw.com 上的 PDF 直链。"""
    try:
        r = s.get(CONTENT_URL, params={"encodeUrl": enc}, timeout=30)
    except Exception as e:
        print(f"    [warn] 内容页请求失败：{e}")
        return None
    m = re.search(r"https?://pdf\.dfcfw\.com/[^\"'\s?]+\.pdf", r.text)
    return m.group(0) if m else None


def solve_waf(s: requests.Session, html: str) -> bool:
    """用 node 执行 pdf.dfcfw.com 的 JS Cookie 挑战，把 cookie 写回 session。"""
    js = re.sub(r"</?script>", "", html)
    shim = (
        "var _ck=[];"
        "var document={set cookie(v){_ck.push(v)},get cookie(){return ''}};"
        "var location={href:''};function setTimeout(){};"
        + js +
        "\nconsole.log(JSON.stringify(_ck));"
    )
    try:
        p = subprocess.run(["node", "-e", shim], capture_output=True, text=True, timeout=20)
    except FileNotFoundError:
        print("    [error] 未找到 node，无法破解 pdf.dfcfw.com 反爬挑战")
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


def download_pdf(s: requests.Session, url: str, dest: Path) -> int:
    """下载 PDF 到 dest；遇 WAF 挑战自动破解后重试。返回字节数；失败抛异常。"""
    r = s.get(url, timeout=60)
    if r.content[:5] != b"%PDF-" and r.text.lstrip()[:7].startswith("<script"):
        if solve_waf(s, r.text):
            time.sleep(0.3)
            r = s.get(url, timeout=60)
    if r.content[:5] != b"%PDF-":
        raise RuntimeError(f"下载内容非 PDF（WAF 未通过或链接失效），前缀={r.content[:16]!r}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(r.content)
    return len(r.content)


# ---------------------------------------------------------------------------
# 入库
# ---------------------------------------------------------------------------
def ingest_records(s: requests.Session, matched: dict, targets, done: dict, limit: int) -> None:
    from x_agent.rag import ingest_pdf   # 复用现成入库逻辑，勿自写解析

    total_new = 0
    for a in targets:
        recs = list(matched[a].values())
        # 新到旧排序，优先补最新
        recs.sort(key=lambda r: (r.get("publishDate") or ""), reverse=True)
        got = 0
        print(f"\n=== 下载入库 {a}（命中 {len(recs)} 篇）===")
        for rec in recs:
            if limit and got >= limit:
                break
            enc = rec.get("encodeUrl")
            sid = f"research:em:{enc}"
            if sid in done:
                continue
            title = rec.get("title") or ""
            pub = (rec.get("publishDate") or "")[:10]
            org = rec.get("orgSName") or ""
            researcher = rec.get("researcher") or ""

            pdf_url = resolve_pdf_url(s, enc)
            if not pdf_url:
                print(f"  [skip] 未解析到 PDF 链接：{pub} {title[:30]}")
                time.sleep(SLEEP)
                continue
            try:
                size = download_pdf(s, pdf_url, TMP_PDF)
            except Exception as e:
                print(f"  [skip] 下载失败：{pub} {title[:30]} -> {e}")
                time.sleep(SLEEP)
                continue

            try:
                blocks = ingest_pdf(
                    str(TMP_PDF),
                    title=f"{pub} {title}",
                    author=f"{org}·{researcher}",
                    source_id=sid,
                    source_type="research",
                    use_ocr=False,      # 研报为文字型 PDF
                    skip_vectors=True,  # 向量交给 embed-all 统一生成
                )
            except Exception as e:
                print(f"  [skip] 入库失败：{pub} {title[:30]} -> {e}")
                time.sleep(SLEEP)
                continue

            if blocks and blocks > 0:
                done[sid] = {
                    "analyst": a,
                    "title": title,
                    "date": pub,
                    "org": org,
                    "blocks": blocks,
                    "size": size,
                }
                save_done(done)   # 每篇成功即落盘，便于断点续传
                total_new += 1
                got += 1
                print(f"  [ok] {pub} {title[:34]} -> {blocks} 块 / {size//1024}KB")
            else:
                print(f"  [warn] 入库返回 0 块（不计入 done）：{pub} {title[:30]}")
            time.sleep(SLEEP)

    if TMP_PDF.exists():
        try:
            TMP_PDF.unlink()
        except Exception:
            pass
    print(f"\n本次新入库 {total_new} 篇。累计已入库 {len(done)} 篇。")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def cmd_status(done: dict) -> None:
    print(f"已入库研报：{len(done)} 篇（记录文件 {DONE_FILE}）")
    by_analyst = Counter(v.get("analyst", "?") for v in done.values())
    for a, n in by_analyst.most_common():
        print(f"  {a}: {n}")


def main() -> None:
    ap = argparse.ArgumentParser(description="东方财富研报中心：抓取分析师历史研报入库 RAG")
    ap.add_argument("--analysts", default="", help="逗号分隔分析师名，默认内置全表")
    ap.add_argument("--survey-only", action="store_true", help="只做覆盖调查报告，不下载")
    ap.add_argument("--years", type=int, default=3, help="下载模式往前几年（默认3）；覆盖调查恒扫到数据下限")
    ap.add_argument("--limit", type=int, default=0, help="每人最多下载 N 篇（调试用，0=不限）")
    ap.add_argument("--status", action="store_true", help="打印已入库统计后退出")
    args = ap.parse_args()

    done = load_done()
    if args.status:
        cmd_status(done)
        return

    targets = [x.strip() for x in args.analysts.split(",") if x.strip()] or list(DEFAULT_ANALYSTS)
    this_year = date.today().year

    s = make_session()

    # 覆盖调查：恒扫到数据下限，给出真实的历史最早篇；下载：只扫 --years 窗口
    if args.survey_only:
        start_year = DATA_FLOOR_YEAR
    else:
        start_year = max(DATA_FLOOR_YEAR, this_year - args.years + 1)

    print(f"目标分析师({len(targets)})：{'、'.join(targets)}")
    print(f"扫描区间：{start_year} ~ {this_year}，模式：{'覆盖调查' if args.survey_only else '下载入库'}\n")

    matched = scan(s, targets, start_year, this_year)
    stats = build_stats(matched)

    report = render_survey(stats, targets, start_year, this_year)
    print("\n" + report)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SURVEY_FILE.write_text(report, encoding="utf-8")
    print(f"覆盖报告已写入 {SURVEY_FILE}")

    if args.survey_only:
        return

    ingest_records(s, matched, targets, done, args.limit)


if __name__ == "__main__":
    main()
