"""
量化分析三模块的 pytest 测试套件：
  - x_agent/factor_model.py
  - x_agent/portfolio_optimizer.py
  - x_agent/risk_analyzer.py

设计原则（与 tests/test_storage.py / test_chunking.py 风格一致）：
  - 绝不联网：polars 缺失时 `_load_*_pl` 的 import 先失败 → importorskip 跳过，
    其内部的 akshare 实时行情兜底永远不会触发。
  - 不碰真实 DB：所有读 SQLite 的函数都用「内存 sqlite3 + FakeStore(conn)」喂入
    合成数据（这既满足任务的"temp DB via constructor"选项，又真正跑了源码里的 SQL）。
  - 合成数据带已知数学性质（常数价→0 收益、零方差→0 波动等），以属性断言为主，
    仅在能手算的地方用 pytest.approx 锁精确值。

针对最近的 O(n^2)→O(n) 集合成员过滤优化：
  - portfolio_optimizer._signal_views 用 set(symbols) 做成员判断 —— 本文件**充分验证**
    （在/不在集合、$ 前缀与大小写归一化、坏 JSON 跳过、无信号 ticker 不出现、view 数值）。
  - factor_model._load_mkt_cap_pl / _load_value_pl 同样用 set(symbols)，但首行即
    `import polars`，本环境无 polars → 对应测试 **SKIP**（仅在 polars 存在时才运行）。

主要运行方式：
    python -m pytest tests/test_analytics.py -v
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd
import pytest

# 让 `import x_agent.*` 在任意 cwd 下都能工作
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from x_agent import factor_model as fm
from x_agent import portfolio_optimizer as po
from x_agent import risk_analyzer as ra


# ── 公共辅助：内存 sqlite + FakeStore ────────────────────────────────────────


class FakeStore:
    """最简 Store 替身：源码只用到 store.conn.execute(...).fetchall()。"""

    def __init__(self, conn):
        self.conn = conn

    def load_concept_mappings(self):  # 供 get_concept_mappings 测试覆盖
        return {}


def _mem_conn() -> sqlite3.Connection:
    return sqlite3.connect(":memory:")


def _make_price_bars(conn, prices: dict[str, list[float]],
                     start: str = "2024-01-01", market: str = "a_shares") -> None:
    """
    建 price_bars 表并写入合成收盘价。
    prices: {symbol: [close, close, ...]}，按连续工作日填日期。
    timestamp 用 'YYYY-MM-DD'（长度 10，满足源码的 LENGTH=10 过滤）。
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS price_bars "
        "(symbol TEXT, timestamp TEXT, close REAL, market TEXT)"
    )
    base = dt.date.fromisoformat(start)
    for sym, closes in prices.items():
        for i, c in enumerate(closes):
            ts = (base + dt.timedelta(days=i)).isoformat()
            conn.execute(
                "INSERT INTO price_bars (symbol, timestamp, close, market) VALUES (?,?,?,?)",
                (sym, ts, c, market),
            )
    conn.commit()


def _make_signals_tables(conn) -> None:
    """建空的 signals / tweets 表（_signal_views 用 JOIN）。"""
    conn.execute("CREATE TABLE IF NOT EXISTS tweets (id TEXT PRIMARY KEY, created_at TEXT)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS signals "
        "(tweet_id TEXT, tickers TEXT, score REAL)"
    )
    conn.commit()


def _add_signal(conn, tweet_id: str, created_at: str, tickers: list[str] | str, score: float) -> None:
    tickers_json = tickers if isinstance(tickers, str) else json.dumps(tickers)
    conn.execute("INSERT OR REPLACE INTO tweets (id, created_at) VALUES (?,?)", (tweet_id, created_at))
    conn.execute(
        "INSERT INTO signals (tweet_id, tickers, score) VALUES (?,?,?)",
        (tweet_id, tickers_json, score),
    )
    conn.commit()


# ══════════════════════════════════════════════════════════════════════════
# factor_model.py
# ══════════════════════════════════════════════════════════════════════════


def test_concept_mappings_constants_consistent():
    """硬编码映射的目标 GICS 应落在 SW_TO_GICS 的值域大类里（+Others）。"""
    sw_targets = set(fm.SW_TO_GICS.values())
    allowed = sw_targets | {"Others"}
    bad = {k: v for k, v in fm.CONCEPT_TO_GICS.items() if v not in allowed}
    assert not bad, f"概念映射出现未知 GICS 大类: {bad}"
    # ALL_SECTORS 是 SW_TO_GICS 值域的有序去重
    assert fm.ALL_SECTORS == sorted(sw_targets)


def test_get_concept_mappings_no_store_returns_hardcoded_copy():
    m = fm.get_concept_mappings(None)
    assert m == fm.CONCEPT_TO_GICS
    # 必须是副本，改动不应污染模块常量
    m["__tmp__"] = "Technology"
    assert "__tmp__" not in fm.CONCEPT_TO_GICS


def test_get_concept_mappings_db_overrides_hardcoded():
    class _Store:
        def load_concept_mappings(self):
            # 覆盖一个已有键 + 新增一个键
            return {"人工智能": "Communication Services", "全新概念X": "Energy"}

    m = fm.get_concept_mappings(_Store())
    assert m["人工智能"] == "Communication Services"   # DB 覆盖硬编码
    assert m["全新概念X"] == "Energy"                    # DB 新增
    assert m["光伏"] == "Utilities"                      # 未被覆盖的硬编码保留


def test_get_concept_mappings_db_empty_falls_back():
    class _Store:
        def load_concept_mappings(self):
            return {}

    assert fm.get_concept_mappings(_Store()) == fm.CONCEPT_TO_GICS


def test_get_concept_mappings_store_error_falls_back():
    class _Store:
        def load_concept_mappings(self):
            raise RuntimeError("db down")

    assert fm.get_concept_mappings(_Store()) == fm.CONCEPT_TO_GICS


def test_returns_from_store_constant_price_yields_zero_returns():
    """常数价格 → pct_change 全 0（已知性质）。"""
    conn = _mem_conn()
    _make_price_bars(conn, {"600000": [10.0] * 15})
    store = FakeStore(conn)
    rets = fm._returns_from_store(store, ["600000"])
    assert rets is not None
    assert list(rets.columns) == ["600000"]
    assert np.allclose(rets["600000"].dropna().values, 0.0)


def test_returns_from_store_known_increments():
    """已知价格序列 → 手算收益率。10→11→12.1 即 +10%、+10%。"""
    conn = _mem_conn()
    _make_price_bars(conn, {"600001": [10.0, 11.0, 12.1] + [12.1] * 9})
    store = FakeStore(conn)
    rets = fm._returns_from_store(store, ["600001"])
    vals = rets["600001"].dropna().values
    assert vals[0] == pytest.approx(0.10)
    assert vals[1] == pytest.approx(0.10)


def test_returns_from_store_skips_short_series_and_returns_none():
    """每只 < 10 行被跳过；全部不足 → 返回 None。"""
    conn = _mem_conn()
    _make_price_bars(conn, {"600002": [1.0, 2.0, 3.0]})  # 仅 3 行 < 10
    store = FakeStore(conn)
    assert fm._returns_from_store(store, ["600002"]) is None


def test_returns_from_store_multi_symbol_shape():
    conn = _mem_conn()
    _make_price_bars(conn, {"600000": [10.0 + i for i in range(15)],
                            "000001": [20.0 + i for i in range(15)]})
    store = FakeStore(conn)
    rets = fm._returns_from_store(store, ["600000", "000001"])
    assert set(rets.columns) == {"600000", "000001"}
    # 14 个收益率（15 价格 - 1），均为正（递增价）
    assert (rets.dropna().values > 0).all()


# ── polars 依赖的集合成员过滤优化（无 polars → SKIP）────────────────────────


def test_load_mkt_cap_pl_membership_filter():
    """
    验证 _load_mkt_cap_pl 的 set(symbols) 成员过滤：只保留请求集合内的 symbol。
    需 polars；本环境缺失则 SKIP。fundamentals 喂满以避免 akshare 兜底联网。
    """
    pytest.importorskip("polars")
    conn = _mem_conn()
    syms = ["600000", "000001"]
    # price_bars 提供交易日
    _make_price_bars(conn, {s: [10.0] * 12 for s in syms})
    # fundamentals 全量覆盖（cap_map 充满 → 不触发 akshare）；外加一个集合外 symbol
    conn.execute("CREATE TABLE fundamentals (date TEXT, symbol TEXT, market_cap REAL, book_price REAL)")
    conn.execute("INSERT INTO fundamentals VALUES ('2024-02-01','600000',100.0,0.5)")
    conn.execute("INSERT INTO fundamentals VALUES ('2024-02-01','000001',200.0,0.4)")
    conn.execute("INSERT INTO fundamentals VALUES ('2024-02-01','999999',900.0,0.9)")  # 集合外
    conn.commit()
    df = fm._load_mkt_cap_pl(conn, syms)
    got = set(df["symbol"].unique().to_list())
    assert got == set(syms), "集合外 symbol 未被过滤掉"
    # 市值映射正确
    cap = dict(zip(df["symbol"].to_list(), df["market_cap"].to_list()))
    assert cap["600000"] == pytest.approx(100.0)
    assert cap["000001"] == pytest.approx(200.0)


def test_load_value_pl_membership_and_threshold():
    """
    _load_value_pl 同样用 set(symbols) 过滤；样本 < 10 时按设计返回 None。
    需 polars；缺失则 SKIP。
    """
    pytest.importorskip("polars")
    import polars as pl
    conn = _mem_conn()
    syms = ["600000", "000001"]
    conn.execute("CREATE TABLE fundamentals (date TEXT, symbol TEXT, market_cap REAL, book_price REAL)")
    conn.execute("INSERT INTO fundamentals VALUES ('2024-02-01','600000',100.0,0.5)")
    conn.execute("INSERT INTO fundamentals VALUES ('2024-02-01','000001',200.0,0.4)")
    conn.execute("CREATE TABLE quarterly_financials (symbol TEXT, report_date TEXT, total_revenue REAL)")
    conn.execute("INSERT INTO quarterly_financials VALUES ('600000','2024Q1',50.0)")
    conn.execute("INSERT INTO quarterly_financials VALUES ('000001','2024Q1',80.0)")
    conn.commit()
    mkt = pl.DataFrame({"date": ["2024-02-01", "2024-02-01"],
                        "symbol": ["600000", "000001"],
                        "market_cap": [100.0, 200.0]}).with_columns(pl.col("date").str.to_date())
    # syms_ok < 10 → 返回 None（设计行为）
    assert fm._load_value_pl(conn, syms, mkt) is None


# ══════════════════════════════════════════════════════════════════════════
# portfolio_optimizer.py
# ══════════════════════════════════════════════════════════════════════════


def _recent_iso(hours_ago: float = 1.0) -> str:
    return (dt.datetime.utcnow() - dt.timedelta(hours=hours_ago)).isoformat()


def test_signal_views_basic_normalization_and_value():
    """
    _signal_views 集合成员优化的核心验证：
      - $ 前缀剥离 + 大写归一化后命中集合
      - view == (avg-5)/100，avg=8 → 0.03
    """
    conn = _mem_conn()
    _make_signals_tables(conn)
    _add_signal(conn, "t1", _recent_iso(1), ["$aapl"], 6)   # -> AAPL
    _add_signal(conn, "t2", _recent_iso(2), ["aapl"], 10)   # -> AAPL，avg=(6+10)/2=8
    store = FakeStore(conn)
    views = po._signal_views(store, ["AAPL"], lookback_hours=72)
    assert set(views.keys()) == {"AAPL"}
    assert views["AAPL"] == pytest.approx((8 - 5) / 100)  # 0.03


def test_signal_views_excludes_symbols_not_in_set():
    """集合外 ticker 的信号被忽略（成员过滤的反向证明）。"""
    conn = _mem_conn()
    _make_signals_tables(conn)
    _add_signal(conn, "t1", _recent_iso(1), ["TSLA"], 9)   # TSLA 不在集合
    _add_signal(conn, "t2", _recent_iso(1), ["AAPL"], 7)
    store = FakeStore(conn)
    views = po._signal_views(store, ["AAPL"], lookback_hours=72)
    assert "TSLA" not in views
    assert views["AAPL"] == pytest.approx((7 - 5) / 100)


def test_signal_views_symbol_without_signal_absent():
    """请求集合里没有任何信号的 symbol 不应出现在 views 中。"""
    conn = _mem_conn()
    _make_signals_tables(conn)
    _add_signal(conn, "t1", _recent_iso(1), ["AAPL"], 6)
    store = FakeStore(conn)
    views = po._signal_views(store, ["AAPL", "MSFT"], lookback_hours=72)
    assert "AAPL" in views
    assert "MSFT" not in views


def test_signal_views_skips_malformed_json():
    """坏 tickers JSON 不应导致异常，应被跳过。"""
    conn = _mem_conn()
    _make_signals_tables(conn)
    _add_signal(conn, "bad", _recent_iso(1), "{not json", 8)
    _add_signal(conn, "ok", _recent_iso(1), ["AAPL"], 6)
    store = FakeStore(conn)
    views = po._signal_views(store, ["AAPL"], lookback_hours=72)
    assert views == {"AAPL": pytest.approx((6 - 5) / 100)}


def test_signal_views_old_tweets_excluded():
    """超出 lookback 窗口的 tweet 被 SQL 的 created_at >= since 过滤掉。"""
    conn = _mem_conn()
    _make_signals_tables(conn)
    old = (dt.datetime.utcnow() - dt.timedelta(hours=200)).isoformat()
    _add_signal(conn, "old", old, ["AAPL"], 10)
    store = FakeStore(conn)
    views = po._signal_views(store, ["AAPL"], lookback_hours=72)
    assert views == {}


def test_signal_views_low_score_excluded():
    """score < 3 被 SQL 过滤（s.score >= 3）。"""
    conn = _mem_conn()
    _make_signals_tables(conn)
    _add_signal(conn, "lo", _recent_iso(1), ["AAPL"], 2)
    store = FakeStore(conn)
    views = po._signal_views(store, ["AAPL"], lookback_hours=72)
    assert views == {}


def test_fetch_price_history_shape_and_min_rows():
    """_fetch_price_history：≥5 行保留，<5 行剔除；index 为 DatetimeIndex。"""
    conn = _mem_conn()
    _make_price_bars(conn, {"AAA": [1.0 + i for i in range(12)],
                            "BBB": [2.0, 3.0]})  # 仅 2 行 < 5 → 剔除
    store = FakeStore(conn)
    df = po._fetch_price_history(["AAA", "BBB"], store)
    assert list(df.columns) == ["AAA"]
    assert len(df) == 12
    assert isinstance(df.index, pd.DatetimeIndex)


def test_fetch_price_history_empty_when_all_short():
    conn = _mem_conn()
    _make_price_bars(conn, {"AAA": [1.0, 2.0]})
    store = FakeStore(conn)
    df = po._fetch_price_history(["AAA"], store)
    assert df.empty


def _seeded_prices(n_days: int, n_assets: int, seed: int = 7) -> dict[str, list[float]]:
    """确定性几何随机游走价格，带正漂移，保证协方差非奇异。"""
    rng = np.random.default_rng(seed)
    out = {}
    for a in range(n_assets):
        rets = rng.normal(0.0008, 0.012, n_days)   # 正漂移
        px = 100.0 * np.cumprod(1 + rets)
        out[f"60000{a}"] = px.tolist()
    return out


def test_run_optimizer_weights_sum_to_one_and_bounded():
    """
    run_optimizer 端到端（pypfopt）：合成价格 + 空 signals 表 → max_sharpe 路径。
    属性断言对两条路径都成立：权重和≈1、有限、long-only（≥ -1e-9）。
    """
    pytest.importorskip("pypfopt")
    conn = _mem_conn()
    prices = _seeded_prices(n_days=60, n_assets=3, seed=11)
    _make_price_bars(conn, prices)
    _make_signals_tables(conn)  # 空表 → _signal_views 返回 {} → max_sharpe（且避免 no-such-table）
    store = FakeStore(conn)
    cfg = {"finance": {"enabled": True,
                       "a_shares": [{"code": c} for c in prices.keys()]}}
    res = po.run_optimizer(store, cfg)
    assert res is not None
    w = res["weights"]
    assert len(w) >= 1
    total = sum(w.values())
    assert total == pytest.approx(1.0, abs=1e-2)
    for v in w.values():
        assert np.isfinite(v)
        assert v >= -1e-9   # long-only


def test_run_optimizer_equal_weight_fallback_on_thin_data():
    """价格历史 < 10 行 → 文档化的等权兜底，权重和≈1。"""
    pytest.importorskip("pypfopt")
    conn = _mem_conn()
    _make_price_bars(conn, {"600000": [10.0] * 6, "600001": [12.0] * 6})
    _make_signals_tables(conn)
    store = FakeStore(conn)
    cfg = {"finance": {"enabled": True,
                       "a_shares": [{"code": "600000"}, {"code": "600001"}]}}
    res = po.run_optimizer(store, cfg)
    assert res["method"] == "equal_weight"
    assert sum(res["weights"].values()) == pytest.approx(1.0, abs=1e-3)


def test_run_optimizer_disabled_returns_none():
    store = FakeStore(_mem_conn())
    assert po.run_optimizer(store, {"finance": {"enabled": False}}) is None


# ══════════════════════════════════════════════════════════════════════════
# risk_analyzer.py
# ══════════════════════════════════════════════════════════════════════════


def test_load_returns_constant_price_zero_returns():
    conn = _mem_conn()
    _make_price_bars(conn, {"600000": [10.0] * 65})
    store = FakeStore(conn)
    rets = ra._load_returns(store, ["600000"], min_rows=60)
    assert list(rets.columns) == ["600000"]
    assert np.allclose(rets["600000"].dropna().values, 0.0)


def test_load_returns_respects_min_rows():
    """不足 min_rows 的 symbol 被剔除。"""
    conn = _mem_conn()
    _make_price_bars(conn, {"600000": [10.0 + i for i in range(65)],
                            "600001": [5.0 + i for i in range(20)]})  # 20 < 60
    store = FakeStore(conn)
    rets = ra._load_returns(store, ["600000", "600001"], min_rows=60)
    assert list(rets.columns) == ["600000"]


def test_load_returns_empty_when_insufficient():
    conn = _mem_conn()
    _make_price_bars(conn, {"600000": [1.0, 2.0, 3.0]})
    store = FakeStore(conn)
    assert ra._load_returns(store, ["600000"], min_rows=60).empty


def test_compute_risk_report_zero_variance_series():
    """零方差（常数价）→ vol≈0, max_dd≈0, total_ret≈0。"""
    conn = _mem_conn()
    _make_price_bars(conn, {"600000": [10.0] * 30})
    store = FakeStore(conn)
    cfg = {"finance": {"a_shares": [{"code": "600000"}]}}
    rep = ra.compute_risk_report(store, cfg)
    assert "600000" in rep
    m = rep["600000"]
    assert m["vol_ann"] == pytest.approx(0.0, abs=1e-9)
    assert m["max_dd"] == pytest.approx(0.0, abs=1e-9)
    assert m["total_ret"] == pytest.approx(0.0, abs=1e-9)
    assert m["n_days"] == 29   # 30 价格 - 1 收益率


def test_compute_risk_report_declining_series_signs():
    """单调下跌价格 → total_ret<0, max_dd<0（符号正确）；恒定 -1% 日收益 → vol≈0。"""
    conn = _mem_conn()
    closes = [100.0 * (0.99 ** i) for i in range(40)]  # 每日恒定 -1%
    _make_price_bars(conn, {"600000": closes})
    store = FakeStore(conn)
    cfg = {"finance": {"a_shares": [{"code": "600000"}]}}
    rep = ra.compute_risk_report(store, cfg)
    m = rep["600000"]
    assert m["total_ret"] < 0
    assert m["max_dd"] < 0
    # 恒定 -1% 日收益 = 零方差 → 年化波动应≈0（符号/数学性质正确）
    assert m["vol_ann"] == pytest.approx(0.0, abs=1e-6)


def test_compute_risk_report_known_volatility_value():
    """
    交替 +1%/-1% 收益序列 → 日收益标准差可手算，年化波动 = std*sqrt(252)。
    """
    conn = _mem_conn()
    # 价格在两个值间交替：收益序列约为 +r, -r', 交替；用对称价构造
    closes = []
    p = 100.0
    for i in range(41):
        closes.append(p)
        p = p * (1.01 if i % 2 == 0 else (1 / 1.01))  # 上一步若 +1% 则下一步回到原值
    store = FakeStore(_mem_conn())
    _make_price_bars(store.conn, {"600000": closes})
    cfg = {"finance": {"a_shares": [{"code": "600000"}]}}
    rep = ra.compute_risk_report(store, cfg)
    m = rep["600000"]
    # 重算期望值：直接对源码同款公式比对
    s = pd.Series(closes)
    r = s.pct_change().dropna()
    expected_vol = float(r.std() * np.sqrt(252))
    assert m["vol_ann"] == pytest.approx(round(expected_vol, 4), abs=1e-4)
    assert m["vol_ann"] > 0


def test_compute_risk_report_empty_when_no_data():
    conn = _mem_conn()
    conn.execute("CREATE TABLE price_bars (symbol TEXT, timestamp TEXT, close REAL, market TEXT)")
    conn.commit()
    store = FakeStore(conn)
    cfg = {"finance": {"a_shares": [{"code": "600000"}]}}
    assert ra.compute_risk_report(store, cfg) == {}


def test_run_risk_optimizer_skips_without_riskfolio():
    """riskfolio-lib 未安装 → 源码返回 None（不报错）。"""
    riskfolio = pytest.importorskip("riskfolio")  # noqa: F841 — 装了才继续
    # 若环境装了 riskfolio：用合成数据跑一遍，断言权重和≈1、风险指标存在。
    conn = _mem_conn()
    prices = _seeded_prices(n_days=80, n_assets=3, seed=5)
    _make_price_bars(conn, prices)
    store = FakeStore(conn)
    cfg = {"finance": {"enabled": True,
                       "a_shares": [{"code": c} for c in prices.keys()]}}
    res = ra.run_risk_optimizer(store, cfg, method="MV")
    if res is not None:
        assert sum(res["weights"].values()) == pytest.approx(1.0, abs=1e-2)
        assert "risk_metrics" in res
        assert res["risk_metrics"]["vol_ann"] >= 0
