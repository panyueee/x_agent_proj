"""概念板块映射每周更新脚本。

功能：
  1. 从 AKShare 拉取最新东方财富概念板块列表
  2. 与 DB 已有映射对比，找出新增概念
  3. 新概念先用关键词规则自动分类（覆盖约 80%）
  4. 无法自动分类的标为 Others + 打印待确认清单
  5. 可选 --llm 模式：把未知概念批量发给 Claude 自动分类

用法：
    python scripts/update_concept_sectors.py              # 自动分类
    python scripts/update_concept_sectors.py --llm        # LLM 辅助分类未知概念
    python scripts/update_concept_sectors.py --confirm    # 打印待确认清单
    python scripts/update_concept_sectors.py --set "概念名称=Technology"  # 人工确认

建议加入 crontab 每周一 9:00 运行：
    0 9 * * 1 cd /Users/pany19/Documents/x_agent_proj && \
              source .venv/bin/activate && python scripts/update_concept_sectors.py
"""
from __future__ import annotations

import argparse
import os
import sys
import re
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from x_agent.storage import Store
from x_agent.factor_model import CONCEPT_TO_GICS

# ── 关键词自动分类规则（按顺序匹配，先匹配先赢）──
_KEYWORD_RULES: list[tuple[list[str], str]] = [
    # ── Technology ──
    (["AI", "人工智能", "大模型", "算力", "ChatGPT", "AIGC", "GPT",
      "芯片", "半导体", "晶圆", "存储", "IGBT", "碳化硅", "第三代半导体",
      "氮化镓", "蓝宝石", "OLED", "MiniLED", "MicroLED", "LED", "显示",
      "柔性屏", "折叠屏", "屏下", "电子纸", "裸眼3D", "全息",
      "云计算", "大数据", "数字", "信创", "操作系统", "数据库", "软件",
      "ERP", "网络安全", "数据安全", "边缘计算", "工业互联", "物联网",
      "区块链", "元宇宙", "VR", "AR", "混合现实", "空间计算",
      "国产替代", "EDA", "光刻", "封装", "测试", "PCB", "射频", "MLCC",
      "被动元件", "传感器", "机器视觉", "激光雷达", "毫米波",
      "UWB", "WiFi", "无线充电", "无线耳机", "智能穿戴", "消费电子",
      "3D打印", "超导", "量子", "超级电容", "植物照明",
      "Manus", "DeepSeek", "Sora", "Kimi", "MLOps",
      "华为概念", "华为昇腾", "华为海思", "华为欧拉", "鸿蒙",
      "英伟达", "苹果概念", "CPO", "液冷", "高带宽内存",
      "数据中心", "东数西算", "IDC", "光纤", "光模块",
      "安防", "人脸识别", "智能摄像", "3D摄像头",
      "华为汽车", "小米概念", "百度概念", "阿里概念", "腾讯云",
      "抖音", "快手", "拼多多", "蚂蚁", "荣耀"], "Technology"),

    # ── Communication Services ──
    (["5G", "6G", "卫星通信", "北斗", "导航", "广播",
      "游戏", "影视", "短视频", "直播", "流媒体", "电竞", "电子竞技",
      "网红", "互联网服务", "互联网金融", "IPv6", "Web3",
      "VPN", "光纤", "宽带"], "Communication Services"),

    # ── Health Care ──
    (["医药", "医疗", "创新药", "CXO", "CDMO", "CRO", "器械",
      "基因", "测序", "医美", "减肥", "GLP-1", "肿瘤", "免疫",
      "细胞", "抗体", "疫苗", "中药", "生物", "健康",
      "体外诊断", "精准诊断", "DRG", "单抗", "重组蛋白",
      "肝素", "阿兹海默", "青蒿素", "维生素", "特色药",
      "独家药品", "辅助生殖", "病毒防治", "病原体", "幽门螺杆菌",
      "肝炎", "流感", "长寿药", "人脑工程", "核污染防治",
      "医废处理", "噪声防治", "抗菌"], "Health Care"),

    # ── Consumer Discretionary ──
    (["新能源汽车", "智能汽车", "自动驾驶", "无人驾驶", "车联网",
      "汽车整车", "汽车热管理", "汽车一体化压铸", "汽车拆解",
      "换电", "轮毂电机", "减速器", "小米汽车", "特斯拉",
      "飞行汽车", "eVTOL", "磁悬浮",
      "免税", "跨境", "电商", "新零售", "零售", "社区团购",
      "宠物", "潮玩", "户外", "运动", "家居", "装饰",
      "酒店", "旅游", "航空", "教育", "培训", "奢侈", "化妆",
      "盲盒", "谷子经济", "文娱", "体育", "婴童",
      "智能电视", "电子烟", "退税"],
     "Consumer Discretionary"),

    # ── Consumer Staples ──
    (["白酒", "啤酒", "酿酒", "饮料", "食品", "农业", "养殖",
      "渔业", "水产", "鸡肉", "猪肉", "乳业", "调味品",
      "预制菜", "人造肉", "代糖", "农药", "兽药", "化肥",
      "种子", "消费复苏", "乡村振兴", "粮食", "草甘膦",
      "味蕾", "土地流转"], "Consumer Staples"),

    # ── Industrials ──
    (["机器人", "人形", "无人机", "低空", "航空发动机", "航天",
      "军工", "国防", "民爆", "船舶", "海工", "海洋",
      "央企", "国企", "国资", "一带一路", "中字头",
      "工程机械", "工业母机", "轨道交通", "铁路", "港口",
      "物流", "快递", "交运", "大飞机", "航母",
      "工程建设", "建筑节能", "装配建筑", "PPP",
      "3D打印", "新型工业化", "东北振兴", "西部大开发",
      "雄安新区", "京津冀", "长三角", "成渝", "滨海新区",
      "深圳特区", "新型城镇化", "海绵城市", "地下管网",
      "智慧城市", "智慧灯杆", "水利建设", "地热能",
      "海南自贸", "上海自贸", "粤港自贸", "湖北自贸",
      "沪企改革", "并购重组", "ETC", "磁悬浮",
      "工业大麻"], "Industrials"),

    # ── Materials ──
    (["锂电", "固态电池", "碳酸锂", "磷酸铁锂", "钠离子",
      "刀片电池", "麒麟电池", "电池技术", "电池回收",
      "复合集流体", "PVDF", "钒电池",
      "稀土", "黄金", "铜", "铝", "锌", "钨", "钼",
      "锰", "钴", "镍", "铂", "钯", "氦气", "稀缺资源",
      "贵金属", "小金属", "有色", "钛白粉",
      "化工", "有机硅", "环氧丙烷", "降解塑料", "碳纤维",
      "石墨烯", "碳基材料", "新材料", "纳米银", "PEEK",
      "钢铁", "建材", "玻璃", "水泥", "造纸", "包装材料",
      "工业气体", "超超临界"], "Materials"),

    # ── Energy ──
    (["光伏", "风能", "风电", "储能", "氢能", "核电",
      "可控核聚变", "抽水蓄能", "分布式",
      "HJT电池", "TOPCon电池", "BC电池", "钙钛矿电池",
      "新能源", "煤炭", "石油石化", "天然气", "页岩",
      "LNG", "油气资源", "油气设服", "可燃冰",
      "燃料电池", "充电桩", "高压快充", "换电"], "Utilities"),

    # ── Utilities ──
    (["公用事业", "电力", "电网", "特高压", "智能电网",
      "虚拟电厂", "空气能热泵", "净水", "水务",
      "垃圾", "环保", "土壤修复", "建筑节能",
      "尾气治理", "医废", "碳中和", "碳达峰", "碳交易",
      "绿电", "绿氢", "CCUS", "低碳冶金",
      "地热能", "超导"], "Utilities"),

    # ── Financials ──
    (["券商", "保险", "银行", "信托", "期货", "基金",
      "金融科技", "支付", "消费金融", "融资", "创投",
      "化债", "AMC", "互联网金融"], "Financials"),

    # ── Real Estate ──
    (["地产", "房地产", "物业", "REITs", "商业地产",
      "城投", "租售同权", "房屋检测", "新型城镇化"], "Real Estate"),

    # ── Others（明确是指数/策略标签，不是产业）──
    (["昨日", "近期", "历史新高", "百日新高", "低价股", "破净",
      "红利", "权重股", "大盘股", "中盘股", "小盘股", "微盘",
      "ST股", "B股", "AB股", "AH股", "GDR", "次新股", "破发",
      "超跌", "反转", "趋势股", "题材股", "周期股",
      "HS300", "上证", "深成", "创业板综", "创业成份",
      "MSCI", "富时", "标准普尔", "QFII", "社保重仓", "机构重仓",
      "证金持股", "沪股通", "深股通", "北交所概念",
      "IPO", "举牌", "股权", "转债", "参股新三板",
      "价值股", "成长", "风格", "指数"], "Others"),
]

GICS_OPTIONS = [
    "Technology", "Communication Services", "Health Care",
    "Consumer Discretionary", "Consumer Staples", "Industrials",
    "Materials", "Utilities", "Financials", "Real Estate", "Others",
]


def _auto_classify(name: str) -> tuple[str, str]:
    """
    用关键词规则自动分类概念名称。
    返回 (gics, source)，source 为 'seed'/'auto'/'others'。
    """
    # 优先查硬编码种子表
    if name in CONCEPT_TO_GICS:
        return CONCEPT_TO_GICS[name], "seed"
    # 关键词规则
    for keywords, gics in _KEYWORD_RULES:
        if any(kw in name for kw in keywords):
            return gics, "auto"
    return "Others", "auto"


def _llm_classify(concepts: list[str], model: str = "claude-sonnet-4-6") -> dict[str, str]:
    """用 Claude 批量分类无法自动识别的概念板块。"""
    try:
        import anthropic
    except ImportError:
        print("[update] anthropic 未安装，跳过 LLM 分类")
        return {}

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("[update] ANTHROPIC_API_KEY 未设置，跳过 LLM 分类")
        return {}

    client = anthropic.Anthropic(api_key=api_key)
    chunk_size = 30  # 每次最多 30 个，避免超长 prompt
    result = {}

    for i in range(0, len(concepts), chunk_size):
        batch = concepts[i:i + chunk_size]
        prompt = f"""以下是A股东方财富概念板块名称列表，请将每个概念归类到最合适的GICS大类。

可选GICS大类：
{chr(10).join(f'- {g}' for g in GICS_OPTIONS)}

概念列表：
{chr(10).join(f'{j+1}. {c}' for j, c in enumerate(batch))}

请严格按以下JSON格式输出，不要输出其他内容：
{{"results": [{{"concept": "概念名", "gics": "GICS大类"}}]}}"""

        try:
            resp = client.messages.create(
                model=model, max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            import json, re
            raw = resp.content[0].text.strip()
            m = re.search(r"\{[\s\S]+\}", raw)
            if m:
                data = json.loads(m.group(0))
                for item in data.get("results", []):
                    if item.get("concept") and item.get("gics") in GICS_OPTIONS:
                        result[item["concept"]] = item["gics"]
        except Exception as e:
            print(f"[update] LLM 分类失败（批次 {i//chunk_size+1}）: {e}")
        time.sleep(1)

    return result


def fetch_akshare_concepts() -> list[str]:
    """从 AKShare 拉取东方财富当前全部概念板块名称。"""
    try:
        import akshare as ak
        df = ak.stock_board_concept_name_em()
        return df["板块名称"].dropna().tolist()
    except Exception as e:
        print(f"[update] AKShare 拉取概念列表失败: {e}")
        return []


def run_update(store: Store, use_llm: bool = False) -> dict:
    """
    主流程：拉取最新概念列表 → 分类 → 入库。
    返回统计信息 dict。
    """
    print("[update] 从 AKShare 拉取最新概念板块列表...")
    all_concepts = fetch_akshare_concepts()
    if not all_concepts:
        print("[update] 无法获取概念列表，退出")
        return {}

    print(f"[update] 共 {len(all_concepts)} 个概念板块")

    # 加载已有映射
    existing = store.load_concept_mappings()
    new_concepts = [c for c in all_concepts if c not in existing]
    print(f"[update] 新增概念 {len(new_concepts)} 个，已有 {len(existing)} 个")

    if not new_concepts:
        print("[update] 无新概念，无需更新")
        return {"total": len(all_concepts), "new": 0, "existing": len(existing)}

    # 自动分类
    auto_mapped = {}
    unknown = []
    for concept in new_concepts:
        gics, source = _auto_classify(concept)
        if gics == "Others":
            unknown.append(concept)
        auto_mapped[concept] = (gics, source)

    # LLM 分类未知概念
    llm_mapped = {}
    if use_llm and unknown:
        print(f"[update] LLM 分类 {len(unknown)} 个未知概念...")
        llm_mapped = _llm_classify(unknown)

    # 入库
    saved = others = 0
    for concept, (gics, source) in auto_mapped.items():
        if concept in llm_mapped:
            gics   = llm_mapped[concept]
            source = "llm"
        confirmed = 1 if source == "seed" else 0
        store.save_concept_mapping(concept, gics, source=source, confirmed=confirmed)
        if gics == "Others":
            others += 1
        saved += 1

    print(f"[update] 入库 {saved} 条，其中 Others（待确认）{others} 条")

    # 打印待确认清单
    if others > 0:
        print(f"\n=== 待人工确认的概念（{others} 个）===")
        print("运行以下命令确认：")
        print("  python scripts/update_concept_sectors.py --set \"概念名称=GICS大类\"")
        print()
        for concept in unknown:
            if concept not in llm_mapped:
                print(f"  ❓ {concept}")

    return {"total": len(all_concepts), "new": saved, "existing": len(existing), "others": others}


def main():
    parser = argparse.ArgumentParser(description="概念板块映射每周更新")
    parser.add_argument("--llm",     action="store_true", help="用 Claude 分类未知概念")
    parser.add_argument("--confirm", action="store_true", help="打印所有待确认概念")
    parser.add_argument("--set",     type=str, default="", help="人工确认：'概念名称=GICS大类'")
    parser.add_argument("--db",      type=str, default="./output/x_agent.db")
    args = parser.parse_args()

    store = Store(args.db)

    # 初始化：把硬编码种子导入 DB（首次运行）
    existing = store.load_concept_mappings()
    if not existing:
        print("[update] 首次运行，导入种子映射...")
        for concept, gics in CONCEPT_TO_GICS.items():
            store.save_concept_mapping(concept, gics, source="seed", confirmed=1)
        print(f"[update] 种子导入完成，共 {len(CONCEPT_TO_GICS)} 条")

    if args.set:
        # 人工确认单条
        if "=" not in args.set:
            print("格式错误，应为 '概念名称=GICS大类'")
            return
        concept, gics = args.set.split("=", 1)
        concept = concept.strip()
        gics    = gics.strip()
        if gics not in GICS_OPTIONS:
            print(f"无效 GICS 大类：{gics}")
            print("可选：" + " / ".join(GICS_OPTIONS))
            return
        store.confirm_concept(concept, gics)
        print(f"✅ 已确认：{concept} → {gics}")
        return

    if args.confirm:
        # 打印待确认清单
        unconfirmed = store.unconfirmed_concepts()
        if not unconfirmed:
            print("✅ 所有概念已确认")
        else:
            print(f"\n=== 待确认概念（{len(unconfirmed)} 个）===\n")
            for item in unconfirmed:
                print(f"  ❓ {item['concept']:20s}  当前→ {item['gics']:25s}  来源: {item['source']}")
            print(f"\n用 --set '概念名=GICS大类' 修正")
        return

    # 正常更新流程
    run_update(store, use_llm=args.llm)


if __name__ == "__main__":
    main()
