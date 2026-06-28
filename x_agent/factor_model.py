"""多因子风险模型（toraniko）— A 股适配层。

toraniko 是 Polars 原生的多因子模型库，核心因子：
  - Market（市场因子）
  - Sector（行业因子，11 个 0/1 指示列）
  - Momentum（252 日动量）
  - Value（账价比 / 销价比 / 现金流价比）
  - Size（市值对数）

数据需求（见 DATA_REQUIREMENTS 常量）：
  - 日收益率：至少 252 交易日（约 1 年）
  - 市值：每日浮动市值（亿元）
  - 估值指标：P/B 倒数（账价比）、P/S 倒数、P/CF 倒数
  - 行业分类：申万一级行业（映射到 toraniko GICS 11 行业）

目前 AKShare 可提供的：
  - 日收益率：ak.stock_zh_a_hist（日频，需回拉 1 年+）
  - 市值：ak.stock_individual_info_em 或 ak.stock_zh_a_spot_em
  - P/B：ak.stock_individual_info_em（每日末市净率）
  - 行业：ak.stock_board_industry_name_em（申万一级）

目前 AKShare 不能直接提供的（需要财报数据）：
  - P/S（市销率倒数）：需要年度/季度营收
  - P/CF（现金流价格比）：需要现金流量表
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

import numpy as np
import pandas as pd

# ── 数据需求清单（供调用方参考）──
DATA_REQUIREMENTS = {
    "price_returns": {
        "source":   "AKShare ak.stock_zh_a_hist / price_bars 表",
        "columns":  ["date", "symbol", "asset_returns"],
        "min_rows": 252,
        "note":     "日频收益率，至少 1 年历史",
        "status":   "缺 — price_bars 表当前只有少量数据点，需持续积累或一次性回填",
    },
    "market_cap": {
        "source":   "AKShare ak.stock_individual_info_em 字段 '总市值'",
        "columns":  ["date", "symbol", "market_cap"],
        "min_rows": 1,
        "note":     "每日总市值（亿元），Momentum/Size 因子必须",
        "status":   "缺 — 未实现抓取，需新增到 finance_fetcher.py",
    },
    "book_price": {
        "source":   "AKShare ak.stock_individual_info_em 字段 '市净率' 取倒数",
        "columns":  ["date", "symbol", "book_price"],
        "note":     "P/B 倒数 = 账价比，Value 因子之一",
        "status":   "缺 — 未实现抓取",
    },
    "sales_price": {
        "source":   "需财报数据（营收/市值），AKShare 不直接提供",
        "columns":  ["date", "symbol", "sales_price"],
        "note":     "P/S 倒数，Value 因子之一；可用 ak.stock_financial_analysis_indicator",
        "status":   "缺 — 需财报接口",
    },
    "cf_price": {
        "source":   "需财报数据（经营现金流/市值），AKShare 不直接提供",
        "columns":  ["date", "symbol", "cf_price"],
        "note":     "P/CF 倒数，Value 因子之一；暂时可省略，只用 book_price",
        "status":   "缺 — 可暂时跳过",
    },
    "sector": {
        "source":   "AKShare ak.stock_board_industry_name_em（申万一级行业）",
        "columns":  ["date", "symbol"] + ["Technology", "Financials", "Energy",
                     "Industrials", "Consumer", "Healthcare", "Materials",
                     "Real Estate", "Utilities", "Communication", "Others"],
        "note":     "申万一级 → toraniko GICS 行业映射，0/1 指示",
        "status":   "缺 — 需新增行业抓取模块",
    },
}

# ── 申万一级（31 个，2021 年修订版）→ toraniko GICS 11 大类映射 ──
SW_TO_GICS: dict[str, str] = {
    # 科技
    "计算机":     "Technology",
    "电子":       "Technology",
    # 通信与传媒
    "通信":       "Communication Services",
    "传媒":       "Communication Services",
    # 医疗
    "医药生物":   "Health Care",
    "美容护理":   "Health Care",          # 2021 新增（从医药生物拆出）
    # 金融
    "银行":       "Financials",
    "非银金融":   "Financials",
    # 必选消费
    "食品饮料":   "Consumer Staples",
    "农林牧渔":   "Consumer Staples",
    # 可选消费
    "纺织服饰":   "Consumer Discretionary",
    "汽车":       "Consumer Discretionary",
    "商贸零售":   "Consumer Discretionary",
    "家用电器":   "Consumer Discretionary",
    "社会服务":   "Consumer Discretionary",  # 2021 新增（从商贸零售拆出）
    # 工业
    "轻工制造":   "Industrials",
    "机械设备":   "Industrials",
    "国防军工":   "Industrials",
    "交通运输":   "Industrials",
    "建筑装饰":   "Industrials",
    # 原材料
    "建筑材料":   "Materials",
    "基础化工":   "Materials",
    "钢铁":       "Materials",
    "有色金属":   "Materials",
    # 能源
    "煤炭":       "Energy",
    "石油石化":   "Energy",
    # 公用事业
    "电力设备":   "Utilities",
    "公用事业":   "Utilities",
    "环保":       "Utilities",             # 2021 新增（从公用事业拆出）
    # 房地产
    "房地产":     "Real Estate",
    # 综合
    "综合":       "Industrials",
}

# ── 概念板块 → GICS 大类映射 ──
# 来源：东方财富概念板块（ak.stock_board_concept_name_em），动态变化
# 原则：按核心驱动产业归类；跨行业概念归入主导方向
CONCEPT_TO_GICS: dict[str, str] = {
    # ── 科技 / AI ──
    "人工智能":       "Technology",
    "大模型":         "Technology",
    "AI算力":         "Technology",
    "算力":           "Technology",
    "ChatGPT概念":    "Technology",
    "AIGC":           "Technology",
    "云计算":         "Technology",
    "大数据":         "Technology",
    "数字经济":       "Technology",
    "工业互联网":     "Technology",
    "物联网":         "Technology",
    "区块链":         "Technology",
    "网络安全":       "Technology",
    "信创":           "Technology",
    "操作系统":       "Technology",
    "数据库":         "Technology",
    # ── 半导体 ──
    "半导体":         "Technology",
    "芯片":           "Technology",
    "晶圆代工":       "Technology",
    "存储芯片":       "Technology",
    "第三代半导体":   "Technology",
    "碳化硅":         "Technology",
    "IGBT":           "Technology",
    "国产替代":       "Technology",
    # ── 机器人 / 硬件 ──
    "人形机器人":     "Industrials",
    "工业机器人":     "Industrials",
    "无人机":         "Industrials",
    "低空经济":       "Industrials",
    "航空发动机":     "Industrials",
    # ── 新能源 ──
    "新能源":         "Utilities",
    "光伏":           "Utilities",
    "风电":           "Utilities",
    "储能":           "Utilities",
    "氢能源":         "Utilities",
    "核电":           "Utilities",
    "分布式能源":     "Utilities",
    # ── 新能源汽车 ──
    "新能源汽车":     "Consumer Discretionary",
    "锂电池":         "Materials",
    "固态电池":       "Materials",
    "碳酸锂":         "Materials",
    "磷酸铁锂":       "Materials",
    "钠离子电池":     "Materials",
    "电池回收":       "Materials",
    # ── 医药 / 医疗 ──
    "创新药":         "Health Care",
    "CXO":            "Health Care",
    "医疗器械":       "Health Care",
    "AI制药":         "Health Care",
    "基因测序":       "Health Care",
    "医美":           "Health Care",
    "减肥药":         "Health Care",
    "肿瘤免疫":       "Health Care",
    # ── 消费 ──
    "白酒":           "Consumer Staples",
    "消费复苏":       "Consumer Staples",
    "免税":           "Consumer Discretionary",
    "宠物经济":       "Consumer Discretionary",
    "跨境电商":       "Consumer Discretionary",
    # ── 金融 ──
    "券商":           "Financials",
    "保险":           "Financials",
    "银行":           "Financials",
    "REITs":          "Real Estate",
    # ── 周期 / 资源 ──
    "黄金":           "Materials",
    "铜":             "Materials",
    "稀土":           "Materials",
    "钨":             "Materials",
    "锰":             "Materials",
    "钼":             "Materials",
    # ── 政策主题 ──
    "央企改革":       "Industrials",
    "国企改革":       "Industrials",
    "一带一路":       "Industrials",
    "军民融合":       "Industrials",
    "乡村振兴":       "Consumer Staples",
    "碳中和":         "Utilities",
    "碳达峰":         "Utilities",
    # ── 通信 ──
    "卫星通信":       "Communication Services",
    "北斗导航":       "Communication Services",
    "5G":             "Communication Services",
    "6G":             "Communication Services",
    # ── 其他 ──
    "港股通":         "Others",
    "沪深300":        "Others",
    "中证500":        "Others",
}


def _returns_from_store(store, symbols: list[str]) -> Optional[pd.DataFrame]:
    """从 price_bars 读取日收益率，列=symbol。"""
    frames = {}
    for sym in symbols:
        rows = store.conn.execute(
            "SELECT timestamp, close FROM price_bars WHERE symbol=? ORDER BY timestamp",
            (sym,),
        ).fetchall()
        if len(rows) < 10:
            continue
        s = pd.Series({r[0][:10]: float(r[1]) for r in rows}, name=sym)
        frames[sym] = s
    if not frames:
        return None
    prices = pd.DataFrame(frames)
    prices.index = pd.to_datetime(prices.index)
    return prices.pct_change().dropna(how="all")


def check_data_readiness(store, cfg: dict) -> dict:
    """
    检查运行 toraniko 需要的数据是否就绪。
    返回 {data_type: {"ready": bool, "rows": int, "needed": int, "status": str}}
    """
    fin_cfg = cfg.get("finance", {})
    a_symbols = [item["code"] for item in fin_cfg.get("a_shares", [])]

    result = {}

    # 1. 日收益率
    returns = _returns_from_store(store, a_symbols)
    if returns is not None and not returns.empty:
        min_rows = returns.shape[0]
        result["price_returns"] = {
            "ready":  min_rows >= 252,
            "rows":   min_rows,
            "needed": 252,
            "status": f"{'✅' if min_rows >= 252 else '❌'} {min_rows}/252 行（{len(returns.columns)} 个品种）",
        }
    else:
        result["price_returns"] = {"ready": False, "rows": 0, "needed": 252,
                                    "status": "❌ price_bars 表无数据"}

    # 2. 市值（检查 DB 里有没有 market_cap 字段）
    try:
        mc_rows = store.conn.execute(
            "SELECT COUNT(*) FROM price_bars WHERE market_cap IS NOT NULL"
        ).fetchone()[0]
        result["market_cap"] = {
            "ready":  mc_rows > 0,
            "rows":   mc_rows,
            "needed": len(a_symbols),
            "status": f"{'✅' if mc_rows > 0 else '❌'} {mc_rows} 行（需新增 market_cap 列到 price_bars）",
        }
    except Exception:
        result["market_cap"] = {"ready": False, "rows": 0, "needed": len(a_symbols),
                                 "status": "❌ price_bars 表无 market_cap 列"}

    # 3. 估值指标（暂无）
    result["book_price"]  = {"ready": False, "rows": 0, "needed": len(a_symbols),
                              "status": "❌ 未实现：需 ak.stock_individual_info_em 市净率字段"}
    result["sales_price"] = {"ready": False, "rows": 0, "needed": len(a_symbols),
                              "status": "❌ 未实现：需财报营收数据（ak.stock_financial_analysis_indicator）"}
    result["cf_price"]    = {"ready": False, "rows": 0, "needed": len(a_symbols),
                              "status": "⚠️  可暂时跳过（Value 因子只用 book_price 也能跑）"}

    # 4. 行业分类（检查 industry_nodes）
    chain_count = store.conn.execute(
        "SELECT COUNT(DISTINCT code) FROM industry_nodes"
    ).fetchone()[0]
    result["sector"] = {
        "ready":  chain_count >= len(a_symbols),
        "rows":   chain_count,
        "needed": len(a_symbols),
        "status": f"{'✅' if chain_count >= len(a_symbols) else '⚠️'} industry_nodes 有 {chain_count} 个节点，"
                  f"需要 {len(a_symbols)} 个；需添加 SW 行业列",
    }

    return result


def run_factor_model(store, cfg: dict) -> Optional[dict]:
    """
    尝试运行 toraniko 因子模型，数据不足时返回 None 并打印缺失项。
    就绪条件：price_returns ≥ 252 行 + market_cap 存在。
    """
    try:
        import toraniko
        from toraniko.styles import factor_mom
    except ImportError:
        print("[factor] toraniko 未安装，跳过")
        return None

    fin_cfg = cfg.get("finance", {})
    a_symbols = [item["code"] for item in fin_cfg.get("a_shares", [])]

    readiness = check_data_readiness(store, cfg)
    pr = readiness.get("price_returns", {})
    mc = readiness.get("market_cap", {})

    if not pr.get("ready") or not mc.get("ready"):
        print("[factor] 数据未就绪，跳过 toraniko 因子模型：")
        for k, v in readiness.items():
            print(f"  {k}: {v['status']}")
        return None

    # 数据就绪时在这里扩展实际调用 toraniko
    # （目前数据未就绪，先返回 None）
    return None


def print_data_checklist(store, cfg: dict) -> None:
    """打印数据就绪状态清单，供开发者快速了解缺口。"""
    readiness = check_data_readiness(store, cfg)
    print("\n=== toraniko 数据就绪状态 ===")
    for k, v in readiness.items():
        req = DATA_REQUIREMENTS.get(k, {})
        print(f"\n[{k}]")
        print(f"  状态  : {v['status']}")
        if req.get("source"):
            print(f"  来源  : {req['source']}")
        if req.get("note"):
            print(f"  说明  : {req['note']}")
    print()
