#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
知识星球（zsxq.com）帖子【附件 PDF 文件】下载 → OCR → 入库 RAG。

功能：翻页拉取指定星球的 topics → 取 topic["talk"]["files"] 中的 .pdf 附件 →
      取下载地址 → 下载到临时文件 → 复用 x_agent.rag.ingest_pdf 入库
      （source_type="zsxq"）；source_id 去重 + 全局限流间隔 + 断点续传。

v1 只处理【文件型 PDF 附件】，不处理图片（图片 OCR 产出稀疏数字会被质检拒且
翻倍下载量，已在设计评审时砍掉）。

用法（需先 export ZSXQ_TOKEN 或 --token 传入；--auth-mode 默认 cookie）：
  .venv/bin/python scripts/ingest_zsxq_files.py --group-id 111 --limit 1 --dry-run
  .venv/bin/python scripts/ingest_zsxq_files.py --group-id 111,222 --limit 5
  .venv/bin/python scripts/ingest_zsxq_files.py --status
  .venv/bin/python scripts/ingest_zsxq_files.py --selftest      # 离线样例自测，绝不联网

===============================================================================
⚠️ 重要说明：未对真实 zsxq API 做过任何联网实测（账号安全）
-------------------------------------------------------------------------------
- 用户 zsxq 账号曾触发过频率限制（code=1059）。本脚本在开发/自测阶段
  【绝不】向 api.zsxq.com 发任何真实请求，仅用内置样例跑 --selftest。
- 真实联网验收须由主控亲自慢速执行（建议先 `--limit 1 --dry-run`，
  再 `--limit 1` 真跑一条），务必在跑通前确认以下三点：
    1) download_url 响应形状：现按 resp["resp_data"]["download_url"]，
       兜底试 resp["resp_data"]["url"]，真实字段名需以首个响应为准。
    2) file 对象字段名：现按 file_id / id 兜底取 ID，name 取文件名，
       真实字段名需以首个响应为准。
    3) download_url 的 host 是否为「签名 CDN / OSS」——本脚本会在首个真实
       下载时 print 出 host，据此判断能否全量抓取（签名 URL 一般有时效）。
- 限流安全：全局最小调用间隔（env ZSXQ_MIN_INTERVAL，默认 5s）；
  遇 code==1988（限流）由复用的 api_get 退避重试；遇 code==1059（频率/滥用
  信号）立即冷却 ZSXQ_COOLDOWN_1059（默认 300s）并醒目告警，绝不几秒重试。
===============================================================================
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path

# ── 项目根路径 & scripts 目录（供 import x_agent.rag 与复用 ingest_zsxq helper）──
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# ── 网络直连准备：避开系统代理 + 修正证书 ────────────────────────────────────────
try:
    import certifi
    os.environ["SSL_CERT_FILE"] = certifi.where()
except Exception:  # certifi 缺失也不致命
    pass

import requests  # noqa: E402  （放在证书设置之后 import）

# 复用 scripts/ingest_zsxq.py 的已验证 helper（cookie 鉴权、限流退避 GET、翻页）
from ingest_zsxq import make_session, api_get, iter_topics, clean_text  # noqa: E402
# PDF 入库复用 rag.ingest_pdf（内部含 文字型/扫描型判定 + 去重 + 断点续传）
from x_agent.rag import ingest_pdf  # noqa: E402

# ── 常量 ──────────────────────────────────────────────────────────────────────
API_BASE = "https://api.zsxq.com/v2"
DONE_PATH = ROOT / "data" / "zsxq_files_done.json"    # 仅入库成功后记录，断点续传
TMP_DIR = ROOT / "data"                                # 临时 PDF 落地目录
DEFAULT_MIN_INTERVAL = 5.0                             # 全局最小调用间隔（秒）
COOLDOWN_1059 = float(os.environ.get("ZSXQ_COOLDOWN_1059", "300"))  # 命中 1059 冷却


# ── 断点续传：已入库 source_id 集合 ────────────────────────────────────────────
def load_done() -> set[str]:
    if DONE_PATH.exists():
        try:
            return set(json.loads(DONE_PATH.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()


def save_done(done: set[str]) -> None:
    DONE_PATH.parent.mkdir(parents=True, exist_ok=True)
    DONE_PATH.write_text(
        json.dumps(sorted(done), ensure_ascii=False, indent=0), encoding="utf-8"
    )


# ── 全局限流间隔（所有 zsxq API 调用间的最小间隔）───────────────────────────────
_last_call_ts = 0.0


def polite_sleep() -> None:
    """确保两次 zsxq API 调用之间至少间隔 ZSXQ_MIN_INTERVAL 秒（全局）。

    注：翻页 iter_topics 内部另有自己的温柔间隔（复用代码，无法注入）；
    本函数用于本脚本额外发起的 download_url 调用，进一步降低触限概率。
    """
    global _last_call_ts
    try:
        interval = float(os.environ.get("ZSXQ_MIN_INTERVAL", DEFAULT_MIN_INTERVAL))
    except ValueError:
        interval = DEFAULT_MIN_INTERVAL
    now = time.monotonic()
    wait = interval - (now - _last_call_ts)
    if wait > 0:
        time.sleep(wait)
    _last_call_ts = time.monotonic()


# ── 纯函数：便于 --selftest 离线校验，不触发任何网络 ───────────────────────────
def topic_files(topic: dict) -> list[dict]:
    """从 topic 中取附件文件列表。主位置 talk.files，兜底顶层 files。无 → []。"""
    talk = topic.get("talk") or {}
    files = talk.get("files")
    if not files:
        files = topic.get("files")  # 顶层兜底（形状未确认）
    return files if isinstance(files, list) else []


def pick_file_id(file: dict):
    """防御性取 file_id：优先 file_id，兜底 id。"""
    return file.get("file_id") or file.get("id")


def is_pdf(file: dict) -> bool:
    """按 name 后缀判断是否 PDF（大小写不敏感）。"""
    name = file.get("name") or ""
    return name.lower().endswith(".pdf")


def build_source_id(gid, topic_id, file_id) -> str:
    """统一 source_id 规则：zsxq:<gid>:<topic_id>:file:<file_id>。"""
    return f"zsxq:{gid}:{topic_id}:file:{file_id}"


def parse_download_url(resp: dict) -> str | None:
    """防御性解析下载地址响应：resp_data.download_url，兜底 resp_data.url。"""
    rd = resp.get("resp_data") or {}
    return rd.get("download_url") or rd.get("url")


# ── zsxq API：取下载地址（带 1059 冷却）─────────────────────────────────────────
def get_download_url(sess: requests.Session, file_id) -> str | None:
    """GET /v2/files/{file_id}/download_url，返回下载地址（防御性取字段）。

    1988（限流）由复用的 api_get 内部退避重试；1059（频率/滥用信号）在此
    醒目告警并冷却 COOLDOWN_1059 秒后再抛出，绝不几秒内重试。
    """
    polite_sleep()
    url = f"{API_BASE}/files/{file_id}/download_url"
    try:
        resp = api_get(sess, url)
    except RuntimeError as e:
        if "1059" in str(e):
            print(
                "\n" + "=" * 60 +
                f"\n⚠️⚠️⚠️  命中 zsxq 频率/滥用信号 code=1059！\n"
                f"    立即停止请求并冷却 {COOLDOWN_1059:.0f}s，请勿继续跑。\n"
                + "=" * 60,
                file=sys.stderr,
            )
            time.sleep(COOLDOWN_1059)
        raise
    return parse_download_url(resp)


# ── 下载文件字节（用不带 zsxq 鉴权的干净会话，避免把 token 泄露给 CDN）─────────
def _stream_download(sess: requests.Session, url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with sess.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                if chunk:
                    f.write(chunk)


def download_to(url: str, dest: Path, auth_sess: requests.Session | None = None) -> None:
    """流式下载 url 到 dest。
    先用免 token 干净会话（对签名 CDN/OSS 链接正确，且不把 token 泄露给第三方）；
    若返回 401/403（说明该链接需 zsxq 鉴权），再用带鉴权的 auth_sess 兜底重试。
    """
    clean = requests.Session()
    clean.trust_env = False
    try:
        _stream_download(clean, url, dest)
    except requests.HTTPError as e:
        code = getattr(e.response, "status_code", None)
        if code in (401, 403) and auth_sess is not None:
            print(f"[下载] 免token 收到 {code}，改用 zsxq 鉴权会话重试", file=sys.stderr)
            _stream_download(auth_sess, url, dest)
        else:
            raise


# ── 主流程 ────────────────────────────────────────────────────────────────────
_printed_host = False  # 首个真实下载打印一次 host，供主控判断是否签名 CDN


def process_group(sess, gid: str, gname: str, done: set[str],
                  limit: int | None, dry_run: bool) -> dict:
    """处理单个星球：遍历帖子 → 挑 PDF 附件 → 下载入库。返回统计。"""
    global _printed_host
    stat = {"topics": 0, "pdf_found": 0, "skipped_nonpdf": 0,
            "skipped_done": 0, "ingested": 0, "failed": 0}

    for topic in iter_topics(sess, gid, limit):
        stat["topics"] += 1
        topic_id = topic.get("topic_id")

        for file in topic_files(topic):
            file_id = pick_file_id(file)
            name = file.get("name") or ""
            if file_id is None:
                print(f"[跳过] 无法取到 file_id：{file!r}", file=sys.stderr)
                continue
            if not is_pdf(file):
                stat["skipped_nonpdf"] += 1
                print(f"[跳过非PDF] {name!r} (file_id={file_id})", file=sys.stderr)
                continue
            # 标题下载前即可算（日期·主题·报告名）；来源(author)下载后从PDF内容识别
            title = build_title(topic, name)

            stat["pdf_found"] += 1
            source_id = build_source_id(gid, topic_id, file_id)
            if source_id in done:
                stat["skipped_done"] += 1
                continue

            if dry_run:
                print(f"[dry-run] 将下载: {source_id}  name={name!r}  title={title[:30]!r}")
                continue

            # 取下载地址
            try:
                dl_url = get_download_url(sess, file_id)
            except Exception as e:
                stat["failed"] += 1
                print(f"[失败] 取下载地址异常 {source_id}: {e}", file=sys.stderr)
                # 1059 已在 get_download_url 冷却过；此处直接终止本星球，避免继续触限
                if "1059" in str(e):
                    print("[中止] 因 code=1059 终止本星球剩余处理。", file=sys.stderr)
                    return stat
                continue
            if not dl_url:
                stat["failed"] += 1
                print(f"[失败] 下载地址为空（响应形状需确认）: {source_id}", file=sys.stderr)
                continue

            # 首个真实下载：打印 host，供主控判断是否签名 CDN / OSS（能否全量抓）
            if not _printed_host:
                host = urllib.parse.urlparse(dl_url).netloc
                print(f"[首个下载 host] {host}  ← 判断是否签名 CDN/OSS 及时效")
                _printed_host = True

            tmp_pdf = TMP_DIR / f"_zsxq_tmp_{file_id}.pdf"
            try:
                download_to(dl_url, tmp_pdf, auth_sess=sess)
                # 来源：从下载好的PDF内容识别出品机构(BofA/大摩/华创…)，识别不到退回渠道
                author = resolve_source(str(tmp_pdf), gname)
                n = ingest_pdf(
                    str(tmp_pdf),
                    title=title,
                    author=author,
                    source_id=source_id,
                    source_type="zsxq",
                    use_ocr=True,
                    skip_vectors=True,
                )
                # ingest_pdf 返回 ≥1（新入库）或 0（已去重/无文本）；无异常即视为成功
                if n >= 0:
                    done.add(source_id)
                    save_done(done)  # 每成功一条即落盘，中断可续
                    stat["ingested"] += 1
                    print(f"[入库OK] {source_id}  chunks+={n}  name={name!r}")
            except Exception as e:
                stat["failed"] += 1
                print(f"[失败] 下载/入库异常 {source_id}: {e}", file=sys.stderr)
            finally:
                try:
                    tmp_pdf.unlink(missing_ok=True)
                except Exception:
                    pass

    return stat


def _topic_date(topic: dict) -> str:
    """帖子发布日期 YYYY-MM-DD（取 create_time 前 10 位）。"""
    return (topic.get("create_time") or "")[:10]


def _topic_theme(topic: dict) -> str:
    """帖子主题：正文剥 <e> 标签/解实体后的干净文字（clean_text）。"""
    talk = topic.get("talk") or {}
    return clean_text(talk.get("text") or "").strip()


# 研报机构识别表：(规范名, [小写匹配模式…])。来源=从PDF内容解析出的出品机构，
# 而非"知识星球"这个渠道（渠道已由 source_type=zsxq + source_id 记录）。
RESEARCH_FIRMS = [
    ("BofA Securities", ["bofa securities", "merrill lynch", "bank of america"]),
    ("Morgan Stanley", ["morgan stanley"]),
    ("Goldman Sachs", ["goldman sachs"]),
    ("J.P. Morgan", ["j.p. morgan", "jpmorgan", "jp morgan"]),
    ("UBS", ["ubs securities", "ubs ag", "ubs limited"]),
    ("Citi", ["citigroup", "citi research", "citibank"]),
    ("Nomura", ["nomura"]),
    ("Jefferies", ["jefferies"]),
    ("Barclays", ["barclays"]),
    ("HSBC", ["hsbc"]),
    ("Macquarie", ["macquarie"]),
    ("Daiwa", ["daiwa"]),
    ("Mizuho", ["mizuho"]),
    ("Deutsche Bank", ["deutsche bank"]),
    ("Credit Suisse", ["credit suisse"]),
    ("Bernstein", ["bernstein"]),
    ("Wolfe Research", ["wolfe research"]),
    ("华创证券", ["华创证券"]),
    ("中泰证券", ["中泰证券"]),
    ("中金公司", ["中金公司", "china international capital"]),
    ("国泰君安", ["国泰君安"]),
    ("中信证券", ["中信证券"]),
    ("招商证券", ["招商证券"]),
    ("广发证券", ["广发证券"]),
]


def detect_firm(text: str) -> str | None:
    """从研报文字（通常首页页眉/免责声明）识别出品机构。命中返回规范名，否则 None。"""
    low = (text or "").lower()
    for name, pats in RESEARCH_FIRMS:
        if any(p in low for p in pats):
            return name
    return None


def pdf_head_text(path: str, max_pages: int = 2, max_chars: int = 4000) -> str:
    """取 PDF 前 max_pages 页的文字层（文字型PDF直接可得；扫描型返回空→来源退回渠道）。"""
    try:
        import fitz
        doc = fitz.open(path)
        parts = []
        for i in range(min(max_pages, doc.page_count)):
            parts.append(doc.load_page(i).get_text())
            if sum(len(p) for p in parts) >= max_chars:
                break
        doc.close()
        return "".join(parts)[:max_chars]
    except Exception:
        return ""


def build_title(topic: dict, file_name: str) -> str:
    """title = "YYYY-MM-DD · <主题(剥<e>标签)> · <报告名>"。"""
    date = _topic_date(topic)
    theme = _topic_theme(topic)
    stem = re.sub(r"\.pdf$", "", file_name, flags=re.I).strip()
    return " · ".join(x for x in [date, theme[:60], stem] if x)[:200]


def resolve_source(pdf_path: str, gname: str) -> str:
    """来源(author)：优先从PDF内容识别出的研报机构；识别不到退回渠道"知识星球·<星球名>"。"""
    firm = detect_firm(pdf_head_text(pdf_path))
    if firm:
        return firm
    return f"知识星球·{gname}" if gname else "知识星球"


def resolve_group_names(sess, gids: list[str]) -> dict[str, str]:
    """尽力取星球名（失败不致命，返回空名）。复用 ingest_zsxq 的 groups 接口。"""
    name_map: dict[str, str] = {}
    try:
        from ingest_zsxq import list_groups
        polite_sleep()
        for g in list_groups(sess):
            name_map[str(g.get("group_id"))] = g.get("name", "")
    except Exception:
        pass
    return name_map


# ── 状态 ──────────────────────────────────────────────────────────────────────
def print_status() -> None:
    done = load_done()
    print(f"已入库 PDF 附件 source_id 数：{len(done)}")
    per_group: dict[str, int] = {}
    for sid in done:
        parts = sid.split(":")
        if len(parts) >= 2:
            per_group[parts[1]] = per_group.get(parts[1], 0) + 1
    for gid, n in sorted(per_group.items()):
        print(f"  星球 {gid}: {n} 个 PDF")
    print(f"done 文件：{DONE_PATH}")


# ── 自测（离线，无需 token / 网络）────────────────────────────────────────────
SELFTEST_TOPICS = [
    {
        "topic_id": 5001,
        "type": "talk",
        "talk": {
            "text": "本周研报合集，见附件。\n第二行内容。",
            "files": [
                {"file_id": 111, "name": "行业研报.pdf", "size": 12345},
                {"id": 222, "name": "配图.png"},          # 非 pdf → 跳过
                {"file_id": 333, "name": "NOTE.PDF"},       # 大写后缀 → 命中
            ],
        },
    },
    {
        "topic_id": 5002,
        "type": "talk",
        "talk": {"text": "只是一条纯文字动态，没有附件。"},  # 无 files → 跳过
    },
    {
        "topic_id": 5003,
        "type": "q&a",
        "question": {"text": "无 talk 字段的帖子"},          # 无 talk → 无 files
    },
]

FAKE_DL_RESP_DOWNLOAD_URL = {
    "succeeded": True, "code": 0,
    "resp_data": {"download_url": "https://signed-cdn.zsxq.example.com/f/111.pdf?sig=abc&e=1699"},
}
FAKE_DL_RESP_URL = {
    "succeeded": True, "code": 0,
    "resp_data": {"url": "https://oss.example.com/y.pdf"},   # 兜底字段
}
FAKE_DL_RESP_EMPTY = {"succeeded": True, "code": 0, "resp_data": {}}


def run_selftest() -> int:
    print("=== 离线样例自测（绝不联网）===")

    # 1) 取 files：talk.files 命中，无 files / 无 talk 的帖返回 []
    f0 = topic_files(SELFTEST_TOPICS[0])
    f1 = topic_files(SELFTEST_TOPICS[1])
    f2 = topic_files(SELFTEST_TOPICS[2])
    assert len(f0) == 3, f"talk.files 抽取错误：{f0!r}"
    assert f1 == [], "无附件帖应返回空列表"
    assert f2 == [], "无 talk 帖应返回空列表"

    # 2) PDF 过滤：应挑出 111(.pdf) 与 333(.PDF)，跳过 222(.png)
    pdfs = [f for f in f0 if is_pdf(f)]
    ids = [pick_file_id(f) for f in pdfs]
    assert ids == [111, 333], f"PDF 过滤结果错误：{ids!r}"
    assert not is_pdf(f0[1]), "非 pdf(.png) 不应被判为 pdf"

    # 3) 防御性 file_id 兜底：{'id': 222} 应能取到 222
    assert pick_file_id({"id": 222}) == 222, "file_id 兜底 id 失效"
    assert pick_file_id({"file_id": 9}) == 9, "file_id 优先失效"

    # 4) source_id 格式
    sid = build_source_id("99", 5001, 111)
    assert sid == "zsxq:99:5001:file:111", f"source_id 格式错误：{sid!r}"

    # 5) 下载地址防御性解析：download_url 优先、url 兜底、空返回 None
    assert parse_download_url(FAKE_DL_RESP_DOWNLOAD_URL) == \
        "https://signed-cdn.zsxq.example.com/f/111.pdf?sig=abc&e=1699", "download_url 解析错误"
    assert parse_download_url(FAKE_DL_RESP_URL) == "https://oss.example.com/y.pdf", "url 兜底解析错误"
    assert parse_download_url(FAKE_DL_RESP_EMPTY) is None, "空响应应返回 None"

    # 6) title 构建：主题(剥标签)+报告名 编入
    title = build_title(SELFTEST_TOPICS[0], "报告A.pdf")
    assert "本周研报合集" in title, f"主题未进标题：{title!r}"
    assert "报告A" in title and "报告A.pdf" not in title, f"报告名处理错误：{title!r}"

    # 7) 来源=从PDF内容识别机构；识别不到退回渠道
    assert detect_firm("... BofA Securities does and seeks to do business ...") == "BofA Securities"
    assert detect_firm("摩根士丹利 Morgan Stanley Research") == "Morgan Stanley"
    assert detect_firm("华创证券研究所 宏观") == "华创证券"
    assert detect_firm("这是一段没有机构名的普通文字") is None
    # resolve_source 无法命中(空文本路径)时退回渠道
    assert detect_firm(pdf_head_text("/不存在的路径.pdf")) is None

    print("全部断言通过 ✅（仅离线样例，未联网实测真实 zsxq API）")
    return 0


# ── CLI ───────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="知识星球帖子 PDF 附件下载入库 RAG")
    ap.add_argument("--group-id", help="星球 ID（必填），可逗号分隔多个")
    ap.add_argument("--limit", type=int, default=3, help="每星球最多处理 N 个帖（默认 3）")
    ap.add_argument("--status", action="store_true", help="查看已入库进度")
    ap.add_argument("--dry-run", action="store_true", help="只列出会下载哪些 file，不真下不入库")
    ap.add_argument("--selftest", action="store_true", help="离线样例自测（绝不联网）")
    ap.add_argument("--group-name", help="显式指定星球名（覆盖联网解析，省一次API且稳定）")
    ap.add_argument("--token", help="zsxq token（否则读 env ZSXQ_TOKEN）")
    ap.add_argument("--auth-mode", choices=["cookie", "header"],
                    default=os.environ.get("ZSXQ_AUTH_MODE", "cookie"),
                    help="鉴权形式：cookie(默认，已验证) 或 header")
    args = ap.parse_args()

    if args.selftest:
        return run_selftest()
    if args.status:
        print_status()
        return 0

    if not args.group_id:
        print("错误：需要 --group-id（可逗号分隔多个）", file=sys.stderr)
        return 2
    gids = [x.strip() for x in args.group_id.split(",") if x.strip()]

    token = args.token or os.environ.get("ZSXQ_TOKEN", "")
    if not token:
        print("错误：需要 zsxq token。请 export ZSXQ_TOKEN=... 或 --token ...", file=sys.stderr)
        return 2

    sess = make_session(token, args.auth_mode)
    # 显式 --group-name 覆盖（单星球场景）→ 跳过联网解析，省一次 API 且稳定
    if args.group_name and len(gids) == 1:
        name_map = {gids[0]: args.group_name}
    else:
        name_map = resolve_group_names(sess, gids) if not args.dry_run else {}

    done = load_done()
    grand = {"topics": 0, "pdf_found": 0, "skipped_nonpdf": 0,
             "skipped_done": 0, "ingested": 0, "failed": 0}
    try:
        for gid in gids:
            gname = name_map.get(gid, "")
            print(f"\n===== 星球 {gid} {gname} =====")
            stat = process_group(sess, gid, gname, done, args.limit, args.dry_run)
            for k in grand:
                grand[k] += stat.get(k, 0)
            print(f"  统计: {stat}")
    finally:
        if not args.dry_run:
            save_done(done)

    print(f"\n===== 汇总 =====\n{grand}")
    if not args.dry_run:
        print(f"done 已保存：{DONE_PATH}（共 {len(done)} 条 source_id）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
