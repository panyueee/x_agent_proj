# -*- coding: utf-8 -*-
"""官方公告适配器 —— 财政/监管/贸易类政策事件（无结构化 akshare 源，走一手公告）。

写入 output/x_agent.db 的 policy_events 表（冻结契约见 x_agent/policy_events.py，
本文件只 import，绝不修改该模块或 storage.py）。

## 设计要点

1. **Source Card 纪律**：每条事件入库前必须过 `assert_source_card()`——
   缺 source_url、source_tier 不是 official_primary、或 URL 域名不在
   `OFFICIAL_DOMAIN_ALLOWLIST` 白名单内，一律拒绝入库（`SourceCardError`）。
   绝不用研报/媒体转述的 URL 冒充一手来源。

2. **骨架 `OfficialSource` + `fetch_announcement_list()`**：给定一个官方站点
   （名称/列表页 URL/域名前缀），尝试通用列表页解析（标题+日期+详情链接）。
   政府网站结构千差万别，这里只做“尽力而为”的通用兜底 parser——生产使用
   前几乎总需要按站点定制 XPath/CSS 选择器。抓不到就返回空列表，不抛异常
   中断整个流程（对应任务里“某官方站抓取受阻就换一条，别卡死”的要求）。

3. **样例事件 `SAMPLE_EVENTS`**：本次骨架验证阶段，公告解析器尚未逐站定制，
   所以 3-5 条样例事件是通过人工检索确认官方原文页面后手工构造的
   `PolicyEvent`（而不是跑通 `fetch_announcement_list` 自动抓到的）。
   每条都标注了检索来源方式，供后续复核。这符合任务里“低量验证管道可行，
   不追求覆盖面”的要求——下一步要做的是把 `fetch_announcement_list` 按站点
   补全 parser，让样例真正走自动抓取路径。

## 用法

    .venv/bin/python scripts/load_policy_announcements.py            # 灌样例
    .venv/bin/python scripts/load_policy_announcements.py --dry-run   # 只校验不写库
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from urllib.parse import urlparse

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from x_agent.policy_events import (  # noqa: E402  （冻结契约，只读不改）
    PolicyEvent,
    connect_write,
    ensure_schema,
    upsert_event,
)

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output", "x_agent.db"
)

# ── Source Card 校验 ─────────────────────────────────────────────────────

# 官方一手来源域名白名单（中国政府/部委 + 美方贸易/财政/管制相关站点）。
# 新增来源前先确认是官方主域，不是转载/媒体/第三方聚合站。
OFFICIAL_DOMAIN_ALLOWLIST = {
    # 中国
    "www.pbc.gov.cn", "pbc.gov.cn",                    # 中国人民银行
    "www.mof.gov.cn", "mof.gov.cn", "yss.mof.gov.cn", "m.mof.gov.cn", "qh.mof.gov.cn",
    "www.gov.cn", "gov.cn",                             # 中国政府网（国务院文件权威转发口）
    "www.csrc.gov.cn", "csrc.gov.cn",                   # 证监会
    "www.mofcom.gov.cn", "mofcom.gov.cn",
    "exportcontrol.mofcom.gov.cn", "aqygzj.mofcom.gov.cn", "cacs.mofcom.gov.cn",
    "www.npc.gov.cn", "npc.gov.cn",                     # 全国人大
    "www.ndrc.gov.cn", "ndrc.gov.cn",                   # 发改委
    "www.customs.gov.cn", "customs.gov.cn",             # 海关总署
    "www.mee.gov.cn", "mee.gov.cn",                     # 生态环境部（转发国务院文件常用）
    # 美国
    "ustr.gov", "www.ustr.gov",                          # 美国贸易代表办公室
    "home.treasury.gov",                                 # 美财政部
    "www.federalregister.gov", "federalregister.gov",    # 联邦公报（生效文本）
    "www.whitehouse.gov", "whitehouse.gov",
    "www.bis.doc.gov", "bis.doc.gov",                    # 商务部工业安全局（出口管制清单）
}


class SourceCardError(ValueError):
    """事件缺失合规的一手来源标注，拒绝入库。"""


def assert_source_card(event: PolicyEvent) -> None:
    """Source Card 校验：source_url 非空、域名在白名单、source_tier=official_primary。

    这是本脚本相对于契约 `PolicyEvent.validate()` 的**额外**约束——契约本身允许
    source_tier 为空或 media（比如给 akshare 结构化源留口子），但本适配器专门
    对付"没有结构化真值、必须靠一手公告"的场景，纪律要更严，拒绝媒体转述。
    """
    if not event.source_url:
        raise SourceCardError(f"{event.action}@{event.announce_date}: 缺 source_url")
    domain = urlparse(event.source_url).netloc
    if domain not in OFFICIAL_DOMAIN_ALLOWLIST:
        raise SourceCardError(
            f"{event.action}@{event.announce_date}: 域名 {domain!r} 不在官方白名单"
            f"（source_url={event.source_url}）"
        )
    if event.source_tier != "official_primary":
        raise SourceCardError(
            f"{event.action}@{event.announce_date}: source_tier={event.source_tier!r} "
            f"！= official_primary（本适配器只收一手官方原文，转述/媒体一律拒绝）"
        )


# ── 骨架：可复用的官方公告列表抓取结构 ────────────────────────────────────


@dataclass
class OfficialSource:
    """一个官方公告来源的站点配置。"""
    name: str            # 人读名称，如 "财政部-财政新闻"
    list_url: str        # 公告列表页 URL
    domain: str           # 期望的域名（用于二次校验，防止跳转到非官方页）
    # 通用兜底选择器；不同站点结构差异很大，多数情况需要子类/覆写此函数而非依赖通用规则
    link_selector: str = "a"


def fetch_announcement_list(source: OfficialSource, limit: int = 10,
                             timeout: int = 15) -> list[dict]:
    """尽力而为地抓取一个官方来源的公告列表（标题 + 详情链接）。

    这是通用兜底实现：政府网站列表页结构差异极大（有的用 <ul><li><a>，有的整页
    JS 渲染），此处只做最基础的 <a> 标签扫描 + 简单过滤，抓不到有效条目就返回
    空列表（不抛异常，让调用方换一条来源，对应"某官方站抓取受阻就换一条别卡死"
    的要求）。生产使用前应按站点在此函数旁新增专用 parser
    （如 `_parse_mof_list()` / `_parse_csrc_list()`），再按 source.name 分派。
    """
    try:
        resp = requests.get(
            source.list_url,
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (policy-announcement-adapter/0.1)"},
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[fetch_announcement_list] {source.name} 抓取失败，跳过: {e}")
        return []

    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
    except ImportError:
        print("[fetch_announcement_list] 缺 bs4，跳过解析")
        return []

    items: list[dict] = []
    for a in soup.select(source.link_selector):
        href = a.get("href", "").strip()
        title = a.get_text(strip=True)
        if not href or not title or len(title) < 6:
            continue
        # 相对链接补全域名
        if href.startswith("/"):
            href = f"https://{source.domain}{href}"
        if not href.startswith("http"):
            continue
        if urlparse(href).netloc not in OFFICIAL_DOMAIN_ALLOWLIST:
            continue  # 跳转到非官方域（常见于列表页混排友情链接/广告）
        items.append({"title": title, "url": href})
        if len(items) >= limit:
            break
    return items


def build_event_from_announcement(
    *, region: str, category: str, issuer: str, action: str, direction: str,
    announce_date: str, title: str, source_url: str,
    event_type: str = "discretionary", effective_date: str = "",
    params: dict | None = None,
) -> PolicyEvent:
    """把抓到/核实的公告字段组装成 PolicyEvent，并立刻过 Source Card 校验。

    抓取函数（`fetch_announcement_list` 或未来的站点专用 parser）拿到的原始
    条目只有 title/url，日期、issuer、action、direction、params 等结构化字段
    仍需要人工或 LLM 抽取环节补齐——这是"公告解析"与"结构化字段抽取"两个
    独立子问题，本骨架先把后者的落库接口和校验规则钉死。
    """
    ev = PolicyEvent(
        region=region, category=category, event_type=event_type,
        issuer=issuer, action=action, direction=direction,
        announce_date=announce_date, effective_date=effective_date,
        title=title, params=params or {},
        source_url=source_url, source_tier="official_primary",
        verification_status="verified",
    )
    assert_source_card(ev)
    return ev


# ── 样例事件（人工检索 + WebFetch 核验官方原文页面，非自动抓取）───────────
#
# 每条都在 WebSearch/WebFetch 中打开官方原文页确认过标题/日期/URL，
# 不是从研报或新闻转述摘抄。日期为公告/表决/印发日（announce_date），
# 不是媒体报道日。

def sample_events() -> list[PolicyEvent]:
    events = []

    # 1. CN/财政/DEFICIT_RATIO —— 2025年全国预算报告，赤字率从 2024 年 3% 提高到
    #    2025 年 4%（政府工作报告口径公开数字），赤字规模 5.66 万亿元。
    #    十四届全国人大三次会议 2025-03-05 开幕，预算报告随会提请审查；
    #    财政部官网发布报告全文页面时间戳为 2025-03-06。
    events.append(build_event_from_announcement(
        region="CN", category="财政", issuer="财政部",
        action="DEFICIT_RATIO", direction="expand",
        announce_date="2025-03-05",
        title="关于2025年中央和地方预算草案的报告（赤字率按4%安排，较2024年提高1个百分点）",
        source_url="https://www.mof.gov.cn/zhengwuxinxi/caizhengxinwen/202503/t20250306_3959380.htm",
        params={"delta_pp": 1.0, "deficit_ratio_pct": 4.0, "deficit_scale_yi_cny": 56600},
    ))

    # 2. CN/财政/SPECIAL_BOND —— 十四届全国人大常委会第十二次会议 2024-11-08
    #    表决通过，一次性增加 6 万亿元地方政府专项债务限额置换存量隐性债务，
    #    分三年实施。中国政府网权威转发。
    events.append(build_event_from_announcement(
        region="CN", category="财政", issuer="全国人大常委会",
        action="SPECIAL_BOND", direction="expand",
        announce_date="2024-11-08",
        title="全国人大常委会批准增加6万亿元地方政府债务限额置换存量隐性债务",
        source_url="https://www.gov.cn/yaowen/liebiao/202411/content_6985595.htm",
        params={"amount_yi_cny": 60000, "note": "一次报批分三年实施，非单一百分点变动，无 delta_pp"},
    ))

    # 3. CN/监管/CAPITAL_MARKET —— 国务院 2024-04-12 印发《关于加强监管防范
    #    风险推动资本市场高质量发展的若干意见》（新"国九条"），证监会官网转发。
    events.append(build_event_from_announcement(
        region="CN", category="监管", issuer="国务院",
        action="CAPITAL_MARKET_REFORM", direction="na",
        announce_date="2024-04-12",
        title="国务院关于加强监管防范风险推动资本市场高质量发展的若干意见（新“国九条”）",
        source_url="https://www.csrc.gov.cn/csrc/c100028/c7473564/content.shtml",
        params={},  # 定性监管文件，无量化 delta_pp
    ))

    # 4. CN/监管/EXPORT_CONTROL —— 商务部、海关总署 2023-07-03 发布 2023 年
    #    第 23 号公告，对镓、锗相关物项实施出口管制，2023-08-01 起实施。
    events.append(build_event_from_announcement(
        region="CN", category="监管", issuer="商务部/海关总署",
        action="EXPORT_CONTROL", direction="na",
        announce_date="2023-07-03", effective_date="2023-08-01",
        title="商务部 海关总署公告2023年第23号 关于对镓、锗相关物项实施出口管制的公告",
        source_url="http://exportcontrol.mofcom.gov.cn/article/zcfg/gnzcfg/zcfggzqd/202307/847.html",
        params={},
    ))

    # 5. US/贸易/TARIFF —— USTR 2024-05-14 宣布 Section 301 对华关税四年期
    #    复审后的调整方案（电动车 25%→100%、半导体 25%→50%、电池 25% 等，
    #    分品类分年生效）。USTR 官网新闻稿。
    events.append(build_event_from_announcement(
        region="US", category="贸易", issuer="USTR",
        action="TARIFF", direction="hike",
        announce_date="2024-05-14",
        title="USTR Announces Section 301 Tariff Increases on China "
              "(EVs 25%→100%, semiconductors 25%→50%, batteries, etc.)",
        source_url="https://ustr.gov/about-us/policy-offices/press-office/press-releases/"
                    "2024/may/us-trade-representative-katherine-tai-take-further-action-"
                    "china-tariffs-after-releasing-statutory",
        params={"note": "多品类关税上调，税率因商品而异，无单一 delta_pp（示例：电动车 delta_pp=75.0）"},
    ))

    return events


# ── 主流程 ─────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DB_PATH, help="目标 SQLite 路径（默认 output/x_agent.db）")
    parser.add_argument("--dry-run", action="store_true", help="只跑 Source Card 校验，不写库")
    args = parser.parse_args()

    events = sample_events()  # 构造期已过 assert_source_card，这里能拿到的都是合规事件
    print(f"[load_policy_announcements] 样例事件 {len(events)} 条，全部通过 Source Card 校验")

    if args.dry_run:
        for ev in events:
            print(f"  DRY-RUN {ev.region}/{ev.category}/{ev.action}@{ev.announce_date} "
                  f"issuer={ev.issuer} url={ev.source_url}")
        return 0

    conn = connect_write(args.db)
    ensure_schema(conn)  # 幂等；表已存在则跳过
    inserted, updated = 0, 0
    for ev in events:
        problems = ev.validate()
        if problems:
            print(f"  [跳过] 契约校验失败 {ev.action}@{ev.announce_date}: {problems}")
            continue
        is_new = upsert_event(conn, ev, strict=True)
        inserted += is_new
        updated += (not is_new)
        tag = "新增" if is_new else "覆盖(幂等)"
        print(f"  [{tag}] {ev.region}/{ev.category}/{ev.action}@{ev.announce_date} "
              f"issuer={ev.issuer}")
    conn.close()
    print(f"[load_policy_announcements] 完成：新增 {inserted} 条，覆盖 {updated} 条 -> {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
