# -*- coding: utf-8 -*-
"""
报告输出 QA 门禁 + 溯源/事实-推断分离纪律。

借鉴 quantskills 三 skill 的 validate_report 思路（正则清单式校验），但泛化到本项目
多种报告类型（digest / dossier / risk / persona / scenario），并加入本项目更看重的
**溯源纪律**：数字断言要能追到来源、事实与推断要分开标。

用法：
    from x_agent.report_qa import validate_report, PROV_FACT, PROV_INFER
    issues = validate_report(text, kind="digest")      # 返回问题列表，空=通过
    # 生成报告时给断言打标：f"{PROV_FACT} 5月PPI同比+3.9%（来源：akshare macro_china_ppi）"
    #                       f"{PROV_INFER} 通胀或已见顶"

设计为"警告而非硬拦"：默认返回 issues 让调用方决定；CLI 可 --strict 时非零退出。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ── 溯源标记（事实/推断分离约定）──────────────────────────────────────────────
# 生成报告的模块统一用这套前缀，QA 才能机械校验。
PROV_FACT = "〔事实〕"    # 有来源、可核验的客观数据/事件
PROV_INFER = "〔推断〕"   # 分析师/模型的判断、预测、解读
PROV_TAGS = (PROV_FACT, PROV_INFER)

# 数字断言的粗匹配：百分比 / 金额（元/亿/万亿）/ 点位 / 倍数
_NUM_CLAIM = re.compile(r"[-+]?\d[\d,\.]*\s*(%|个百分点|pp|元|亿|万亿|万|倍|点|bp)")
# 来源线索：报告里出现这些视为"有标注来源"
_SOURCE_HINT = re.compile(r"(来源|数据来源|来源接口|使用接口|akshare|东财|Wind|Pandadata|"
                          r"source_id|source_url|source_tier|SC-\d|row_id|截图|公告|统计局|央行)")


@dataclass(frozen=True)
class ReportProfile:
    """一种报告类型的 QA 画像。"""
    kind: str
    min_chars: int
    required: tuple[tuple[str, str], ...]   # (正则, 缺失时的提示)
    need_disclaimer: bool = True
    need_freshness: bool = True             # 需数据截止/生成时间戳
    need_provenance: bool = True            # 需事实/推断分离 + 数字有来源


# 各报告类型的必备结构（正则按小节标题匹配；宽松，允许不同写法）
PROFILES: dict[str, ReportProfile] = {
    "digest": ReportProfile(
        "digest", 300,
        (("(##|###)\\s*", "缺少任何分节标题"),),
    ),
    "dossier": ReportProfile(
        "dossier", 400,
        (("(Current Model|当前模型|# )", "缺少 Current Model / 主标题"),
         ("(开放问题|open.?question|待验证|未知)", "缺少开放问题/未知项（研究不该只有结论）"),),
    ),
    "risk": ReportProfile(
        "risk", 300,
        (("(风险|回撤|波动|VaR|暴露)", "缺少风险指标小节"),),
        need_disclaimer=False,
    ),
    "persona": ReportProfile(
        "persona", 300,
        (("(画像|框架|世界观|预测|命中)", "缺少画像/框架/预测小节"),),
        need_disclaimer=False,
    ),
    "scenario": ReportProfile(
        "scenario", 200,
        (("(情景|情境|scenario|损益|回撤)", "缺少情景/损益小节"),),
        need_disclaimer=False,
    ),
    "generic": ReportProfile("generic", 200, (), need_disclaimer=False,
                             need_freshness=False, need_provenance=False),
}


def validate_report(text: str, kind: str = "generic") -> list[str]:
    """校验一份报告文本，返回问题列表（空列表=通过）。"""
    p = PROFILES.get(kind, PROFILES["generic"])
    issues: list[str] = []
    body = text.strip()

    if len(body) < p.min_chars:
        issues.append(f"内容过短（{len(body)}<{p.min_chars} 字），可能不完整")

    for pattern, msg in p.required:
        if not re.search(pattern, text, flags=re.MULTILINE | re.IGNORECASE):
            issues.append(msg)

    if p.need_freshness and not re.search(
            r"(数据日|数据截止|生成时间|截止时间|快照|as.?of|\d{4}-\d{2}-\d{2})", text):
        issues.append("缺少数据日/生成时间/快照日期")

    if p.need_disclaimer and not re.search(
            r"(不构成投资建议|不提供操作建议|仅作.{0,4}事实|风险自担)", text):
        issues.append("缺少非投资建议/免责声明")

    # T+1 类数据的时点纪律
    if re.search(r"(两融|融资融券|北向|陆股通)", text) and not re.search(r"(T\+1|数据日|截止)", text):
        issues.append("两融/北向数据需标注 T+1 或实际数据日")

    if p.need_provenance:
        issues += _check_provenance(text)

    return issues


def _check_provenance(text: str) -> list[str]:
    """溯源纪律：事实/推断是否分标 + 数字断言是否有来源线索。"""
    issues: list[str] = []
    has_fact = PROV_FACT in text
    has_infer = PROV_INFER in text

    # 有数字断言却整篇没有来源线索 → 高风险
    num_hits = _NUM_CLAIM.findall(text)
    if num_hits and not _SOURCE_HINT.search(text):
        issues.append(f"出现 {len(num_hits)} 处数字断言但全文无来源线索（溯源缺失）")

    # 既有事实又有推断内容，却完全没用分离标记 → 提示（不硬拦）
    looks_analytical = re.search(r"(预测|判断|或将|可能|预计|看多|看空|建议)", text)
    if looks_analytical and not (has_fact or has_infer):
        issues.append(f"含分析性判断但未用 {PROV_FACT}/{PROV_INFER} 区分事实与推断")

    return issues


def is_clean(text: str, kind: str = "generic") -> bool:
    return not validate_report(text, kind)


# ── 生成流程接入辅助 ──────────────────────────────────────────────────────────

def provenance_footer(sources: str, disclaimer: bool = True) -> str:
    """标准溯源页脚：生成时间 + 数据来源 + 可选免责。生成报告统一 append 它，
    既满足 QA 的 freshness/来源/免责检查，也让人看清口径。"""
    import datetime as _dt
    ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    foot = f"\n\n---\n_生成时间：{ts}｜数据来源：{sources}_"
    if disclaimer:
        foot += "\n_本报告仅作事实归纳，不构成投资建议，风险自担。_"
    return foot


def qa_and_warn(text: str, kind: str, label: str = "") -> list[str]:
    """校验并把问题打到 stdout（非硬拦），返回问题列表。供生成器收尾调用。"""
    issues = validate_report(text, kind)
    if issues:
        tag = f"[QA:{kind}{' '+label if label else ''}]"
        print(f"{tag} {len(issues)} 个输出规范问题：")
        for i in issues:
            print(f"  - {i}")
    return issues
