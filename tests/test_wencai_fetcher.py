"""
wencai_fetcher.py + storage.wencai_picks + digest 问财板块 的 pytest 测试套件。

测试范围（pywencai 全部 mock，绝不真实联网 / 调 node）：
  - run_queries：禁用开关（enabled: false → 空列表且不 import pywencai）
  - run_queries：mock pywencai.get 返回 DataFrame → 标准化字段正确
    （代码拆分、价格转 float、指标列去日期后缀、max_per_query 截断、label 透传）
  - run_queries：pywencai 返回 None（无结果）/ 抛异常时不炸，其余查询继续
  - Store.save_wencai_pick：同日同查询同股票只存一次（去重），不同日/查询/股票分开存
  - digest._wencai_section：有数据时渲染"问财选股"板块、只展示最新一天；无数据时返回 []

所有测试使用 tmp_path 下的临时数据库，绝不触碰真实 output/*.db。

可用 pytest 运行：
    python -m pytest tests/test_wencai_fetcher.py -v
"""
from __future__ import annotations

import os
import sys
import types
from unittest import mock

import pandas as pd

# 让 `import x_agent.wencai_fetcher` 在任意 cwd 下都能工作
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from x_agent import wencai_fetcher as wf
from x_agent.storage import Store
from x_agent.digest import _wencai_section


# ── 辅助 ──────────────────────────────────────────────────────────────────────

def _sample_df() -> pd.DataFrame:
    """模拟问财真实返回的 DataFrame（列名带日期后缀，数值多为字符串）。"""
    return pd.DataFrame([
        {
            "股票代码": "600115.SH", "股票简称": "中国东航",
            "最新价": "3.86", "最新涨跌幅": "-1.78",
            "市盈率(pe)[20260702]": "13.053",
            "总市值[20260702]": "7.96e10",
            "market_code": "17", "code": "600115",
        },
        {
            "股票代码": "600029.SH", "股票简称": "南方航空",
            "最新价": "5.25", "最新涨跌幅": "-1.87",
            "市盈率(pe)[20260702]": "16.059",
            "总市值[20260702]": "8.43e10",
            "market_code": "17", "code": "600029",
        },
        {
            "股票代码": "601628.SH", "股票简称": "中国人寿",
            "最新价": "38.8", "最新涨跌幅": "0.05",
            "市盈率(pe)[20260702]": "14.060",
            "总市值[20260702]": None,
            "market_code": "17", "code": "601628",
        },
    ])


def _cfg(queries, enabled=True, max_per_query=20):
    return {
        "wencai": {
            "enabled": enabled,
            "max_per_query": max_per_query,
            "sleep_sec": 0,   # 测试不真实 sleep
            "queries": queries,
        }
    }


def _fake_pywencai(get_fn):
    """构造带 get 方法的假 pywencai 模块。"""
    m = types.ModuleType("pywencai")
    m.get = get_fn
    return m


def _run(cfg, get_fn):
    """注入假 pywencai 后执行 run_queries。"""
    with mock.patch.dict(sys.modules, {"pywencai": _fake_pywencai(get_fn)}):
        return wf.run_queries(cfg)


def _pick(code="600519", query="q1", date="2026-07-02", **kw):
    rec = {
        "code": code, "full_code": f"{code}.SH", "name": "测试股",
        "price": 10.0, "change_pct": 1.5, "metrics": {"市盈率(pe)": "20"},
        "query": query, "label": "lab", "date": date,
        "fetched_at": f"{date}T08:00:00",
    }
    rec.update(kw)
    return rec


# ── run_queries：禁用开关 ─────────────────────────────────────────────────────

def test_disabled_returns_empty():
    calls = []
    out = _run(_cfg(["随便查"], enabled=False), lambda **kw: calls.append(kw))
    assert out == []
    assert calls == []   # 禁用时完全不触发查询


def test_missing_section_returns_empty():
    assert wf.run_queries({}) == []
    assert wf.run_queries(None) == []


# ── run_queries：标准化 ───────────────────────────────────────────────────────

def test_normalize_fields():
    out = _run(_cfg([{"query": "市盈率<20", "label": "cheap"}]),
               lambda **kw: _sample_df())
    assert len(out) == 3
    r = out[0]
    assert r["code"] == "600115"
    assert r["full_code"] == "600115.SH"
    assert r["name"] == "中国东航"
    assert r["price"] == 3.86           # 字符串 → float
    assert r["change_pct"] == -1.78
    assert r["query"] == "市盈率<20"
    assert r["label"] == "cheap"
    assert r["date"] and r["fetched_at"]
    # 指标列去掉日期后缀；核心列/market_code 不进 metrics；None 值被剔除
    assert r["metrics"]["市盈率(pe)"] == "13.053"
    assert "总市值" in r["metrics"]
    assert "market_code" not in r["metrics"]
    assert "总市值" not in out[2]["metrics"]   # 第三行总市值为 None → 剔除


def test_max_per_query_truncates():
    out = _run(_cfg(["查"], max_per_query=2), lambda **kw: _sample_df())
    assert len(out) == 2
    assert [r["code"] for r in out] == ["600115", "600029"]


def test_string_query_form():
    """queries 里写纯字符串也可以，label 默认空。"""
    out = _run(_cfg(["市盈率<20"]), lambda **kw: _sample_df())
    assert out[0]["query"] == "市盈率<20"
    assert out[0]["label"] == ""


def test_row_without_code_skipped():
    df = pd.DataFrame([{"股票代码": "", "股票简称": "无代码", "code": None}])
    assert _run(_cfg(["查"]), lambda **kw: df) == []


# ── run_queries：容错 ─────────────────────────────────────────────────────────

def test_none_result_and_exception_do_not_break_other_queries():
    def get(query="", **kw):
        if query == "q_none":
            return None            # 问财无结果
        if query == "q_boom":
            raise RuntimeError("网络挂了")
        return _sample_df()

    out = _run(_cfg(["q_none", "q_boom", "q_ok"]), get)
    # 前两条失败不影响第三条
    assert len(out) == 3
    assert all(r["query"] == "q_ok" for r in out)


# ── Store：wencai_picks 去重 ──────────────────────────────────────────────────

def test_save_wencai_pick_dedup(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    assert store.save_wencai_pick(_pick()) is True
    assert store.save_wencai_pick(_pick()) is False   # 同日同查询同股票 → 忽略
    rows = store.recent_wencai_picks()
    assert len(rows) == 1
    date, query, label, code, name, price, change_pct = rows[0]
    assert (date, query, label, code, name) == ("2026-07-02", "q1", "lab", "600519", "测试股")
    assert price == 10.0 and change_pct == 1.5


def test_save_wencai_pick_different_keys(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    assert store.save_wencai_pick(_pick()) is True
    assert store.save_wencai_pick(_pick(code="000001")) is True        # 不同股票
    assert store.save_wencai_pick(_pick(query="q2")) is True           # 不同查询
    assert store.save_wencai_pick(_pick(date="2026-07-03")) is True    # 不同日期
    assert len(store.recent_wencai_picks()) == 4


def test_store_reopen_idempotent(tmp_path):
    """同路径重复打开 Store（建表 IF NOT EXISTS）不丢数据。"""
    path = str(tmp_path / "t.db")
    Store(path).save_wencai_pick(_pick())
    assert len(Store(path).recent_wencai_picks()) == 1


# ── digest：问财板块 ──────────────────────────────────────────────────────────

def test_wencai_section_empty(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    assert _wencai_section(store) == []


def test_wencai_section_renders_latest_day_only(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    store.save_wencai_pick(_pick(date="2026-07-01", code="111111", name="旧股"))
    store.save_wencai_pick(_pick(date="2026-07-02", code="600519", name="新股A"))
    store.save_wencai_pick(_pick(date="2026-07-02", code="000858", name="新股B", query="q2"))
    lines = _wencai_section(store)
    text = "\n".join(lines)
    assert "问财选股" in text
    assert "2026-07-02" in text
    assert "新股A" in text and "新股B" in text
    assert "旧股" not in text          # 只展示最新一天
    assert "q1" in text and "q2" in text   # 按查询分组各有标题


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
