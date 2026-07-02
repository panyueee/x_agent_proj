# -*- coding: utf-8 -*-
"""批次一新表（securities/portfolios/positions/risk_snapshots）的存取与快照语义。"""
import numpy as np
import pandas as pd
import pytest

from x_agent.storage import Store


@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path / "t.db"))


def test_securities_roundtrip(store):
    n = store.upsert_securities([
        {"symbol": "sh.600519", "market": "a", "name": "贵州茅台",
         "sector_gics": "Consumer Staples", "aliases": ["600519", "贵州茅台"],
         "has_parquet": 1},
        {"symbol": "BTC-USD", "market": "crypto", "aliases": ["$BTC"]},
    ])
    assert n == 2
    sec = store.get_security("sh.600519")
    assert sec["name"] == "贵州茅台"
    assert sec["sector_gics"] == "Consumer Staples"
    assert sec["aliases"] == ["600519", "贵州茅台"]
    assert store.get_security("BTC-USD")["market"] == "crypto"
    assert store.get_security("nope") is None

    # 幂等重跑：REPLACE 不翻倍
    store.upsert_securities([{"symbol": "BTC-USD", "market": "crypto",
                              "name": "Bitcoin"}])
    rows = store.conn.execute("SELECT COUNT(*) FROM securities").fetchone()[0]
    assert rows == 2
    assert store.get_security("BTC-USD")["name"] == "Bitcoin"


def test_positions_snapshot_ledger(store):
    """不可变账本：新快照不覆盖旧快照；latest_positions 取最近 date（支持 asof）。"""
    store.upsert_portfolio("demo", "示例组合")
    store.save_positions_snapshot("demo", "2026-06-01", [
        {"symbol": "sh.600519", "weight": 0.6},
        {"symbol": "NVDA", "weight": 0.4},
    ])
    store.save_positions_snapshot("demo", "2026-06-20", [
        {"symbol": "sh.600519", "weight": 0.5},
        {"symbol": "NVDA", "weight": 0.3},
        {"symbol": "BTC-USD", "weight": 0.2},
    ])

    latest = store.latest_positions("demo")
    assert {r["symbol"] for r in latest} == {"sh.600519", "NVDA", "BTC-USD"}
    assert all(r["date"] == "2026-06-20" for r in latest)

    older = store.latest_positions("demo", asof="2026-06-10")
    assert {r["symbol"] for r in older} == {"sh.600519", "NVDA"}
    assert store.latest_positions("demo", asof="2026-05-01") == []
    assert store.latest_positions("ghost") == []


def test_load_positions_normalizes_weights(store, monkeypatch):
    """load_positions：市场判定走 securities 主表，权重归一化到 Σ=1。"""
    from x_agent.risk.portfolio import load_positions

    store.upsert_securities([
        {"symbol": "sh.600519", "market": "a"},
        {"symbol": "NVDA", "market": "us"},
    ])
    store.upsert_portfolio("p1")
    store.save_positions_snapshot("p1", "2026-06-30", [
        {"symbol": "sh.600519", "weight": 0.3},
        {"symbol": "NVDA", "weight": 0.1},
    ])
    df = load_positions(store, "p1", data_dir="/nonexistent")
    assert df["weight"].sum() == pytest.approx(1.0)
    assert df.set_index("symbol").loc["sh.600519", "weight"] == pytest.approx(0.75)

    with pytest.raises(ValueError):
        load_positions(store, "empty_portfolio")


def test_risk_snapshot_roundtrip(store):
    store.save_risk_snapshot({
        "portfolio_id": "demo", "date": "2026-06-30",
        "vol_ann": 0.234, "var99_1d": 0.0343, "te_ann": None,
        "factor_vol": 0.20, "specific_vol": 0.12,
        "exposures": {"mkt": 0.92, "size": -0.1},
        "risk_contrib": {"mkt": 0.7, "size": 0.05},
        "stock_contrib": [{"symbol": "sh.600519", "name": "贵州茅台", "pct": 0.4}],
    })
    snap = store.latest_risk_snapshot("demo")
    assert snap["vol_ann"] == pytest.approx(0.234)
    assert snap["exposures"]["mkt"] == pytest.approx(0.92)
    assert snap["stock_contrib"][0]["symbol"] == "sh.600519"
    assert snap["method"] == "ewma_factor_v1"
    assert snap["te_ann"] is None

    # 同键覆盖写 + 跨组合取最新
    store.save_risk_snapshot({"portfolio_id": "demo", "date": "2026-06-30",
                              "vol_ann": 0.25})
    assert store.latest_risk_snapshot("demo")["vol_ann"] == pytest.approx(0.25)
    assert store.latest_risk_snapshot()["portfolio_id"] == "demo"
    assert store.latest_risk_snapshot("ghost") is None


def test_digest_risk_section(store):
    """digest 的 _risk_section：无快照返回 []，有快照输出关键数字。"""
    from x_agent.digest import _risk_section

    assert _risk_section(store) == []
    store.save_risk_snapshot({
        "portfolio_id": "demo", "date": "2026-06-30",
        "vol_ann": 0.234, "var99_1d": 0.0343, "te_ann": 0.05,
        "factor_vol": 0.20, "specific_vol": 0.12,
        "exposures": {"mkt": 0.92},
        "risk_contrib": {"mkt": 0.7},
        "stock_contrib": [{"symbol": "sh.600519", "name": "贵州茅台", "pct": 0.4}],
    })
    lines = _risk_section(store)
    text = "\n".join(lines)
    assert "组合风险" in text and "23.40%" in text and "贵州茅台" in text
