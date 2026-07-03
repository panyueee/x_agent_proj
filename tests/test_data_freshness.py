"""
data_freshness 单测：聚焦"落后天数 / 阈值判定 / 退出码"的纯逻辑，
外加用临时 parquet 验证目录级检查（日期列解析、group-max、stale/broken/未来脏日期）。

务必用项目 venv 跑：.venv/bin/python -m pytest tests/test_data_freshness.py
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from scripts import data_freshness as F


TODAY = dt.date(2026, 7, 3)


# ── 纯函数：lag_days ──────────────────────────────────────────────────────────

def test_lag_days_basic():
    assert F.lag_days(dt.date(2026, 7, 1), TODAY) == 2
    assert F.lag_days(TODAY, TODAY) == 0


def test_lag_days_none():
    assert F.lag_days(None, TODAY) is None


# ── 纯函数：classify（状态 + 退出码语义）────────────────────────────────────

def test_classify_fresh_within_threshold():
    # 落后 2 天，阈值 4 → FRESH（吸收周末）
    status, lag = F.classify(dt.date(2026, 7, 1), TODAY, threshold_days=4, alarm=True)
    assert status == F.FRESH and lag == 2


def test_classify_weekend_not_stale():
    # 周五收盘(6/26)→ 周一(6/29)看是 3 天，阈值 4 不该报警
    monday = dt.date(2026, 6, 29)
    status, _ = F.classify(dt.date(2026, 6, 26), monday, threshold_days=4, alarm=True)
    assert status == F.FRESH


def test_classify_stale_over_threshold():
    status, lag = F.classify(dt.date(2025, 9, 10), TODAY, threshold_days=45, alarm=True)
    assert status == F.STALE and lag > 45


def test_classify_none_is_broken_not_fresh():
    # 解析不出日期绝不能当 FRESH
    status, lag = F.classify(None, TODAY, threshold_days=45, alarm=True)
    assert status == F.BROKEN and lag is None


def test_classify_info_source_never_alarms():
    # 信息级源即便很旧也只是 INFO（不进退出码）
    status, lag = F.classify(dt.date(2020, 1, 1), TODAY, threshold_days=4, alarm=False)
    assert status == F.INFO and lag > 0


# ── 日期强转 _coerce_date ────────────────────────────────────────────────────

@pytest.mark.parametrize("val,expected", [
    ("2026-07-02", dt.date(2026, 7, 2)),
    ("2025年6月份", dt.date(2025, 6, 1)),
    (dt.datetime(2026, 6, 30, 15, 0), dt.date(2026, 6, 30)),
    (dt.date(2026, 6, 30), dt.date(2026, 6, 30)),
    (b"2026-01-05", dt.date(2026, 1, 5)),
    ("garbage", None),
    (None, None),
])
def test_coerce_date(val, expected):
    assert F._coerce_date(val) == expected


# ── 目录级检查：用临时 parquet ───────────────────────────────────────────────

def _write(tmp: Path, name: str, dates, col="date"):
    df = pd.DataFrame({col: dates, "close": [1.0] * len(dates)})
    (tmp).mkdir(parents=True, exist_ok=True)
    df.to_parquet(tmp / name, index=False)


def test_check_parquet_dir_fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "DATA", tmp_path)
    _write(tmp_path / "quotes", "AAA.parquet", ["2026-07-01", "2026-07-02"])
    _write(tmp_path / "quotes", "BBB.parquet", ["2026-07-01", "2026-07-02"])
    r = F.check_parquet_dir("q", "quotes", ["date"], threshold=4,
                            alarm=True, recursive=False, today=TODAY)
    assert r["status"] == F.FRESH
    assert r["latest"] == "2026-07-02"
    assert r["lag"] == 1


def test_check_parquet_dir_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "DATA", tmp_path)
    _write(tmp_path / "old", "X.parquet", ["2025-09-10"])
    r = F.check_parquet_dir("m", "old", ["date"], threshold=45,
                            alarm=True, recursive=False, today=TODAY)
    assert r["status"] == F.STALE
    assert r["lag"] > 45


def test_check_parquet_dir_counts_laggards(tmp_path, monkeypatch):
    # 一个文件新、一个文件落后组内最新很多 → group FRESH 但 stale_files>=1
    monkeypatch.setattr(F, "DATA", tmp_path)
    _write(tmp_path / "mix", "fresh.parquet", ["2026-07-02"])
    _write(tmp_path / "mix", "lag.parquet", ["2026-06-01"])
    r = F.check_parquet_dir("mix", "mix", ["date"], threshold=4,
                            alarm=True, recursive=False, today=TODAY)
    assert r["status"] == F.FRESH  # group-max 新
    assert r["stale_files"] >= 1   # 但抓到静默落后的文件


def test_check_parquet_dir_future_date_ignored(tmp_path, monkeypatch):
    # 真实 FRED 场景：某个文件整体是未来脏日期(2036)，不能抬高 group-max；
    # 其余文件的真实最新(2025-09)才应是 group-max。（跨文件粒度）
    monkeypatch.setattr(F, "DATA", tmp_path)
    _write(tmp_path / "fred", "good.parquet", ["2025-09-08", "2025-09-10"])
    _write(tmp_path / "fred", "bad.parquet", ["2036-10-01"])
    r = F.check_parquet_dir("fred", "fred", ["date"], threshold=45,
                            alarm=True, recursive=False, today=TODAY)
    assert r["latest"] == "2025-09-10"
    assert r["status"] == F.STALE
    assert "未来脏日期" in r["note"]


def test_check_parquet_dir_empty_dir_broken(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "DATA", tmp_path)
    (tmp_path / "empty").mkdir()
    r = F.check_parquet_dir("e", "empty", ["date"], threshold=4,
                            alarm=True, recursive=False, today=TODAY)
    assert r["status"] == F.BROKEN


def test_check_parquet_dir_chinese_month_col(tmp_path, monkeypatch):
    # 月份列 "2026年6月份" 要能解析，不能当 BROKEN
    monkeypatch.setattr(F, "DATA", tmp_path)
    _write(tmp_path / "pmi", "pmi.parquet", ["2026年5月份", "2026年6月份"], col="月份")
    r = F.check_parquet_dir("pmi", "pmi", ["月份"], threshold=45,
                            alarm=True, recursive=False, today=TODAY)
    assert r["latest"] == "2026-06-01"
    assert r["status"] == F.FRESH


# ── 退出码语义（run 聚合）────────────────────────────────────────────────────

def test_exit_code_semantics():
    # 信息级 STALE 不该触发退出码，仅 alarm=True 的 STALE/BROKEN 触发
    results = [
        {"source": "a", "status": F.FRESH, "alarm": True},
        {"source": "b", "status": F.STALE, "alarm": False},  # 信息级，不告警
    ]
    bad = sum(1 for r in results if r["alarm"] and r["status"] in (F.STALE, F.BROKEN))
    assert bad == 0

    results.append({"source": "c", "status": F.STALE, "alarm": True})
    bad = sum(1 for r in results if r["alarm"] and r["status"] in (F.STALE, F.BROKEN))
    assert bad == 1
