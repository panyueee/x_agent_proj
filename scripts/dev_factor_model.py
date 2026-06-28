"""
toraniko 因子模型开发/验证脚本（测试数据）

流程：
  1. 从 price_bars 取数据最充分的 N 只 A 股（测试集）
  2. 从 akshare 一次性抓全量 A 股市值（stock_zh_a_spot_em）
  3. 从 akshare 抓申万一级行业成分（构建 code→行业 映射）
  4. 构建 toraniko 所需的 4 张 Polars DataFrame
  5. 运行 estimate_factor_returns，输出因子收益率 & 残差
  6. 写结果到 output/factor_returns.csv

用法：
  .venv/bin/python scripts/dev_factor_model.py
  .venv/bin/python scripts/dev_factor_model.py --n 50 --min-days 250
"""
from __future__ import annotations

import argparse
import sqlite3
import datetime as dt
from pathlib import Path

import pandas as pd
import polars as pl

DB_PATH = "output/x_agent.db"

# 申万一级行业 → toraniko GICS 映射
SW_TO_GICS: dict[str, str] = {
    "计算机":   "Technology",
    "电子":     "Technology",
    "通信":     "Communication Services",
    "传媒":     "Communication Services",
    "医药生物": "Health Care",
    "美容护理": "Health Care",
    "银行":     "Financials",
    "非银金融": "Financials",
    "食品饮料": "Consumer Staples",
    "农林牧渔": "Consumer Staples",
    "纺织服饰": "Consumer Discretionary",
    "汽车":     "Consumer Discretionary",
    "商贸零售": "Consumer Discretionary",
    "家用电器": "Consumer Discretionary",
    "社会服务": "Consumer Discretionary",
    "轻工制造": "Industrials",
    "机械设备": "Industrials",
    "国防军工": "Industrials",
    "交通运输": "Industrials",
    "建筑装饰": "Industrials",
    "电力设备": "Industrials",
    "建筑材料": "Materials",
    "基础化工": "Materials",
    "钢铁":     "Materials",
    "有色金属": "Materials",
    "煤炭":     "Energy",
    "石油石化": "Energy",
    "公用事业": "Utilities",
    "环保":     "Utilities",
    "房地产":   "Real Estate",
    "综合":     "Industrials",
}

ALL_SECTORS = sorted(set(SW_TO_GICS.values()))


# ── Step 1: 从 DB 取测试股票列表 ─────────────────────────────────────────
def get_test_symbols(n: int, min_days: int) -> list[str]:
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        """
        SELECT symbol, COUNT(*) as cnt
        FROM price_bars
        WHERE market IN ('a_shares', 'A')
          AND LENGTH(timestamp) = 10
          AND (symbol LIKE '0%' OR symbol LIKE '3%' OR symbol LIKE '6%')
        GROUP BY symbol
        HAVING cnt >= ?
        ORDER BY cnt DESC
        LIMIT ?
        """,
        (min_days, n),
    ).fetchall()
    conn.close()
    # 过滤 B 股（末位 B）和其他非 A 股代码
    syms = [r[0] for r in rows if not r[0].endswith("B") and len(r[0]) == 6]
    print(f"[data] 测试股票 {len(syms)} 只（≥{min_days} 天）")
    return syms


# ── Step 2: 从 DB 加载价格数据，构建日收益率 DataFrame ────────────────────
def load_returns(symbols: list[str]) -> pl.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    placeholders = ",".join("?" * len(symbols))
    rows = conn.execute(
        f"""
        SELECT symbol, timestamp as date, close
        FROM price_bars
        WHERE symbol IN ({placeholders})
          AND market IN ('a_shares', 'A')
          AND LENGTH(timestamp) = 10
        ORDER BY symbol, date
        """,
        symbols,
    ).fetchall()
    conn.close()

    df = (
        pl.DataFrame({"symbol": [r[0] for r in rows],
                      "date":   [r[1] for r in rows],
                      "close":  [float(r[2]) for r in rows]})
        .with_columns(pl.col("date").cast(pl.Date))
        .sort(["symbol", "date"])
        .with_columns(
            pl.col("close")
              .pct_change()
              .over("symbol")
              .alias("asset_returns")
        )
        .drop_nulls("asset_returns")
        .filter(pl.col("asset_returns").is_finite())
        .select("date", "symbol", "asset_returns")
    )
    print(f"[data] returns: {df.shape[0]} 行 × {df['symbol'].n_unique()} 只 × "
          f"{df['date'].n_unique()} 天")
    return df


# ── Step 3: 从 akshare 抓市值（一次性全量） ──────────────────────────────
def fetch_market_cap(symbols: list[str]) -> dict[str, float]:
    """返回 {symbol: 总市值（元）}"""
    import akshare as ak
    print("[akshare] 抓取全量 A 股实时市值...")
    spot = ak.stock_zh_a_spot_em()
    # 列名：代码 / 总市值
    cap_map: dict[str, float] = {}
    for _, row in spot.iterrows():
        code = str(row.get("代码", "")).zfill(6)
        cap = row.get("总市值")
        if code in symbols and cap is not None:
            try:
                cap_map[code] = float(cap)
            except (TypeError, ValueError):
                pass
    print(f"[akshare] 命中 {len(cap_map)}/{len(symbols)} 只")
    return cap_map


def build_mkt_cap_df(returns_df: pl.DataFrame, cap_map: dict[str, float]) -> pl.DataFrame:
    """把当前市值复制到所有交易日（测试近似，生产环境需逐日历史市值）。"""
    syms_with_cap = [s for s in returns_df["symbol"].unique().to_list() if s in cap_map]
    dates = returns_df["date"].unique().sort().to_list()

    rows = [
        {"date": d, "symbol": s, "market_cap": cap_map[s]}
        for s in syms_with_cap
        for d in dates
    ]
    df = pl.DataFrame(rows).with_columns(pl.col("date").cast(pl.Date))
    print(f"[data] market_cap: {df.shape[0]} 行，{df['symbol'].n_unique()} 只")
    return df


# ── Step 4: 申万行业 → sector one-hot ────────────────────────────────────
SECTOR_CACHE_TABLE = "sw_sector_cache"


def _load_sector_cache() -> dict[str, str]:
    """从 DB 加载已缓存的申万行业映射。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            f"SELECT symbol, gics_sector FROM {SECTOR_CACHE_TABLE}"
        ).fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception:
        return {}
    finally:
        conn.close()


def _save_sector_cache(code_to_gics: dict[str, str]) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {SECTOR_CACHE_TABLE} (
            symbol      TEXT PRIMARY KEY,
            gics_sector TEXT,
            updated_at  TEXT
        )
    """)
    now = dt.datetime.utcnow().isoformat()
    conn.executemany(
        f"INSERT OR REPLACE INTO {SECTOR_CACHE_TABLE} (symbol, gics_sector, updated_at) VALUES (?,?,?)",
        [(s, g, now) for s, g in code_to_gics.items()],
    )
    conn.commit()
    conn.close()
    print(f"[cache] 行业映射已写入 DB（{len(code_to_gics)} 只）")


def fetch_sw_sector_map(symbols: list[str], force_refresh: bool = False) -> dict[str, str]:
    """
    返回 {symbol: GICS_sector}。
    优先读 DB 缓存；缓存缺失或 force_refresh=True 时重新从 akshare 抓取。
    """
    sym_set = set(symbols)

    if not force_refresh:
        cached = _load_sector_cache()
        if cached:
            hit = sum(1 for s in symbols if s in cached)
            print(f"[cache] 命中行业缓存 {hit}/{len(symbols)} 只（如需刷新加 --refresh-sector）")
            if hit >= len(symbols) * 0.8:   # 80% 命中率则直接用缓存
                return {s: cached[s] for s in symbols if s in cached}

    import akshare as ak
    print("[akshare] 抓取申万行业分类（全量，首次约 10 分钟）...")
    try:
        ind_names = ak.stock_board_industry_name_em()
        name_col = "板块名称" if "板块名称" in ind_names.columns else ind_names.columns[0]
        sw_names = [n for n in ind_names[name_col].tolist() if n in SW_TO_GICS]
    except Exception as e:
        print(f"[warn] 无法获取行业列表: {e}，使用硬编码测试集")
        return _hardcoded_sectors(symbols)

    # 全量抓取（不只限 symbols，以便缓存对未来所有股票有效）
    all_code_to_gics: dict[str, str] = {}
    for sw_name in sw_names:
        try:
            cons = ak.stock_board_industry_cons_em(symbol=sw_name)
            code_col = "代码" if "代码" in cons.columns else cons.columns[0]
            for code in cons[code_col].tolist():
                c = str(code).zfill(6)
                all_code_to_gics[c] = SW_TO_GICS[sw_name]
        except Exception:
            continue

    _save_sector_cache(all_code_to_gics)
    hit = sum(1 for s in symbols if s in all_code_to_gics)
    print(f"[akshare] 行业命中 {hit}/{len(symbols)} 只")
    return {s: all_code_to_gics[s] for s in symbols if s in all_code_to_gics}


def _hardcoded_sectors(symbols: list[str]) -> dict[str, str]:
    """最小测试集：30 只常见股票的行业硬编码。"""
    mapping = {
        "600519": "Consumer Staples", "000858": "Consumer Staples",
        "600887": "Consumer Staples", "002304": "Consumer Staples",
        "000001": "Financials",       "600036": "Financials",
        "601318": "Financials",       "600030": "Financials",
        "601166": "Financials",       "601688": "Financials",
        "000651": "Consumer Discretionary", "000333": "Consumer Discretionary",
        "002594": "Consumer Discretionary", "601888": "Consumer Discretionary",
        "002415": "Technology",       "000725": "Technology",
        "600570": "Technology",       "688036": "Technology",
        "600276": "Health Care",      "300760": "Health Care",
        "300015": "Health Care",
        "601668": "Industrials",      "300750": "Industrials",
        "000157": "Industrials",
        "600585": "Materials",        "601899": "Materials",
        "600900": "Utilities",        "601985": "Utilities",
        "601857": "Energy",           "600028": "Energy",
        "600048": "Real Estate",
        "600050": "Communication Services", "600941": "Communication Services",
    }
    return {s: mapping[s] for s in symbols if s in mapping}


def build_sector_df(returns_df: pl.DataFrame, code_to_gics: dict[str, str]) -> pl.DataFrame:
    """构建 one-hot sector DataFrame。"""
    syms_with_sector = [s for s in returns_df["symbol"].unique().to_list()
                        if s in code_to_gics]
    dates = returns_df["date"].unique().sort().to_list()

    rows = []
    for s in syms_with_sector:
        sector = code_to_gics[s]
        one_hot = {sec: (1.0 if sec == sector else 0.0) for sec in ALL_SECTORS}
        for d in dates:
            rows.append({"date": d, "symbol": s, **one_hot})

    df = pl.DataFrame(rows).with_columns(pl.col("date").cast(pl.Date))
    print(f"[data] sector: {df.shape[0]} 行，{df['symbol'].n_unique()} 只，"
          f"{len(ALL_SECTORS)} 个行业")
    return df


# ── Step 5: 过滤三表公共股票集，构建 style_df ─────────────────────────────
def build_style_df(returns_df: pl.DataFrame, mkt_cap_df: pl.DataFrame,
                   min_mom_days: int = 252) -> pl.DataFrame:
    from toraniko.styles import factor_mom, factor_sze

    mom = factor_mom(
        returns_df,
        trailing_days=min_mom_days,
        half_life=min_mom_days // 2,
        lag=20,
    ).collect()
    sze = factor_sze(mkt_cap_df).collect()

    style = mom.join(sze, on=["date", "symbol"], how="inner").drop_nulls()
    print(f"[data] style: {style.shape[0]} 行，{style['symbol'].n_unique()} 只")
    return style


# ── Step 6: 对齐四表，运行 estimate_factor_returns ───────────────────────
def run_model(returns_df, mkt_cap_df, sector_df, style_df) -> tuple[pl.DataFrame, pl.DataFrame]:
    from toraniko.model import estimate_factor_returns

    # 取四表交集股票 & 日期
    sym_set = (
        set(returns_df["symbol"].unique().to_list())
        & set(mkt_cap_df["symbol"].unique().to_list())
        & set(sector_df["symbol"].unique().to_list())
        & set(style_df["symbol"].unique().to_list())
    )
    date_set = (
        set(returns_df["date"].unique().to_list())
        & set(style_df["date"].unique().to_list())
    )
    print(f"[model] 公共股票 {len(sym_set)} 只，公共日期 {len(date_set)} 天")

    def _filter(df):
        return df.filter(
            pl.col("symbol").is_in(list(sym_set)) &
            pl.col("date").is_in(list(date_set))
        )

    factor_ret, residuals = estimate_factor_returns(
        _filter(returns_df),
        _filter(mkt_cap_df),
        _filter(sector_df),
        _filter(style_df),
    )
    return factor_ret, residuals


# ── 主函数 ────────────────────────────────────────────────────────────────
def main(n: int, min_days: int, refresh_sector: bool = False):
    Path("output").mkdir(exist_ok=True)

    symbols = get_test_symbols(n, min_days)
    if len(symbols) < 10:
        print("ERROR: 测试股票不足 10 只，请降低 --min-days")
        return

    returns_df = load_returns(symbols)

    cap_map      = fetch_market_cap(symbols)
    mkt_cap_df   = build_mkt_cap_df(returns_df, cap_map)

    code_to_gics = fetch_sw_sector_map(symbols, force_refresh=refresh_sector)
    if len(code_to_gics) < 5:
        print("[warn] 行业数据不足，退回硬编码")
        code_to_gics = _hardcoded_sectors(symbols)
    sector_df    = build_sector_df(returns_df, code_to_gics)

    style_df     = build_style_df(returns_df, mkt_cap_df)

    print("\n[model] 开始运行 toraniko estimate_factor_returns ...")
    try:
        factor_ret, residuals = run_model(returns_df, mkt_cap_df, sector_df, style_df)
    except Exception as e:
        print(f"[ERROR] 模型运行失败: {e}")
        import traceback; traceback.print_exc()
        return

    # ── 输出 ──────────────────────────────────────────────────────────────
    print("\n=== 因子收益率（前5行）===")
    print(factor_ret.head())

    out_path = "output/factor_returns.csv"
    factor_ret.write_csv(out_path)
    print(f"\n因子收益率已写入 {out_path}  ({factor_ret.shape[0]} 行 × {factor_ret.shape[1]} 列)")

    # 因子收益率统计摘要
    numeric_cols = [c for c in factor_ret.columns if c != "date"]
    stats = factor_ret.select(numeric_cols).describe()
    print("\n=== 因子收益率统计 ===")
    print(stats)

    # 残差最大的5只股票
    if "date" in residuals.columns:
        sym_cols = [c for c in residuals.columns if c != "date"]
        abs_mean = {c: residuals[c].abs().mean() for c in sym_cols}
        top5 = sorted(abs_mean.items(), key=lambda x: x[1], reverse=True)[:5]
        print("\n=== 残差绝对均值最大的5只股票 ===")
        for sym, val in top5:
            print(f"  {sym}: {val:.4f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n",              type=int, default=80,   help="测试股票数量")
    parser.add_argument("--min-days",       type=int, default=300,  help="最少交易日数")
    parser.add_argument("--refresh-sector", action="store_true",    help="强制重新抓申万行业（忽略缓存）")
    args = parser.parse_args()
    main(args.n, args.min_days, getattr(args, "refresh_sector", False))
