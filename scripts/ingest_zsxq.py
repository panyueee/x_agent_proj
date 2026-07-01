#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
知识星球（zsxq.com）已订阅星球帖子拉取入库 RAG。

功能：翻页拉取用户已加入星球的 topics → 抽取正文纯文本 → text_quality 质检 →
      ingest_text 入库（source_type="zsxq"）；topic_id 去重 + 限流退避 + 断点续传。

用法（需先 export ZSXQ_TOKEN 或 --token 传入）：
  .venv/bin/python scripts/ingest_zsxq.py --list-groups            # 只列出我的星球
  .venv/bin/python scripts/ingest_zsxq.py --group-id 111,222       # 指定星球入库
  .venv/bin/python scripts/ingest_zsxq.py --all --limit 20         # 全部星球，每个最多20条
  .venv/bin/python scripts/ingest_zsxq.py --group-id 111 --dry-run # 只解析打印不入库
  .venv/bin/python scripts/ingest_zsxq.py --status                 # 看已入库进度
  .venv/bin/python scripts/ingest_zsxq.py --selftest               # 离线样例解析自测

===============================================================================
重要说明（未对真实 zsxq API 实测）：
- 本脚本仅通过内置样例响应验证了「正文抽取 + 标签清洗」逻辑（见 --selftest），
  尚未对真实 api.zsxq.com 做过联网实测。
- 待用户提供 ZSXQ_TOKEN 后，请先跑 --list-groups / --dry-run 做真实实测。
- 鉴权形式（Cookie: zsxq_access_token=<token>  vs  Authorization: <token> header）
  存在两种可能，本脚本做成可配置（--auth-mode / env ZSXQ_AUTH_MODE），
  真正跑通前需按「首个真实响应」确认到底是哪一种，不要假设只有一种。
- 翻页 end_time 有经典 off-by-one，本脚本以 topic_id 去重并在「整页均为已见」时停止，
  以规避重复返回 / 死循环。
===============================================================================
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path

# ── 项目根路径，供 import x_agent.rag ───────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── 网络直连准备：避开系统 clash 代理 + 修正证书 ─────────────────────────────────
try:
    import certifi
    os.environ["SSL_CERT_FILE"] = certifi.where()
except Exception:  # certifi 缺失也不致命，只是走系统默认证书
    pass

import requests  # noqa: E402  （放在证书设置之后 import）

from x_agent.rag import ingest_text, text_quality  # noqa: E402

# ── 常量 ──────────────────────────────────────────────────────────────────────
API_BASE = "https://api.zsxq.com/v2"
DONE_PATH = ROOT / "data" / "zsxq_done.json"     # 已入库 source_id 断点续传
PAGE_COUNT = 20                                   # 单页 topics 数
MAX_RETRY = 5                                     # 限流退避最大重试
BASE_BACKOFF = 3.0                                # 退避基数秒


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


# ── HTTP 会话 ─────────────────────────────────────────────────────────────────
def make_session(token: str, auth_mode: str) -> requests.Session:
    """构造直连 Session。auth_mode ∈ {cookie, header}。

    两种鉴权形式都可能，做成可配置；真正跑通前需按首个真实响应确认。
    """
    s = requests.Session()
    s.trust_env = False  # 关键：忽略系统 http(s)_proxy，避开 clash 代理
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://wx.zsxq.com/",
        "Origin": "https://wx.zsxq.com",
    })
    if auth_mode == "cookie":
        s.headers["Cookie"] = f"zsxq_access_token={token}"
    elif auth_mode == "header":
        s.headers["Authorization"] = token
    else:
        raise ValueError(f"未知 auth_mode: {auth_mode}（应为 cookie 或 header）")
    return s


def api_get(sess: requests.Session, url: str, params: dict | None = None) -> dict:
    """带限流退避的 GET。zsxq 限流返回 code==1988 → sleep 指数退避重试。"""
    last_err = None
    for attempt in range(MAX_RETRY):
        try:
            resp = sess.get(url, params=params, timeout=20)
        except Exception as e:  # 网络抖动也退避重试
            last_err = e
            time.sleep(BASE_BACKOFF * (2 ** attempt))
            continue
        # HTTP 429 也当限流处理
        if resp.status_code == 429:
            time.sleep(BASE_BACKOFF * (2 ** attempt))
            continue
        try:
            data = resp.json()
        except Exception as e:
            last_err = e
            raise RuntimeError(f"响应非 JSON（HTTP {resp.status_code}）: {resp.text[:200]}")
        code = data.get("code")
        if code == 1988:  # zsxq 限流
            wait = BASE_BACKOFF * (2 ** attempt)
            print(f"[限流] code=1988，退避 {wait:.0f}s 后重试（第 {attempt + 1} 次）", file=sys.stderr)
            time.sleep(wait)
            continue
        if not data.get("succeeded", code == 0):
            raise RuntimeError(f"zsxq 接口失败: code={code} body={json.dumps(data, ensure_ascii=False)[:300]}")
        return data
    raise RuntimeError(f"重试 {MAX_RETRY} 次仍失败: {last_err}")


# ── 正文抽取与清洗 ────────────────────────────────────────────────────────────
# zsxq 正文中嵌入标签形如：
#   <e type="hashtag" hid="..." title="%23话题%23" />
#   <e type="mention" uid="..." title="%40某人" />
#   <e type="web" href="..." title="链接标题" />
#   <e type="text_bold" title="加粗文字" />
# title 为 URL 编码；策略：能取回 title 的以其解码文本替换标签，取不到则整体删除。
_E_TAG = re.compile(r"<e\b[^>]*/?>", re.IGNORECASE)
_TITLE_ATTR = re.compile(r'title="([^"]*)"', re.IGNORECASE)


def _replace_e_tag(m: re.Match) -> str:
    """把单个 <e .../> 标签替换为其 title 的解码文本（无 title 则删除）。"""
    tag = m.group(0)
    tm = _TITLE_ATTR.search(tag)
    if not tm:
        return ""
    try:
        return urllib.parse.unquote(tm.group(1))
    except Exception:
        return tm.group(1)


def clean_text(raw: str | None) -> str:
    """剥掉 <e> 嵌入标签、解 HTML 实体、规整空白，返回纯文本。"""
    if not raw:
        return ""
    txt = _E_TAG.sub(_replace_e_tag, raw)   # 处理嵌入标签
    txt = re.sub(r"<[^>]+>", "", txt)        # 兜底：清掉其他残留 HTML 标签
    txt = html.unescape(txt)                 # 解 &amp; &lt; 等实体
    txt = re.sub(r"[ \t]+\n", "\n", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return txt.strip()


def extract_topic(topic: dict) -> tuple[str, str]:
    """按 type 抽取 (title, body_text)。

    type 常见：talk（动态）/ q&a（问答）/ article（文章）。正文位置随 type 变。
    title 取帖子标题或正文首行摘要。
    """
    ttype = topic.get("type", "")
    parts: list[str] = []
    title = ""

    if ttype == "talk":
        talk = topic.get("talk", {}) or {}
        body = clean_text(talk.get("text", ""))
        # talk 可能带 article（附带文章）
        art = talk.get("article") or {}
        if art:
            atitle = clean_text(art.get("title", ""))
            aurl = art.get("article_url") or art.get("url") or ""
            if atitle:
                parts.append(f"[文章] {atitle}")
                title = atitle
            if aurl:
                parts.append(aurl)
        if body:
            parts.append(body)

    elif ttype in ("q&a", "question", "qa"):
        q = topic.get("question", {}) or {}
        a = topic.get("answer", {}) or {}
        qtext = clean_text(q.get("text", ""))
        atext = clean_text(a.get("text", ""))
        if qtext:
            parts.append(f"【问】{qtext}")
            title = qtext
        if atext:
            parts.append(f"【答】{atext}")

    elif ttype == "article":
        art = topic.get("article", {}) or {}
        atitle = clean_text(art.get("title", ""))
        aurl = art.get("article_url") or art.get("url") or ""
        atext = clean_text(art.get("text", "") or art.get("inline_content", ""))
        if atitle:
            parts.append(f"[文章] {atitle}")
            title = atitle
        if aurl:
            parts.append(aurl)
        if atext:
            parts.append(atext)

    else:
        # 未知 type：尽力从常见字段兜底抽取正文
        for key in ("talk", "question", "answer", "article"):
            sub = topic.get(key)
            if isinstance(sub, dict) and sub.get("text"):
                parts.append(clean_text(sub.get("text", "")))

    body_text = "\n\n".join(p for p in parts if p).strip()
    if not title:
        # 无显式标题 → 取正文首行前 40 字作摘要标题
        first_line = body_text.splitlines()[0] if body_text else ""
        title = first_line[:40]
    return title, body_text


# ── zsxq API 封装 ─────────────────────────────────────────────────────────────
def list_groups(sess: requests.Session) -> list[dict]:
    """GET /v2/groups —— 我的已加入星球列表。"""
    data = api_get(sess, f"{API_BASE}/groups")
    groups = data.get("resp_data", {}).get("groups", [])
    return groups


def iter_topics(sess: requests.Session, group_id: str, limit: int | None = None):
    """翻页产出某星球的 topics。

    翻页：加 &end_time=<上一页最后一条 create_time>。
    去重：以 topic_id 去重；整页均为已见 topic_id 时停止（规避 off-by-one 重复/死循环）。
    """
    seen: set = set()
    end_time = None
    yielded = 0
    while True:
        params = {"scope": "all", "count": PAGE_COUNT}
        if end_time:
            params["end_time"] = end_time
        data = api_get(sess, f"{API_BASE}/groups/{group_id}/topics", params=params)
        topics = data.get("resp_data", {}).get("topics", [])
        if not topics:
            break

        page_all_seen = True
        last_ct = None
        for t in topics:
            tid = t.get("topic_id")
            last_ct = t.get("create_time", last_ct)
            if tid in seen:
                continue
            page_all_seen = False
            seen.add(tid)
            yield t
            yielded += 1
            if limit and yielded >= limit:
                return

        # 整页都是已见 → 说明 end_time off-by-one 只把最后一条又返回了，停止
        if page_all_seen:
            break
        if last_ct is None or last_ct == end_time:
            break
        end_time = last_ct
        time.sleep(1.0)  # 温柔翻页，降低限流概率


# ── 主流程 ────────────────────────────────────────────────────────────────────
def ingest_group(sess, group_id: str, group_name: str, done: set[str],
                 limit: int | None, dry_run: bool) -> dict:
    """拉取单个星球并入库，返回统计。"""
    stat = {"total": 0, "skipped_done": 0, "skipped_quality": 0, "ingested": 0, "empty": 0}
    for topic in iter_topics(sess, group_id, limit):
        stat["total"] += 1
        tid = topic.get("topic_id")
        source_id = f"zsxq:{group_id}:{tid}"
        if source_id in done:
            stat["skipped_done"] += 1
            continue

        title, body = extract_topic(topic)
        if not body:
            stat["empty"] += 1
            continue

        author = ""
        # 作者名位置随 type 变
        owner = (topic.get("talk", {}) or {}).get("owner") \
            or (topic.get("question", {}) or {}).get("owner") \
            or topic.get("owner", {})
        if isinstance(owner, dict):
            author = owner.get("name", "")

        ok, reason = text_quality(body)
        if not ok:
            stat["skipped_quality"] += 1
            print(f"[质检未过 {reason}] {source_id}: {title[:30]}", file=sys.stderr)
            continue

        meta = {
            "group_id": str(group_id),
            "group_name": group_name,
            "topic_id": tid,
            "create_time": topic.get("create_time", ""),
            "type": topic.get("type", ""),
        }

        if dry_run:
            print(f"\n──── [{group_name}] {source_id} ({topic.get('type')})")
            print(f"标题: {title}")
            print(f"作者: {author}")
            print(f"正文({len(body)}字): {body[:200]}{'…' if len(body) > 200 else ''}")
            stat["ingested"] += 1  # dry-run 下统计「本应入库」数
            continue

        n = ingest_text(
            text=body,
            source_id=source_id,
            source_type="zsxq",
            title=title,
            author=author,
            extra_meta=meta,
            skip_vectors=True,
        )
        if n >= 0:
            done.add(source_id)
            stat["ingested"] += 1
    return stat


def resolve_groups(sess, args) -> list[tuple[str, str]]:
    """解析要处理的星球 → [(group_id, group_name), ...]。"""
    if args.all:
        groups = list_groups(sess)
        return [(str(g.get("group_id")), g.get("name", "")) for g in groups]
    if args.group_id:
        ids = [x.strip() for x in args.group_id.split(",") if x.strip()]
        # 尝试取名字（失败不致命）
        name_map = {}
        try:
            for g in list_groups(sess):
                name_map[str(g.get("group_id"))] = g.get("name", "")
        except Exception:
            pass
        return [(gid, name_map.get(gid, "")) for gid in ids]
    return []


# ── 自测（离线，无需 token / 网络）────────────────────────────────────────────
SELFTEST_SAMPLE = {
    "succeeded": True,
    "code": 0,
    "resp_data": {
        "topics": [
            {
                "topic_id": 1001,
                "type": "talk",
                "create_time": "2024-01-01T10:00:00.000+0800",
                "talk": {
                    "owner": {"name": "张三"},
                    "text": (
                        "今天聊聊 "
                        '<e type="hashtag" hid="1" title="%23%E6%8A%95%E8%B5%84%23" /> '
                        "以及 "
                        '<e type="mention" uid="9" title="%40%E6%9D%8E%E5%9B%9B" /> '
                        "的看法。风险&amp;收益永远要平衡，仓位管理比择时更重要，"
                        "宁可错过也不要做错，保住本金是长期复利的前提。"
                        '<e type="text_bold" title="%E9%95%BF%E6%9C%9F%E6%8C%81%E6%9C%89" />'
                        "优质资产、忽略短期波动，才是普通人穿越牛熊的唯一王道。"
                    ),
                },
            },
            {
                "topic_id": 1002,
                "type": "q&a",
                "create_time": "2024-01-01T09:00:00.000+0800",
                "question": {
                    "owner": {"name": "王五"},
                    "text": '请问 <e type="hashtag" hid="2" title="%23%E4%BC%B0%E5%80%BC%23" /> 怎么算？',
                },
                "answer": {
                    "text": (
                        "核心是自由现金流折现，先估算未来若干年的现金流再按合理折现率贴现，"
                        "注意保留足够的 &lt;安全边际&gt; 以对冲判断失误，别用 <b>裸标签</b> 式的"
                        "精确假象迷惑自己；估值本质是模糊的正确，而非精确的错误。"
                    ),
                },
            },
        ]
    },
}


def run_selftest() -> int:
    print("=== 离线样例解析自测 ===")
    topics = SELFTEST_SAMPLE["resp_data"]["topics"]
    ok = True

    # 1) talk：应含话题/提及解码文本、正确解 & 实体、去掉 <e> 标签
    t1, b1 = extract_topic(topics[0])
    print(f"[talk] title={t1!r}\n       body={b1!r}")
    assert "<e" not in b1 and "/>" not in b1, "talk 正文残留 <e> 标签"
    assert "#投资#" in b1, "话题标签未解码为 #投资#"
    assert "@李四" in b1, "提及未解码为 @李四"
    assert "&amp;" not in b1 and "&" in b1, "HTML 实体未正确解码（风险&收益）"
    assert "长期持有" in b1, "text_bold 的 title 文本丢失"
    assert t1, "title 应非空（无标题时取正文首行摘要）"

    # 2) q&a：应有【问】【答】结构、解 &lt;&gt; 实体、清掉普通 <b> 标签
    t2, b2 = extract_topic(topics[1])
    print(f"[q&a]  title={t2!r}\n       body={b2!r}")
    assert "【问】" in b2 and "【答】" in b2, "问答结构缺失"
    assert "#估值#" in b2, "问句话题标签未解码"
    assert "<安全边际>" in b2, "&lt;&gt; 实体未解码"
    assert "<b>" not in b2 and "裸标签" in b2, "普通 HTML 标签未清除"

    # 3) author 抽取（contract 字段）：owner.name 位置随 type 变
    def _author(topic):
        owner = (topic.get("talk", {}) or {}).get("owner") \
            or (topic.get("question", {}) or {}).get("owner") \
            or topic.get("owner", {})
        return owner.get("name", "") if isinstance(owner, dict) else ""
    assert _author(topics[0]) == "张三", "talk 作者抽取错误"
    assert _author(topics[1]) == "王五", "q&a 作者抽取错误"

    # 4) text_quality 应放行这两条正文
    for b in (b1, b2):
        good, why = text_quality(b)
        assert good, f"正文被质检误杀: {why} -> {b!r}"

    print("\n全部断言通过 ✅（仅样例解析，未联网实测真实 zsxq API）")
    return 0 if ok else 1


# ── CLI ───────────────────────────────────────────────────────────────────────
def print_status() -> None:
    done = load_done()
    print(f"已入库 source_id 数：{len(done)}")
    # 按 group_id 分组统计
    per_group: dict[str, int] = {}
    for sid in done:
        parts = sid.split(":")
        if len(parts) >= 3:
            per_group[parts[1]] = per_group.get(parts[1], 0) + 1
    for gid, n in sorted(per_group.items()):
        print(f"  星球 {gid}: {n} 条")
    print(f"done 文件：{DONE_PATH}")


def main() -> int:
    ap = argparse.ArgumentParser(description="知识星球帖子入库 RAG")
    ap.add_argument("--group-id", help="星球 ID，可逗号分隔多个")
    ap.add_argument("--all", action="store_true", help="拉取全部已加入星球")
    ap.add_argument("--list-groups", action="store_true", help="只列出我的星球，不入库")
    ap.add_argument("--limit", type=int, default=None, help="每星球最多拉取 N 条")
    ap.add_argument("--status", action="store_true", help="查看已入库进度")
    ap.add_argument("--dry-run", action="store_true", help="只解析打印，不入库")
    ap.add_argument("--selftest", action="store_true", help="离线样例解析自测")
    ap.add_argument("--token", help="zsxq token（否则读 env ZSXQ_TOKEN）")
    ap.add_argument("--auth-mode", choices=["cookie", "header"],
                    default=os.environ.get("ZSXQ_AUTH_MODE", "cookie"),
                    help="鉴权形式：cookie(默认) 或 header；待真实响应确认")
    args = ap.parse_args()

    if args.selftest:
        return run_selftest()
    if args.status:
        print_status()
        return 0

    token = args.token or os.environ.get("ZSXQ_TOKEN", "")
    if not token:
        print("错误：需要 zsxq token。请 export ZSXQ_TOKEN=... 或 --token ...", file=sys.stderr)
        return 2

    sess = make_session(token, args.auth_mode)

    if args.list_groups:
        groups = list_groups(sess)
        print(f"共 {len(groups)} 个已加入星球：")
        for g in groups:
            print(f"  {g.get('group_id')}  {g.get('name', '')}")
        return 0

    targets = resolve_groups(sess, args)
    if not targets:
        print("未指定星球。请用 --group-id 或 --all（或先 --list-groups 查看）", file=sys.stderr)
        return 2

    done = load_done()
    grand = {"total": 0, "ingested": 0, "skipped_done": 0, "skipped_quality": 0, "empty": 0}
    try:
        for gid, gname in targets:
            print(f"\n===== 星球 {gid} {gname} =====")
            stat = ingest_group(sess, gid, gname, done, args.limit, args.dry_run)
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
