# -*- coding: utf-8 -*-
"""report_qa 报告 QA 门禁测试。"""
from x_agent.report_qa import (
    validate_report, is_clean, PROV_FACT, PROV_INFER,
)


def test_generic_too_short():
    issues = validate_report("短", kind="generic")
    assert any("过短" in i for i in issues)


def test_digest_missing_sections_and_freshness():
    txt = "这是一份没有分节、没有日期、没有免责的正文。" * 20
    issues = validate_report(txt, kind="digest")
    assert any("分节" in i for i in issues)
    assert any("时间" in i or "数据日" in i for i in issues)
    assert any("免责" in i or "投资建议" in i for i in issues)


def test_digest_clean_passes():
    txt = (
        "## 市场概览\n生成时间：2026-07-02\n数据来源：akshare。\n"
        f"{PROV_FACT} 上证收于 3200 点（来源：东财）。\n"
        f"{PROV_INFER} 情绪或偏谨慎。\n"
        "本报告仅作事实归纳，不构成投资建议。\n" + "补充说明若干。" * 60
    )
    assert is_clean(txt, kind="digest"), validate_report(txt, "digest")


def test_provenance_numbers_without_source():
    # 有数字断言但无任何来源线索 → 应报溯源缺失
    txt = "## 标题\n生成时间 2026-07-02\n涨幅达 12.5% 且净流入 8亿。\n不构成投资建议。" + "尾" * 300
    issues = validate_report(txt, kind="digest")
    assert any("溯源" in i or "来源" in i for i in issues)


def test_provenance_analytical_without_tags():
    txt = ("## 标题\n生成时间 2026-07-02\n来源：akshare。\n"
           "预计后市看多，可能突破。\n不构成投资建议。" + "尾" * 300)
    issues = validate_report(txt, kind="digest")
    assert any("事实" in i and "推断" in i for i in issues)


def test_north_flow_needs_t1():
    txt = ("## 标题\n生成时间 2026-07-02\n来源：东财。\n"
           f"{PROV_FACT} 北向资金净流入 5亿。\n不构成投资建议。" + "尾" * 300)
    issues = validate_report(txt, kind="digest")
    assert any("T+1" in i for i in issues)


def test_risk_no_disclaimer_needed():
    txt = ("## 组合风险\n2026-06-30 快照\n来源：本项目风险引擎（x_agent.risk）。\n"
           "年化波动 15.97%，最大回撤 -46%，因子暴露 size -0.68。" + "详细归因说明。" * 40)
    # risk 类不要求免责，但要求风险小节 + 日期
    assert is_clean(txt, kind="risk"), validate_report(txt, "risk")


def test_dossier_requires_open_questions():
    txt = "# Current Model\n2026-07-02\n当前判断是 X。\n来源：research。" + "详" * 200
    issues = validate_report(txt, kind="dossier")
    assert any("开放问题" in i or "未知" in i for i in issues)
