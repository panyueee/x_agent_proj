"""
直接分类剩余 140 个 Others 概念，无需 LLM API。
运行：python scripts/classify_remaining_concepts.py [--db output/x_agent.db]
"""
import argparse
import sqlite3
import sys
from pathlib import Path

# 140 个概念的逐一分类
# source = 'manual'（人工知识分类，无需 API）
MANUAL_CLASSIFICATIONS = {
    # ── 财报业绩类 → Others（非行业属性，是财务状态筛选标签）──
    "2025三季报扭亏":   "Others",
    "2025三季报预减":   "Others",
    "2025三季报预增":   "Others",
    "2025年报扭亏":     "Others",
    "2025年报预减":     "Others",
    "2025年报预增":     "Others",
    "2026一季报扭亏":   "Others",
    "2026一季报预减":   "Others",
    "2026一季报预增":   "Others",

    # ── 股票筛选标签 → Others ──
    "AB股":             "Others",
    "AH股":             "Others",
    "B股":              "Others",
    "GDR":              "Others",
    "ST股":             "Others",
    "大盘价值":         "Others",
    "大盘成长":         "Others",
    "大盘股":           "Others",
    "中盘价值":         "Others",
    "中盘成长":         "Others",
    "中盘股":           "Others",
    "小盘价值":         "Others",
    "小盘成长":         "Others",
    "小盘股":           "Others",
    "微盘股":           "Others",
    "微盘精选":         "Others",
    "百元股":           "Others",
    "低价股":           "Others",
    "权重股":           "Others",
    "行业龙头":         "Others",
    "价值股":           "Others",
    "周期股":           "Others",
    "微利股":           "Others",
    "超级品牌":         "Others",
    "独角兽":           "Others",
    "次新股":           "Others",
    "红利股":           "Others",
    "红利破净股":       "Others",
    "破净股":           "Others",
    "破发股":           "Others",
    "破增发价股":       "Others",
    "长期破净":         "Others",
    "超跌股":           "Others",
    "反转股":           "Others",
    "趋势股":           "Others",
    "题材股":           "Others",

    # ── 历史价格技术标签 → Others ──
    "历史新高":         "Others",
    "近期新高":         "Others",
    "百日新高":         "Others",
    "昨日打二板以上表现": "Others",
    "昨日涨停":         "Others",
    "昨日涨停_含一字":  "Others",
    "昨日炸板":         "Others",
    "昨日触板":         "Others",
    "昨日连板":         "Others",
    "昨日连板_含一字":  "Others",
    "昨日首板":         "Others",
    "昨日高振幅":       "Others",
    "昨日高换手":       "Others",
    "最近多板":         "Others",

    # ── 宽基指数成分 → Others ──
    "HS300_":           "Others",
    "上证180_":         "Others",
    "上证380":          "Others",
    "上证50_":          "Others",
    "央视50_":          "Others",
    "宁组合":           "Others",
    "茅指数":           "Others",
    "创业板综":         "Others",
    "创业成份":         "Others",
    "深成500":          "Others",
    "深证100R":         "Others",
    "中证500":          "Others",
    "东方财富热股":     "Others",

    # ── 外资/机构持仓标签 → Others ──
    "MSCI中国":         "Others",
    "富时罗素":         "Others",
    "标准普尔":         "Others",
    "QFII重仓":         "Others",
    "机构重仓":         "Others",
    "社保重仓":         "Others",
    "证金持股":         "Others",
    "沪股通":           "Others",
    "深股通":           "Others",
    "港股通":           "Others",
    "北交所概念":       "Others",

    # ── 公司事件类 → Financials（与资本运作相关）──
    "举牌":             "Financials",
    "股权激励":         "Financials",
    "股权转让":         "Financials",
    "IPO受益":          "Financials",
    "并购重组":         "Financials",
    "转债标的":         "Financials",
    "参股新三板":       "Financials",
    "创投":             "Financials",
    "中特估":           "Financials",   # 央企估值重估，金融/价值重估驱动
    "养老金":           "Financials",   # 养老金入市、险资，金融属性

    # ── 政策/区域经济 → Industrials ──
    "中俄贸易概念":     "Industrials",
    "内贸流通":         "Industrials",
    "统一大市场":       "Industrials",
    "专精特新":         "Industrials",  # 工业/制造升级
    "空间站概念":       "Industrials",  # 航天工程/国防
    "长江三角":         "Industrials",  # 区域经济一体化，以工业/制造为主

    # ── 科技 → Technology ──
    "光通信模块":       "Technology",   # 光芯片/通信硬件
    "电子身份证":       "Technology",
    "电子车牌":         "Technology",
    "SPD概念":          "Technology",   # 浪涌保护/电子器件
    "数据要素":         "Technology",   # 数据要素市场、数字化
    "EDR概念":          "Technology",   # 端点检测与响应，网络安全
    "智慧政务":         "Technology",   # 政务数字化/云计算
    "PLC概念":          "Technology",   # 可编程逻辑控制器，工控/智能制造
    "数据确权":         "Technology",   # 数据治理/区块链
    "电子后视镜":       "Technology",   # 车载电子/ADAS
    "超清视频":         "Technology",   # 4K/8K视频技术
    "增强现实":         "Technology",   # AR
    "虚拟现实":         "Technology",   # VR
    "中芯概念":         "Technology",   # 中芯国际产业链，半导体制造
    "同步磁阻电机":     "Industrials",  # 工业电机/节能设备
    "胎压监测":         "Consumer Discretionary",  # 汽车零部件，消费电子

    # ── 通信 → Communication Services ──
    "卫星互联网":       "Communication Services",
    "小红书概念":       "Communication Services",  # 社交媒体平台
    "共享经济":         "Communication Services",  # 平台经济/互联网
    "通信技术":         "Communication Services",

    # ── 消费 → Consumer Discretionary ──
    "彩票概念":         "Consumer Discretionary",
    "培育钻石":         "Consumer Discretionary",  # 珠宝消费
    "托育服务":         "Consumer Discretionary",  # 婴幼儿托管，教育/消费服务
    "地摊经济":         "Consumer Discretionary",
    "C2M概念":          "Consumer Discretionary",  # 消费者直连工厂
    "新消费":           "Consumer Discretionary",
    "首发经济":         "Consumer Discretionary",  # 首店首发，消费新业态
    "冰雪经济":         "Consumer Discretionary",  # 冰雪运动/旅游消费

    # ── 消费必选 → Consumer Staples ──
    "养老概念":         "Consumer Staples",   # 养老服务/银发消费
    "供销社概念":       "Consumer Staples",   # 农村流通/民生必选

    # ── 材料 → Materials ──
    "锂矿概念":         "Materials",
    "资源开采概念":     "Materials",

    # ── 公用事业 → Utilities ──
    "发电机概念":       "Utilities",
    "雅下水电概念":     "Utilities",   # 水电能源

    # ── 无行业属性/风格标签 → Others ──
    "科技风格":         "Others",
    "消费风格":         "Others",
    "先进制造风格":     "Others",
    "知识产权":         "Others",      # 法律制度概念，无明确行业
    "贬值受益":         "Others",      # 汇率宏观，跨行业
    "反内卷概念":       "Others",      # 政策表态，跨行业
    "科创板做市商":     "Others",      # 市场结构
    "科创板做市股":     "Others",
}


def run(db_path: str) -> None:
    conn = sqlite3.connect(db_path)

    # 确保表存在
    conn.execute("""
        CREATE TABLE IF NOT EXISTS concept_mappings (
            concept     TEXT PRIMARY KEY,
            gics        TEXT NOT NULL,
            source      TEXT DEFAULT 'seed',
            confirmed   INTEGER DEFAULT 0,
            updated_at  TEXT DEFAULT (datetime('now'))
        )
    """)

    # 只更新仍在 Others 的（避免覆盖已手动确认的）
    current_others = {
        r[0] for r in conn.execute(
            "SELECT concept FROM concept_mappings WHERE gics='Others'"
        ).fetchall()
    }

    updated = 0
    for concept, gics in MANUAL_CLASSIFICATIONS.items():
        if concept not in current_others:
            continue  # 已被其他方式分类，跳过
        conn.execute(
            """INSERT INTO concept_mappings (concept, gics, source, confirmed, updated_at)
               VALUES (?, ?, 'manual', 1, datetime('now'))
               ON CONFLICT(concept) DO UPDATE SET
                   gics=excluded.gics,
                   source='manual',
                   confirmed=1,
                   updated_at=datetime('now')
            """,
            (concept, gics),
        )
        updated += 1

    conn.commit()

    # 统计
    rows = conn.execute(
        "SELECT gics, COUNT(*) FROM concept_mappings GROUP BY gics ORDER BY COUNT(*) DESC"
    ).fetchall()
    remaining_others = conn.execute(
        "SELECT concept FROM concept_mappings WHERE gics='Others' ORDER BY concept"
    ).fetchall()

    print(f"\n✅ 已更新 {updated} 条分类（source=manual, confirmed=1）\n")
    print("=== 当前分类分布 ===")
    for gics, cnt in rows:
        print(f"  {gics:<30} {cnt:>4} 个")

    if remaining_others:
        print(f"\n⚠️  仍有 {len(remaining_others)} 个 Others（未在分类表中）：")
        for (c,) in remaining_others:
            print(f"  {c}")
    else:
        print("\n🎉 所有概念已完成分类，Others 仅保留无行业属性标签！")

    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="output/x_agent.db")
    args = parser.parse_args()
    run(args.db)
