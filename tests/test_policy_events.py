"""
x_agent/policy_events.py（+ x_agent/storage.py 的 Store.save_policy_event /
query_policy_events）的 pytest 测试套件。

覆盖高价值行为：
  1. schema：裸 sqlite3 连接 + ensure_schema 建出 policy_events 表且列齐全；
     Store(tmp.db) 同样建出 policy_events，且不破坏 tweets/signals 等既有表。
  2. 幂等：同一 PolicyEvent upsert_event 两次 → 第一次 True / 第二次 False，
     表里只 1 行；数值修订（只改 params，身份字段不变）覆盖同一行，且新值
     确实落库（不是无操作）。
  3. announce_date / effective_date 分离存取。
  4. validate()：非法 region/category/direction/货币 action 各自命中问题列表；
     strict=True 的 upsert 对非法事件抛 ValueError。
  5/6. 匹配层（x_agent/persona/policy_score.py）防泄漏 + 预测/建议分离 ——
     该模块由另一个 agent 并行开发，此刻可能尚未存在。用
     pytest.importorskip 放在**测试函数体内部**（绝不放在文件顶层），
     模块缺失/接口不符时优雅 skip，不炸整个测试文件。

所有测试都用 pytest tmp_path 下的临时数据库，绝不触碰真实
output/x_agent.db，也不写 0 字节的根目录 x_agent.db。

运行：
    .venv/bin/python -m pytest tests/test_policy_events.py -v
"""
from __future__ import annotations

import os
import sqlite3
import sys

# 让 `import x_agent.*` 在任意 cwd 下都能工作
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest

from x_agent import policy_events as pe
from x_agent.storage import Store


# ── 辅助 ──────────────────────────────────────────────────────────────────────

def _db_path(tmp_path) -> str:
    return str(tmp_path / "test_policy.db")


def _rrr_event(**overrides) -> pe.PolicyEvent:
    """一条合法的央行降准事件，公布日与生效日故意分开。"""
    kw = dict(
        region="CN",
        category="货币",
        event_type="discretionary",
        issuer="PBOC",
        action="RRR",
        direction="cut",
        announce_date="2026-05-10",
        effective_date="2026-05-15",
        title="央行下调存款准备金率0.5个百分点",
        params={"delta_pp": -0.5},
        source_url="https://pbc.gov.cn/xxx",
        source_tier="official_primary",
        verification_status="verified",
    )
    kw.update(overrides)
    return pe.PolicyEvent(**kw)


# ── 1. schema ─────────────────────────────────────────────────────────────────

def test_ensure_schema_creates_table_with_columns(tmp_path):
    """裸连接 + ensure_schema：policy_events 表存在，且列与冻结契约一致。"""
    path = _db_path(tmp_path)
    conn = pe.connect_write(path)
    pe.ensure_schema(conn)
    names = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "policy_events" in names
    cols = {r[1] for r in conn.execute("PRAGMA table_info(policy_events)").fetchall()}
    for col in pe._COLUMNS:
        assert col in cols, f"policy_events 缺列 {col}"
    conn.close()


def test_ensure_schema_idempotent(tmp_path):
    """重复 ensure_schema 不应报错（IF NOT EXISTS）。"""
    path = _db_path(tmp_path)
    conn = pe.connect_write(path)
    pe.ensure_schema(conn)
    pe.ensure_schema(conn)  # 不应抛异常
    conn.close()


def test_store_creates_policy_events_without_breaking_existing_tables(tmp_path):
    """Store(tmp.db) 应同时建出 policy_events 与既有表（tweets/signals/...）。"""
    store = Store(_db_path(tmp_path))
    names = {
        r[0] for r in store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "policy_events" in names
    for tbl in ["tweets", "signals", "price_bars", "companies"]:
        assert tbl in names, f"Store 加了 policy_events 后不应丢失既有表 {tbl}"

    # Store 上的写路径也应可用
    ev = _rrr_event()
    assert store.save_policy_event(ev) is True
    rows = store.query_policy_events()
    assert len(rows) == 1
    assert rows[0]["action"] == "RRR"


# ── 2. 幂等 upsert ────────────────────────────────────────────────────────────

def test_upsert_event_idempotent_returns_true_then_false(tmp_path):
    conn = pe.connect_write(_db_path(tmp_path))
    pe.ensure_schema(conn)
    ev = _rrr_event()
    assert pe.upsert_event(conn, ev) is True   # 第一次：新插入
    assert pe.upsert_event(conn, ev) is False  # 第二次：覆盖已有
    cnt = conn.execute("SELECT COUNT(*) FROM policy_events").fetchone()[0]
    assert cnt == 1
    conn.close()


def test_upsert_event_numeric_revision_overwrites_same_row(tmp_path):
    """身份字段（region/category/action/issuer/announce/effective）不变，
    只改 params → event_id 相同 → 覆盖同一行，且新数值确实落库。"""
    conn = pe.connect_write(_db_path(tmp_path))
    pe.ensure_schema(conn)
    ev1 = _rrr_event(params={"delta_pp": -0.5})
    ev2 = _rrr_event(params={"delta_pp": -0.25})  # 数值修订（如从预告改为正式值）

    assert ev1.event_id() == ev2.event_id(), "身份哈希不应含数值"
    assert pe.upsert_event(conn, ev1) is True
    assert pe.upsert_event(conn, ev2) is False

    cnt = conn.execute("SELECT COUNT(*) FROM policy_events").fetchone()[0]
    assert cnt == 1, "数值修订应覆盖同一行，而非新增一行"

    rows = pe.query_events(conn)
    assert len(rows) == 1
    assert rows[0]["params"]["delta_pp"] == -0.25, "覆盖后应读到新数值，而非旧值残留"
    conn.close()


def test_upsert_event_different_identity_creates_new_row(tmp_path):
    """身份字段（如 announce_date）不同 → 不同 event_id → 各自一行。"""
    conn = pe.connect_write(_db_path(tmp_path))
    pe.ensure_schema(conn)
    ev1 = _rrr_event(announce_date="2026-05-10", effective_date="2026-05-15")
    ev2 = _rrr_event(announce_date="2026-08-10", effective_date="2026-08-15")
    assert ev1.event_id() != ev2.event_id()
    assert pe.upsert_event(conn, ev1) is True
    assert pe.upsert_event(conn, ev2) is True
    cnt = conn.execute("SELECT COUNT(*) FROM policy_events").fetchone()[0]
    assert cnt == 2
    conn.close()


# ── 3. announce/effective 分离 ────────────────────────────────────────────────

def test_announce_and_effective_dates_stored_separately(tmp_path):
    conn = pe.connect_write(_db_path(tmp_path))
    pe.ensure_schema(conn)
    ev = _rrr_event(announce_date="2026-05-10", effective_date="2026-05-15")
    pe.upsert_event(conn, ev)
    rows = pe.query_events(conn)
    assert len(rows) == 1
    assert rows[0]["announce_date"] == "2026-05-10"
    assert rows[0]["effective_date"] == "2026-05-15"
    assert rows[0]["announce_date"] != rows[0]["effective_date"]
    conn.close()


def test_query_events_after_filters_by_announce_date_strictly_greater(tmp_path):
    """query_events(after=T) 应严格 > T（防泄漏边界），不含 T 当天。"""
    conn = pe.connect_write(_db_path(tmp_path))
    pe.ensure_schema(conn)
    before = _rrr_event(announce_date="2026-01-01", effective_date="2026-01-05")
    on_t = _rrr_event(announce_date="2026-05-10", effective_date="2026-05-12")
    after = _rrr_event(announce_date="2026-09-01", effective_date="2026-09-05")
    for ev in (before, on_t, after):
        pe.upsert_event(conn, ev)

    rows = pe.query_events(conn, after="2026-05-10")
    announce_dates = {r["announce_date"] for r in rows}
    assert "2026-01-01" not in announce_dates
    assert "2026-05-10" not in announce_dates, "after 应严格大于，不含边界当天"
    assert "2026-09-01" in announce_dates
    conn.close()


# ── 4. validate ───────────────────────────────────────────────────────────────

def test_validate_rejects_illegal_region():
    ev = _rrr_event(region="XX")
    problems = ev.validate()
    assert any("region" in p for p in problems)


def test_validate_rejects_illegal_category():
    ev = _rrr_event(category="不存在的类别")
    problems = ev.validate()
    assert any("category" in p for p in problems)


def test_validate_rejects_illegal_direction():
    ev = _rrr_event(direction="sideways")
    problems = ev.validate()
    assert any("direction" in p for p in problems)


def test_validate_rejects_illegal_monetary_action():
    """货币类 action 必须在 MONETARY_ACTIONS 受控码表内。"""
    ev = _rrr_event(action="MADE_UP_ACTION")
    problems = ev.validate()
    assert any("action" in p for p in problems)


def test_validate_passes_for_legal_event():
    ev = _rrr_event()
    assert ev.validate() == []


def test_strict_upsert_raises_on_invalid_event(tmp_path):
    conn = pe.connect_write(_db_path(tmp_path))
    pe.ensure_schema(conn)
    bad = _rrr_event(region="XX")
    with pytest.raises(ValueError):
        pe.upsert_event(conn, bad, strict=True)
    # 校验失败不应留下脏数据
    cnt = conn.execute("SELECT COUNT(*) FROM policy_events").fetchone()[0]
    assert cnt == 0
    conn.close()


def test_non_strict_upsert_allows_invalid_event(tmp_path):
    """strict=False 时允许写入非法事件（供人工/半结构化补录场景）。"""
    conn = pe.connect_write(_db_path(tmp_path))
    pe.ensure_schema(conn)
    bad = _rrr_event(region="XX")
    assert pe.upsert_event(conn, bad, strict=False) is True
    cnt = conn.execute("SELECT COUNT(*) FROM policy_events").fetchone()[0]
    assert cnt == 1
    conn.close()


# ── 5/6. 匹配层（x_agent/persona/policy_score.py，另一 agent 并行开发）────────
#
# 该模块由另一 agent 并行编写；本文件写作之初它尚不存在，故所有依赖它的测试
# 都用 pytest.importorskip 放在**函数体内部**（绝不放文件顶层），模块缺失/
# import 失败时优雅 skip，不影响本文件其余测试。
#
# 实测确认的真实接口（非猜测）：
#   - match_prediction(pred: Prediction, events: list[dict], asof: str) -> dict
#       候选事件池由调用方传入（自身仍会做完整防泄漏过滤），返回 dict 含
#       outcome ∈ {"hit","miss","unresolved","skipped"}，以及
#       lag_days / value_error / matched_event / reason。
#   - score_predictions(preds: list[Prediction], conn, asof=None) -> dict
#       返回 n_total/n_scorable/n_hit/n_miss/n_unresolved/
#       n_recommendation_skipped/strict_hit_rate/details。
#       strict_hit_rate 分母 = n_hit + n_miss（unresolved/skipped 不进分母）。

def _policy_score():
    return pytest.importorskip(
        "x_agent.persona.policy_score",
        reason="policy_score.py 由另一 agent 并行开发，import 失败时跳过",
    )


def test_policy_score_only_matches_events_after_pred_date(tmp_path):
    """预测 pred_date=T，库里有 T 之前和 T 之后同类 cut 事件 → 只应匹配 T 之后那条。"""
    ps = _policy_score()

    conn = pe.connect_write(_db_path(tmp_path))
    pe.ensure_schema(conn)
    before_event = _rrr_event(
        announce_date="2026-01-05", effective_date="2026-01-10",
        params={"delta_pp": -0.5},
    )
    after_event = _rrr_event(
        announce_date="2026-06-20", effective_date="2026-06-25",
        params={"delta_pp": -0.25},
    )
    pe.upsert_event(conn, before_event)
    pe.upsert_event(conn, after_event)

    pred = pe.Prediction(
        person="tester", pred_date="2026-03-01",
        region="CN", category="货币", action="RRR", direction="cut",
        kind="prediction", horizon_days=180,
    )

    # 故意喂"脏"的全量事件池（不预先按 after 过滤），验证防泄漏是
    # match_prediction 自身的职责，而不是依赖调用方先过滤。
    all_events = pe.query_events(conn)
    outcome = ps.match_prediction(pred, all_events, asof="2026-12-31")
    assert outcome["outcome"] == "hit"
    assert outcome["matched_event"]["announce_date"] == "2026-06-20"
    assert outcome["matched_event"]["announce_date"] != before_event.announce_date
    conn.close()


def test_policy_score_opposite_direction_is_miss(tmp_path):
    """事件在预测之后发生，但方向相反 → 应判 miss，不应判 hit。"""
    ps = _policy_score()

    conn = pe.connect_write(_db_path(tmp_path))
    pe.ensure_schema(conn)
    hike_event = _rrr_event(
        direction="hike", announce_date="2026-06-20", effective_date="2026-06-25",
        params={"delta_pp": 0.5},
    )
    pe.upsert_event(conn, hike_event)

    pred = pe.Prediction(
        person="tester", pred_date="2026-03-01",
        region="CN", category="货币", action="RRR", direction="cut",
        kind="prediction", horizon_days=180,
    )
    events = pe.query_events(conn)
    outcome = ps.match_prediction(pred, events, asof="2026-12-31")
    assert outcome["outcome"] == "miss"
    conn.close()


def test_policy_score_no_event_in_window_is_unresolved_not_in_denominator(tmp_path):
    """窗口/快照内无相关事件 → unresolved，不应计入 strict_hit_rate 的分母。"""
    ps = _policy_score()

    conn = pe.connect_write(_db_path(tmp_path))
    pe.ensure_schema(conn)  # 空库：无任何事件

    pred = pe.Prediction(
        person="tester", pred_date="2026-03-01",
        region="CN", category="货币", action="RRR", direction="cut",
        kind="prediction", horizon_days=30,
    )
    summary = ps.score_predictions([pred], conn, asof="2026-12-31")
    assert summary["n_unresolved"] == 1
    assert summary["n_hit"] == 0
    assert summary["n_miss"] == 0
    # strict_hit_rate 分母只含 hit+miss；此处两者皆 0 → 分母为 0 → None
    assert summary["strict_hit_rate"] is None
    conn.close()


def test_policy_score_strict_hit_rate_denominator_excludes_unresolved(tmp_path):
    """混合 hit/miss/unresolved 时，strict_hit_rate 的分母应只含 hit+miss。"""
    ps = _policy_score()

    conn = pe.connect_write(_db_path(tmp_path))
    pe.ensure_schema(conn)
    pe.upsert_event(conn, _rrr_event(
        direction="cut", announce_date="2026-06-20", effective_date="2026-06-25",
    ))
    pe.upsert_event(conn, _rrr_event(
        action="LPR_1Y", direction="hold",
        announce_date="2026-06-21", effective_date="2026-06-21",
    ))

    hit_pred = pe.Prediction(
        person="tester", pred_date="2026-03-01", region="CN", category="货币",
        action="RRR", direction="cut", kind="prediction", horizon_days=180,
    )
    miss_pred = pe.Prediction(
        person="tester", pred_date="2026-03-01", region="CN", category="货币",
        action="LPR_1Y", direction="cut", kind="prediction", horizon_days=180,
    )
    unresolved_pred = pe.Prediction(
        person="tester", pred_date="2026-03-01", region="CN", category="货币",
        action="MLF_RATE", direction="cut", kind="prediction", horizon_days=180,
    )
    summary = ps.score_predictions(
        [hit_pred, miss_pred, unresolved_pred], conn, asof="2026-12-31",
    )
    assert summary["n_hit"] == 1
    assert summary["n_miss"] == 1
    assert summary["n_unresolved"] == 1
    assert summary["strict_hit_rate"] == pytest.approx(0.5)  # 1/(1+1)，不含 unresolved
    conn.close()


def test_policy_score_recommendation_kind_is_skipped(tmp_path):
    """kind='recommendation' 的预测（'应该这样'）应被跳过，不进分母、不算命中。"""
    ps = _policy_score()

    conn = pe.connect_write(_db_path(tmp_path))
    pe.ensure_schema(conn)
    ev = _rrr_event(announce_date="2026-06-20", effective_date="2026-06-25")
    pe.upsert_event(conn, ev)

    rec = pe.Prediction(
        person="tester", pred_date="2026-03-01",
        region="CN", category="货币", action="RRR", direction="cut",
        kind="recommendation",
    )
    assert rec.is_scorable() is False  # 契约层面已可验证，不依赖 policy_score

    summary = ps.score_predictions([rec], conn, asof="2026-12-31")
    assert summary["n_recommendation_skipped"] == 1
    assert summary["n_hit"] == 0
    assert summary["n_miss"] == 0
    assert summary["n_scorable"] == 0
    assert summary["strict_hit_rate"] is None


def test_prediction_is_scorable_distinguishes_kind():
    """契约层：Prediction.is_scorable() 本身不依赖 policy_score，直接测。"""
    pred = pe.Prediction(
        person="p", pred_date="2026-01-01", region="CN", category="货币",
        action="RRR", direction="cut", kind="prediction",
    )
    rec = pe.Prediction(
        person="p", pred_date="2026-01-01", region="CN", category="货币",
        action="RRR", direction="cut", kind="recommendation",
    )
    assert pred.is_scorable() is True
    assert rec.is_scorable() is False


# ── 独立运行入口（无 pytest 时，跳过 5/6 段依赖 policy_score 的用例） ──────────

def _run_standalone() -> int:
    import tempfile
    import pathlib

    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = failed = skipped = 0
    for fn in fns:
        needs_tmp = "tmp_path" in fn.__code__.co_varnames[: fn.__code__.co_argcount]
        with tempfile.TemporaryDirectory() as d:
            try:
                if needs_tmp:
                    fn(pathlib.Path(d))
                else:
                    fn()
                print(f"PASS  {fn.__name__}")
                passed += 1
            except pytest.skip.Exception as e:
                print(f"SKIP  {fn.__name__}: {e}")
                skipped += 1
            except AssertionError as e:
                print(f"FAIL  {fn.__name__}: {e}")
                failed += 1
            except Exception as e:  # noqa: BLE001
                print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
                failed += 1
    print(f"\n{passed} passed, {skipped} skipped, {failed} failed (total {passed + skipped + failed})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_standalone())
