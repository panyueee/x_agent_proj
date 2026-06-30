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


def get_concept_mappings(store=None) -> dict[str, str]:
    """
    返回完整概念→GICS 映射。
    优先读 DB（每周更新），DB 为空时退化到硬编码 CONCEPT_TO_GICS。
    """
    if store is not None:
        try:
            db_map = store.load_concept_mappings()
            if db_map:
                # DB 映射覆盖硬编码（DB 为最新权威）
                merged = dict(CONCEPT_TO_GICS)
                merged.update(db_map)
                return merged
        except Exception:
            pass
    return dict(CONCEPT_TO_GICS)


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


ALL_SECTORS = sorted(set(SW_TO_GICS.values()))

# ── 数据加载 ─────────────────────────────────────────────────────────────

def _load_returns_pl(conn, symbols: list[str], min_days: int = 252) -> "pl.DataFrame":
    """从 price_bars 读取日收益率，返回 Polars DataFrame。"""
    import polars as pl
    placeholders = ",".join("?" * len(symbols))
    rows = conn.execute(
        f"""SELECT symbol, timestamp as date, close
            FROM price_bars
            WHERE symbol IN ({placeholders})
              AND market IN ('a_shares','A')
              AND LENGTH(timestamp) = 10
            ORDER BY symbol, date""",
        symbols,
    ).fetchall()
    if not rows:
        return pl.DataFrame()
    df = (
        pl.DataFrame({"symbol": [r[0] for r in rows],
                      "date":   [r[1] for r in rows],
                      "close":  [float(r[2]) for r in rows]})
        .with_columns(pl.col("date").str.to_date())
        .sort(["symbol", "date"])
        .with_columns(
            pl.col("close").pct_change().over("symbol").alias("asset_returns")
        )
        .drop_nulls("asset_returns")
        .filter(pl.col("asset_returns").is_finite())
    )
    # 过滤掉交易日不足的股票
    enough = (
        df.group_by("symbol")
          .agg(pl.len().alias("n"))
          .filter(pl.col("n") >= min_days)["symbol"]
          .to_list()
    )
    return df.filter(pl.col("symbol").is_in(enough)).select("date", "symbol", "asset_returns")


def _load_mkt_cap_pl(conn, symbols: list[str]) -> "pl.DataFrame":
    """
    从 fundamentals 读最新一天市值，填充到所有日期（近似）。
    fundamentals 空时退回 akshare 实时行情。
    """
    import polars as pl

    rows = conn.execute(
        "SELECT symbol, market_cap FROM fundamentals "
        "WHERE date = (SELECT MAX(date) FROM fundamentals) AND market_cap IS NOT NULL"
    ).fetchall()

    # symbols 集合只算一次，避免在循环/推导式中反复 set(symbols)
    sym_set = set(symbols)
    cap_map: dict[str, float] = {r[0]: float(r[1]) for r in rows if r[0] in sym_set}

    if len(cap_map) < len(symbols) * 0.5:
        import akshare as ak
        spot = ak.stock_zh_a_spot_em()
        for _, row in spot.iterrows():
            code = str(row.get("代码", "")).zfill(6)
            cap = row.get("总市值")
            if code in sym_set and cap is not None:
                try:
                    cap_map[code] = float(cap)
                except (TypeError, ValueError):
                    pass

    if not cap_map:
        return pl.DataFrame()

    # 获取所有交易日
    syms_ok = list(cap_map.keys())
    ph = ",".join("?" * len(syms_ok))
    dates = [r[0] for r in conn.execute(
        f"SELECT DISTINCT timestamp FROM price_bars WHERE symbol IN ({ph}) AND LENGTH(timestamp)=10 ORDER BY timestamp",
        syms_ok,
    ).fetchall()]

    rows_out = [{"date": d, "symbol": s, "market_cap": cap_map[s]}
                for s in syms_ok for d in dates]
    return (
        pl.DataFrame(rows_out)
        .with_columns(pl.col("date").str.to_date())
    )


def _load_sector_pl(conn, symbols: list[str], dates: list) -> "pl.DataFrame":
    """从 sw_sector_cache 读行业，构建 one-hot DataFrame。"""
    import polars as pl

    rows = conn.execute(
        "SELECT symbol, gics_sector FROM sw_sector_cache WHERE symbol IN ({})".format(
            ",".join("?" * len(symbols))
        ),
        symbols,
    ).fetchall()
    code_to_gics = {r[0]: r[1] for r in rows}

    syms_ok = [s for s in symbols if s in code_to_gics]
    if not syms_ok:
        return pl.DataFrame()

    records = []
    for s in syms_ok:
        sector = code_to_gics[s]
        one_hot = {sec: (1.0 if sec == sector else 0.0) for sec in ALL_SECTORS}
        for d in dates:
            records.append({"date": d, "symbol": s, **one_hot})

    return pl.DataFrame(records).with_columns(pl.col("date").cast(pl.Date))


def _load_value_pl(conn, symbols: list[str], mkt_cap_df: "pl.DataFrame") -> "Optional[pl.DataFrame]":
    """
    构建 Value 因子输入：book_price（P/B倒数）+ sales_price（P/S倒数）。
    cf_price 暂缺，跳过整个 value factor。
    """
    import polars as pl

    # book_price = 1/pb，从 fundamentals 取最新 pb
    bp_rows = conn.execute(
        "SELECT symbol, book_price FROM fundamentals "
        "WHERE date = (SELECT MAX(date) FROM fundamentals) AND book_price IS NOT NULL"
    ).fetchall()
    sym_set = set(symbols)
    bp_map = {r[0]: float(r[1]) for r in bp_rows if r[0] in sym_set}

    # sales_price = total_revenue / market_cap，从 quarterly_financials 取最新一期
    qf_rows = conn.execute(
        """SELECT qf.symbol, qf.total_revenue
           FROM quarterly_financials qf
           INNER JOIN (
               SELECT symbol, MAX(report_date) as max_date
               FROM quarterly_financials
               WHERE symbol IN ({})
               GROUP BY symbol
           ) latest ON qf.symbol = latest.symbol AND qf.report_date = latest.max_date
           WHERE qf.total_revenue IS NOT NULL""".format(",".join("?" * len(symbols))),
        symbols,
    ).fetchall()
    rev_map = {r[0]: float(r[1]) for r in qf_rows}

    syms_ok = [s for s in symbols if s in bp_map and s in rev_map]
    if len(syms_ok) < 10:
        return None

    # market_cap: 取最新一天
    cap_latest = (
        mkt_cap_df.sort("date", descending=True)
        .group_by("symbol").first()
        .select("symbol", "market_cap")
    )
    cap_dict = dict(zip(cap_latest["symbol"].to_list(), cap_latest["market_cap"].to_list()))

    dates = mkt_cap_df["date"].unique().sort().to_list()
    records = []
    for s in syms_ok:
        cap = cap_dict.get(s, 1.0)
        sp = rev_map[s] / cap if cap > 0 else None
        if sp is None or sp <= 0:
            continue
        for d in dates:
            records.append({"date": d, "symbol": s,
                             "book_price": bp_map[s],
                             "sales_price": sp,
                             "cf_price": bp_map[s]})   # cf 用 book 近似
    if not records:
        return None
    return pl.DataFrame(records).with_columns(pl.col("date").cast(pl.Date))


# ── 主模型入口 ────────────────────────────────────────────────────────────

def run_factor_model(store, cfg: dict, min_days: int = 252,
                     max_symbols: int = 300) -> Optional[dict]:
    """
    运行 toraniko 因子模型。
    - 从 price_bars / fundamentals / sw_sector_cache 加载数据
    - 计算 mom + sze（有 value 数据时加 val）style factor
    - 返回 {"factor_returns": pl.DataFrame, "residuals": pl.DataFrame, "n_symbols": int}
    - 数据不足时返回 None
    """
    try:
        from toraniko.model import estimate_factor_returns
        from toraniko.styles import factor_mom, factor_sze, factor_val
        import polars as pl
    except ImportError:
        print("[factor] toraniko 未安装，跳过")
        return None

    conn = store.conn

    # 1. 找有足够数据的 A 股
    rows = conn.execute(
        """SELECT symbol, COUNT(*) as n FROM price_bars
           WHERE market IN ('a_shares','A') AND LENGTH(timestamp)=10
             AND (symbol LIKE '0%' OR symbol LIKE '3%' OR symbol LIKE '6%')
           GROUP BY symbol HAVING n >= ?
           ORDER BY n DESC LIMIT ?""",
        (min_days, max_symbols),
    ).fetchall()
    symbols = [r[0] for r in rows]
    if len(symbols) < 10:
        print(f"[factor] 数据不足（{len(symbols)} 只），跳过")
        return None

    print(f"[factor] 加载 {len(symbols)} 只股票数据...")

    # 2. 构建四张 Polars 表
    returns_df = _load_returns_pl(conn, symbols, min_days)
    if returns_df.is_empty():
        print("[factor] returns 为空，跳过")
        return None

    mkt_cap_df = _load_mkt_cap_pl(conn, symbols)
    if mkt_cap_df.is_empty():
        print("[factor] market_cap 为空，跳过")
        return None

    all_dates = returns_df["date"].unique().sort().to_list()
    sector_df = _load_sector_pl(conn, symbols, all_dates)
    if sector_df.is_empty():
        print("[factor] sector 数据为空（sw_sector_cache 未建立？），跳过")
        return None

    # 3. style factors
    mom_df = factor_mom(returns_df, trailing_days=min_days,
                        half_life=min_days // 2, lag=20).collect()
    sze_df = factor_sze(mkt_cap_df).collect()
    style_df = mom_df.join(sze_df, on=["date", "symbol"], how="inner").drop_nulls()

    # value factor（可选，inner join 确保不引入 null 行）
    val_df = _load_value_pl(conn, symbols, mkt_cap_df)
    if val_df is not None:
        val_score = factor_val(val_df).collect()
        style_df = style_df.join(val_score, on=["date", "symbol"], how="inner")

    # Polars drop_nulls 不会过滤 float NaN；必须显式过滤 NaN 行。
    # 用 0 填充会让某列全为 0，导致截面回归矩阵奇异（SVD 不收敛）。
    style_cols = [c for c in style_df.columns if c not in ("date", "symbol")]
    style_df = (
        style_df
        .filter(pl.all_horizontal([pl.col(c).is_not_nan() for c in style_cols]))
        .drop_nulls(style_cols)
    )
    print(f"[factor] style_df 有效行: {style_df.shape[0]}（过滤 NaN 后）")

    # 4. 取四表交集：以 style_df 的日期为准（mom 需要足够历史，日期集最小）
    sym_set = (
        set(returns_df["symbol"].unique())
        & set(mkt_cap_df["symbol"].unique())
        & set(sector_df["symbol"].unique())
        & set(style_df["symbol"].unique())
    )
    date_set = (
        set(returns_df["date"].unique())
        & set(style_df["date"].unique())
    )

    def _f(df):
        filtered = df.filter(
            pl.col("symbol").is_in(list(sym_set)) &
            pl.col("date").is_in(list(date_set))
        )
        # 确保每个日期每只股票恰好一行（去重取最后一条）
        return filtered.unique(subset=["date", "symbol"], keep="last")

    print(f"[factor] 公共集: {len(sym_set)} 只 × {len(date_set)} 天")

    # 每个日期至少需要有足够股票才能做截面回归
    min_stocks_per_day = max(len(ALL_SECTORS) + len(style_cols) + 5, 20)
    valid_dates = []
    r_f = _f(returns_df)
    for d in sorted(date_set):
        n = r_f.filter(pl.col("date") == d).shape[0]
        if n >= min_stocks_per_day:
            valid_dates.append(d)
    date_set = set(valid_dates)
    print(f"[factor] 有效日期（≥{min_stocks_per_day}只）: {len(date_set)} 天")

    factor_ret, residuals = estimate_factor_returns(
        _f(returns_df), _f(mkt_cap_df), _f(sector_df), _f(style_df)
    )

    print(f"[factor] 因子收益率: {factor_ret.shape[0]} 行 × {factor_ret.shape[1]} 列")
    return {
        "factor_returns": factor_ret,
        "residuals":      residuals,
        "n_symbols":      len(sym_set),
    }


def print_data_checklist(store, cfg: dict) -> None:
    """打印数据就绪状态，方便调试。"""
    conn = store.conn
    pb  = conn.execute("SELECT COUNT(DISTINCT symbol), COUNT(*) FROM price_bars WHERE market IN ('a_shares','A') AND LENGTH(timestamp)=10").fetchone()
    fd  = conn.execute("SELECT COUNT(*) FROM fundamentals").fetchone()[0]
    qf  = conn.execute("SELECT COUNT(DISTINCT symbol) FROM quarterly_financials").fetchone()[0]
    try:
        sc = conn.execute("SELECT COUNT(*) FROM sw_sector_cache").fetchone()[0]
    except Exception:
        sc = 0
    print(f"\n=== toraniko 数据就绪状态 ===")
    print(f"  price_bars     : {'✅' if pb[0] > 100 else '❌'} {pb[0]} 只 / {pb[1]:,} 条")
    print(f"  fundamentals   : {'✅' if fd > 0 else '❌'} {fd} 条（市值+PB）")
    print(f"  quarterly_fin  : {'✅' if qf > 100 else '⚠️'} {qf} 只（营收+FCF）")
    print(f"  sw_sector_cache: {'✅' if sc > 100 else '❌'} {sc} 只（申万行业）")
    print()
