"""
digest.py 摘要构建逻辑回归测试。

覆盖：
  - _format_pct 涨跌幅格式化（正/零/负、小数位）
  - _market_section 行情板块（正常行、空、store 抛异常降级）
  - _portfolio_section 组合权重（排序、<0.1% 过滤、缺 views）
  - _psych_section 市场心理（温度计 bar、LLM 解读、历史趋势）
  - _tgb_section 淘股吧（博文/评论拆分）
  - _factor_section 因子收益率（表头、累计行）
  - _rag_section / _book_annotation（mock rag，含降级）
  - build_digest 端到端（喂假 store + 临时文件路径）

约束：不触碰真实 DB / output 目录，store 全部用内存假对象 / mock。

可用 pytest 运行：
    python -m pytest tests/test_digest.py -v
也可直接当脚本运行（无 pytest 依赖）：
    python tests/test_digest.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from unittest import mock

# 让 `import x_agent.digest` 在任意 cwd 下都能工作
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from x_agent import digest as dg


# ── 测试替身 ──────────────────────────────────────────────────────────────────

class _FakeCursor:
    """仿 sqlite3.Cursor：fetchall / fetchone / description。"""

    def __init__(self, rows, description=None):
        self._rows = list(rows)
        self.description = description

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    """仿 sqlite3.Connection：按 SQL 关键字派发预置游标。

    handlers: list[(substr, _FakeCursor)]，第一个 SQL 含 substr 的返回对应游标。
    未匹配的默认返回空游标。raise_on=True 时所有 execute 抛错（验证降级路径）。
    """

    def __init__(self, handlers=None, raise_on=False):
        self.handlers = handlers or []
        self.raise_on = raise_on
        self.queries = []

    def execute(self, sql, params=()):
        self.queries.append(sql)
        if self.raise_on:
            raise RuntimeError("db boom")
        for substr, cur in self.handlers:
            if substr in sql:
                return cur
        return _FakeCursor([])


class _FakeStore:
    """灵活假 store：用关键字参数注入各方法的返回值或异常。"""

    def __init__(self, **kw):
        self._kw = kw
        self.conn = kw.get("conn", _FakeConn())

    def _ret(self, name, default):
        if name not in self._kw:
            return default
        val = self._kw[name]
        if isinstance(val, Exception):
            raise val
        return val

    def recent_signals(self, categories, limit=80):
        return self._ret("recent_signals", [])

    def recent_price_bars(self, market, limit=50):
        return self._ret("price_bars_" + market, [])

    def latest_portfolio_weights(self):
        return self._ret("portfolio", {})

    def recent_panic_snapshots(self, limit=10):
        return self._ret("panic", [])


def _price_row(symbol, name, close, change_pct, ts="2026-06-30T12:00:00Z"):
    # (symbol, name, market, timestamp, open, high, low, close, volume, change_pct)
    return (symbol, name, "a_shares", ts, 0, 0, 0, close, 0, change_pct)


# ── 1. _format_pct ────────────────────────────────────────────────────────────

def test_format_pct_positive_gets_plus():
    assert dg._format_pct(1.5) == "+1.50%"
    assert dg._format_pct(12.345) == "+12.35%"   # 四舍五入到 2 位


def test_format_pct_zero_treated_as_positive():
    assert dg._format_pct(0) == "+0.00%"
    assert dg._format_pct(0.0) == "+0.00%"


def test_format_pct_negative_no_extra_sign():
    assert dg._format_pct(-3.2) == "-3.20%"
    assert dg._format_pct(-0.005) == "-0.01%" or dg._format_pct(-0.005) == "-0.00%"


# ── 2. _market_section ────────────────────────────────────────────────────────

def test_market_section_builds_table():
    store = _FakeStore(price_bars_a_shares=[
        _price_row("600519", "贵州茅台", 1725.5, 2.1),
        _price_row("000858", "五粮液", 151.2, -1.3),
    ])
    lines = dg._market_section(store, "a_shares", "A 股")
    text = "\n".join(lines)
    assert "### A 股" in text
    assert "| 代码 | 名称 | 最新价 | 涨跌幅 | 更新时间 |" in text
    assert "`600519`" in text and "贵州茅台" in text
    assert "+2.10%" in text and "-1.30%" in text
    # 时间被截到 16 字符并把 T 换成空格
    assert "2026-06-30 12:00" in text


def test_market_section_empty_rows_returns_empty_list():
    store = _FakeStore(price_bars_a_shares=[])
    assert dg._market_section(store, "a_shares", "A 股") == []


def test_market_section_store_exception_degrades():
    store = _FakeStore(price_bars_a_shares=RuntimeError("db down"))
    assert dg._market_section(store, "a_shares", "A 股") == []


def test_market_section_close_uses_4g_format():
    # close:.4g —— 1725.5 -> "1726"（4 位有效数字）
    store = _FakeStore(price_bars_a_shares=[_price_row("X", "测试", 1725.5, 0.0)])
    text = "\n".join(dg._market_section(store, "a_shares", "A 股"))
    assert "1726" in text


# ── 3. _portfolio_section ─────────────────────────────────────────────────────

def test_portfolio_section_sorts_and_formats():
    store = _FakeStore(portfolio={
        "method": "black_litterman",
        "computed_at": "2026-06-30T08:00:00Z",
        "weights": {"BTC": 0.6, "ETH": 0.3, "tiny": 0.0005},
        "views": {"BTC": 0.12},
    })
    lines = dg._portfolio_section(store)
    text = "\n".join(lines)
    assert "## 📊 组合权重建议" in text
    assert "Black-Litterman（信号加权）" in text
    # 权重降序：BTC 行应在 ETH 行之前
    assert text.index("`BTC`") < text.index("`ETH`")
    # 权重百分比格式
    assert "60.0%" in text and "30.0%" in text
    # views 存在的有符号百分比，缺失的为 —
    assert "+12.0%" in text
    assert "| — |" in text          # ETH 无 view
    # 权重 < 0.001 的 tiny 被过滤
    assert "tiny" not in text


def test_portfolio_section_empty_returns_empty():
    assert dg._portfolio_section(_FakeStore(portfolio={})) == []
    assert dg._portfolio_section(_FakeStore(portfolio={"weights": {}})) == []


def test_portfolio_section_exception_degrades():
    store = _FakeStore(portfolio=RuntimeError("nope"))
    assert dg._portfolio_section(store) == []


# ── 4. _psych_section ─────────────────────────────────────────────────────────

def _panic_snapshot(score=50, emotion="panic", signal="buy", **extra):
    base = {
        "panic_score": score,
        "dominant_emotion": emotion,
        "contrarian_signal": signal,
        "fear_count": 10,
        "greed_count": 3,
        "total_posts": 100,
        "computed_at": "2026-06-30T09:30:00Z",
        "llm_report": {},
    }
    base.update(extra)
    return base


def test_psych_section_basic_bar_and_emotion():
    store = _FakeStore(panic=[_panic_snapshot(score=50, emotion="panic", signal="buy")])
    text = "\n".join(dg._psych_section(store))
    assert "## 🧠 市场心理 / Panic Index" in text
    assert "50 / 100" in text
    # filled = int(50/5) = 10 个实心块，10 个空心块
    assert "█" * 10 + "░" * 10 in text
    assert "恐慌" in text and "逆向买入预警" in text
    assert "恐慌信号帖 **10** 条" in text
    # 无 LLM 报告时给出提示
    assert "LLM 解读未启用" in text


def test_psych_section_with_llm_report():
    llm = {
        "sentiment": "bullish",
        "confidence": "high",
        "market_phase": "筑底",
        "crowd_psychology": "群体恐慌见底",
        "key_drivers": ["利空出尽", "超跌"],
        "contrarian_rationale": "逆向买入",
        "short_term_outlook": "反弹",
        "risk_warning": "注意假突破",
    }
    store = _FakeStore(panic=[_panic_snapshot(llm_report=llm)])
    text = "\n".join(dg._psych_section(store))
    assert "LLM 解读未启用" not in text
    assert "筑底" in text and "看多" in text and "置信：高" in text
    assert "群体恐慌见底" in text
    assert "利空出尽、超跌" in text
    assert "反弹" in text and "注意假突破" in text


def test_psych_section_history_table_when_multiple():
    snaps = [_panic_snapshot(score=50), _panic_snapshot(score=30, emotion="greed", signal="sell")]
    text = "\n".join(dg._psych_section(_FakeStore(panic=snaps)))
    assert "历史趋势" in text
    assert "贪婪" in text and "卖出↓" in text


def test_psych_section_empty_and_exception():
    assert dg._psych_section(_FakeStore(panic=[])) == []
    assert dg._psych_section(_FakeStore(panic=RuntimeError("x"))) == []


# ── 5. _tgb_section ───────────────────────────────────────────────────────────

def test_tgb_section_splits_posts_and_replies():
    rows = [
        ("大V甲", "标题一\n正文内容", "http://t/1", "2026-06-30T10:00:00Z", "taoguba"),
        ("大V乙", "[评论]某帖\n这是评论", "http://t/2", "2026-06-30T11:00:00Z", "taoguba_reply"),
    ]
    conn = _FakeConn(handlers=[("FROM tweets", _FakeCursor(rows))])
    store = _FakeStore(conn=conn)
    text = "\n".join(dg._tgb_section(store))
    assert "## 📝 淘股吧动态" in text
    assert "博文（1 篇）" in text
    assert "评论/回复（1 条）" in text
    assert "标题一" in text
    assert "→ 某帖" in text          # [评论] 被替换为 →
    assert "http://t/1" in text


def test_tgb_section_empty_and_exception():
    store_empty = _FakeStore(conn=_FakeConn(handlers=[("FROM tweets", _FakeCursor([]))]))
    assert dg._tgb_section(store_empty) == []
    store_err = _FakeStore(conn=_FakeConn(raise_on=True))
    assert dg._tgb_section(store_err) == []


# ── 6. _factor_section ────────────────────────────────────────────────────────

def test_factor_section_table_and_cumulative():
    desc = [("date",), ("momentum",), ("value",)]
    rows = [
        ("2026-06-30", 0.012, -0.004),
        ("2026-06-29", 0.005, 0.001),
    ]
    avg_cur = _FakeCursor([(0.0085, -0.0015)])
    sel_cur = _FakeCursor(rows, description=desc)

    # _factor_section 先执行 SELECT * ... 再执行 SELECT AVG(...)
    handlers = [("AVG(", avg_cur), ("SELECT *", sel_cur)]
    conn = _FakeConn(handlers=handlers)
    store = _FakeStore(conn=conn)
    text = "\n".join(dg._factor_section(store))
    assert "## 📊 因子收益率（近 20 日）" in text
    assert "momentum" in text and "value" in text
    assert "+1.20%" in text and "-0.40%" in text   # 0.012*100, -0.004*100
    assert "日均因子收益（全周期）" in text
    assert "momentum=+0.850%" in text


def test_factor_section_none_value_renders_dash():
    desc = [("date",), ("momentum",)]
    rows = [("2026-06-30", None)]
    sel_cur = _FakeCursor(rows, description=desc)
    conn = _FakeConn(handlers=[("AVG(", _FakeCursor([(None,)])), ("SELECT *", sel_cur)])
    text = "\n".join(dg._factor_section(_FakeStore(conn=conn)))
    assert "| 2026-06-30 | — |" in text


def test_factor_section_empty_and_exception():
    conn_empty = _FakeConn(handlers=[("SELECT *", _FakeCursor([], description=[("date",)]))])
    assert dg._factor_section(_FakeStore(conn=conn_empty)) == []
    assert dg._factor_section(_FakeStore(conn=_FakeConn(raise_on=True))) == []


# ── 7. _rag_section（mock rag）────────────────────────────────────────────────

def test_rag_section_renders_stats():
    fake_stats = {
        "total_chunks": 42,
        "book_count": 3,
        "by_type": {"book": 30, "pdf": 12},
    }
    with mock.patch("x_agent.rag.collection_stats", return_value=fake_stats):
        text = "\n".join(dg._rag_section())
    assert "## 📚 知识库状态" in text
    assert "共 **42** 个知识块" in text
    assert "书籍 **3** 本" in text
    assert "微信读书" in text and "PDF 文档" in text


def test_rag_section_empty_when_no_chunks():
    with mock.patch("x_agent.rag.collection_stats", return_value={"total_chunks": 0}):
        assert dg._rag_section() == []


def test_rag_section_exception_degrades():
    with mock.patch("x_agent.rag.collection_stats", side_effect=RuntimeError("no db")):
        assert dg._rag_section() == []


# ── 8. _book_annotation（mock rag）────────────────────────────────────────────

def test_book_annotation_formats_quote():
    hits = [{
        "score": 0.9,
        "content": "安全边际是\n投资的核心   原则。",
        "meta": {"title": "聪明的投资者 — 第8章", "author": "格雷厄姆"},
    }]
    with mock.patch("x_agent.rag.collection_stats", return_value={"by_type": {"book": 5}}), \
         mock.patch("x_agent.rag.retrieve", return_value=hits):
        note = dg._book_annotation("安全边际")
    assert "📚 **相关投资原理**" in note
    assert "安全边际是 投资的核心 原则。" in note   # 换行/多空白被压成单空格
    assert "《聪明的投资者》" in note               # 只取 — 前的书名
    assert "by 格雷厄姆" in note


def test_book_annotation_filters_low_score():
    hits = [{"score": 0.001, "content": "太低分", "meta": {"title": "X"}}]
    with mock.patch("x_agent.rag.collection_stats", return_value={"by_type": {"book": 5}}), \
         mock.patch("x_agent.rag.retrieve", return_value=hits):
        assert dg._book_annotation("q") == ""


def test_book_annotation_empty_library_skips():
    with mock.patch("x_agent.rag.collection_stats", return_value={"by_type": {"book": 0}}):
        assert dg._book_annotation("q") == ""


def test_book_annotation_rag_import_failure_degrades():
    with mock.patch("x_agent.rag.collection_stats", side_effect=ImportError("boom")):
        assert dg._book_annotation("q") == ""


# ── 9. build_digest 端到端 ────────────────────────────────────────────────────

def _signal_row(author, text, cat, score, tickers, extracted):
    # (author, text, url, created_at, category, score, tickers, extracted)
    return (author, text, f"http://x/{author}", "2026-06-30T07:00:00Z",
            cat, score, tickers, extracted)


def test_build_digest_writes_file_and_sections():
    extracted = json.dumps({
        "direction": "long", "entry": "100", "target": "120",
        "stop": "90", "confidence": "high", "thesis": "突破在即",
    })
    signals = [
        _signal_row("trader1", "做多 $BTC 突破阻力", "strategy", 8,
                    json.dumps(["BTC"]), extracted),
        _signal_row("trader2", "以太坊生态利好", "web3", 5,
                    json.dumps([]), ""),
        _signal_row("trader3", "全面看涨", "both", 7,
                    json.dumps(["ETH"]), ""),
    ]
    store = _FakeStore(
        recent_signals=signals,
        price_bars_a_shares=[_price_row("600519", "贵州茅台", 1725.5, 2.1)],
    )

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "digest.md")
        # 关闭书籍注解 + 把 rag 统计 mock 成空，避免触碰真实 DB
        with mock.patch("x_agent.rag.collection_stats", return_value={"total_chunks": 0}):
            out = dg.build_digest(store, path, annotate_books=False)
        # 返回值与落盘内容一致
        with open(path, encoding="utf-8") as f:
            assert f.read() == out

    # strategy 含 strategy + both，共 2 条；web3 含 web3 + both，共 2 条
    assert "## 📈 交易策略信号（2 条）" in out
    assert "## 🌐 Web3 资讯（2 条）" in out
    assert "**@trader1**" in out and "BTC" in out
    assert "评分 8" in out
    # extracted 字段被展开
    assert "方向 `long`" in out and "止损 `90`" in out
    assert "逻辑：突破在即" in out
    # 行情板块
    assert "## 💹 市场行情" in out
    assert "贵州茅台" in out
    # 标题行带日期前缀
    assert out.startswith("# X 资讯摘要 —")


def test_build_digest_annotate_books_invokes_annotation():
    signals = [_signal_row("t1", "价值投资", "strategy", 9, json.dumps([]), "")]
    store = _FakeStore(recent_signals=signals)
    hits = [{"score": 0.5, "content": "护城河重要", "meta": {"title": "巴菲特之道"}}]
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "digest.md")
        with mock.patch("x_agent.rag.collection_stats", return_value={"by_type": {"book": 1}, "total_chunks": 1, "book_count": 1}), \
             mock.patch("x_agent.rag.retrieve", return_value=hits):
            out = dg.build_digest(store, path, annotate_books=True)
    assert "📚 **相关投资原理**" in out
    assert "《巴菲特之道》" in out


# ── 独立运行入口（无 pytest 时） ──────────────────────────────────────────────

def _run_standalone() -> int:
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {fn.__name__}: {e}")
            failed += 1
        except Exception as e:  # noqa: BLE001
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed (total {passed + failed})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_standalone())
