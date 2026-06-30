"""
rag.py 非分块核心助手回归测试（不含 split_text，后者已由 tests/test_chunking.py 覆盖）。

覆盖：
  - _rrf_merge   Reciprocal Rank Fusion 融合排序
  - _doc_id      确定性 chunk id 生成
  - _tokenize    jieba 分词（缺 jieba 时退化为逐字符）
  - _tok_fts     空格分隔的 token 串
  - ingest_text  入库 + 幂等去重 + chunks/chunks_fts 1:1 不变量
  - collection_stats  统计反映已入库内容

数据库隔离（关键）：
  在 import x_agent.rag 之前就把 RAG_DB_PATH / LANCE_DB_PATH 指向临时目录，
  随后 importlib.reload 强制模块重新读取这两个环境变量。
  原因：pytest 按字母序先收集 test_chunking.py，那时 x_agent.rag 已以默认
  路径 (./output/rag.db) 进入 sys.modules；若不 reload，本文件复用的就是
  绑定了真实 DB 的旧模块对象。reload 后 RAG_DB_PATH 全局常量与线程局部连接
  (_local) 全部重建，确保绝不触碰 output/rag.db 与 output/rag_vectors。

  本文件可用 pytest 运行：
      python -m pytest tests/test_rag_core.py -v
  也可直接当脚本运行（无 pytest 依赖）：
      python tests/test_rag_core.py
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile

# 让 `import x_agent.rag` 在任意 cwd 下都能工作
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ── 在导入 rag 之前就把 DB 指向临时目录 ─────────────────────────────────────────
_TMP_DIR = tempfile.mkdtemp(prefix="rag_core_test_")
_TMP_DB = os.path.join(_TMP_DIR, "rag_test.db")
os.environ["RAG_DB_PATH"] = _TMP_DB
os.environ["LANCE_DB_PATH"] = os.path.join(_TMP_DIR, "rag_vectors")

import x_agent.rag as rag  # noqa: E402

# test_chunking.py 可能已先以默认路径导入本模块；reload 强制重新读取环境变量。
importlib.reload(rag)


# ── 辅助 ─────────────────────────────────────────────────────────────────────

def _reset_db() -> None:
    """清空两张表到已知状态。线程局部连接在测试间持续存在，需手动清理。"""
    db = rag._db()
    db.execute("DELETE FROM chunks")
    db.execute("DELETE FROM chunks_fts")
    db.commit()


def _counts() -> tuple[int, int]:
    db = rag._db()
    c = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    f = db.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
    return c, f


def _hit(cid: str) -> dict:
    """构造 _rrf_merge 所需的最小命中条目（仅需 'id'，会被写入 'score'）。"""
    return {"id": cid, "content": f"content-{cid}"}


# ── 0. DB 隔离守卫（最重要：失败即大声报错而非静默写真库）────────────────────────

def test_db_isolated_to_tmp():
    assert rag.RAG_DB_PATH == _TMP_DB, (
        f"RAG_DB_PATH 未指向临时库: {rag.RAG_DB_PATH}（reload 失效？）"
    )
    assert "output/rag.db" not in rag.RAG_DB_PATH, "疑似指向真实库 output/rag.db"
    # 实际建连也应落在临时目录
    db = rag._db()
    (dbfile,) = db.execute("PRAGMA database_list").fetchone()[2:3] or ("",)
    assert _TMP_DIR in (dbfile or ""), f"连接文件不在临时目录: {dbfile}"


# ── 1. _doc_id：确定性 / 稳定 / 不同 chunk_idx → 不同 id ──────────────────────────

def test_doc_id_deterministic():
    assert rag._doc_id("src-A", 0) == rag._doc_id("src-A", 0)
    assert rag._doc_id("book:xyz", 7) == rag._doc_id("book:xyz", 7)


def test_doc_id_distinct_chunk_idx():
    sid = "src-A"
    ids = [rag._doc_id(sid, i) for i in range(50)]
    assert len(set(ids)) == 50, "同一 source_id 下不同 chunk_idx 必须得到不同 id"


def test_doc_id_format_ends_with_idx():
    cid = rag._doc_id("src-A", 12)
    assert cid.endswith("_12"), f"id 应以 _<chunk_idx> 结尾: {cid}"
    # 形如 <8位hash>_<idx>
    head, _, tail = cid.partition("_")
    assert len(head) == 8 and tail == "12"


def test_doc_id_source_id_affects_id():
    # 不同 source_id、相同 idx 一般应得到不同 id（哈希前缀不同）
    assert rag._doc_id("src-A", 0) != rag._doc_id("src-B", 0)


# ── 2. _tokenize / _tok_fts：属性而非精确 token 列表（jieba 版本/有无可变）────────

def test_tokenize_nonempty_for_cjk_and_english():
    text = "宁德时代发布新一代麒麟电池 Tesla 2024"
    toks = rag._tokenize(text)
    assert isinstance(toks, list)
    assert len(toks) > 0, "CJK+英文混合输入应产出非空 token"
    # 每个 token 都已 strip，不含两端空白，且非空
    assert all(t == t.strip() and t for t in toks)


def test_tokenize_deterministic():
    text = "光伏产业链 supply chain 联动 2025"
    assert rag._tokenize(text) == rag._tokenize(text)


def test_tokenize_empty_input():
    assert rag._tokenize("") == []
    assert rag._tokenize("   \n\t ") == []


def test_tok_fts_is_whitespace_join_of_tokenize():
    text = "宁德时代 与 比亚迪 battery 2024"
    toks = rag._tokenize(text)
    assert toks, "前置条件：该输入应产生 token"
    assert rag._tok_fts(text) == " ".join(toks)
    # token 串内不应出现连续/首尾多余空白结构以外的东西：split 还原应等于 token 列表
    assert rag._tok_fts(text).split() == toks


def test_tok_fts_deterministic_and_nonempty():
    text = "新能源汽车 EV 渗透率 2025"
    a, b = rag._tok_fts(text), rag._tok_fts(text)
    assert a == b
    assert a.strip() != ""


def test_tok_fts_empty_returns_raw_text():
    # 源码：tokens 为空时返回原文本（而非空串）
    assert rag._tok_fts("") == ""
    ws = "   "
    assert rag._tok_fts(ws) == ws


# ── 3. _rrf_merge：融合打分 / 多列表高排名占优 / 去重 / 空列表 / 降序 ──────────────

def test_rrf_empty_inputs():
    assert rag._rrf_merge([]) == []
    assert rag._rrf_merge([[], []]) == []


def test_rrf_exact_scores_and_order():
    # list1: a(rank0), b(rank1) ; list2: b(rank0), c(rank1)
    # k=60，code 用 1/(k+rank+1)（标准 RRF 的 1-indexed 约定）
    merged = rag._rrf_merge([[_hit("a"), _hit("b")],
                             [_hit("b"), _hit("c")]])
    ids = [h["id"] for h in merged]
    # b 同时出现且排名靠前 → 融合分最高
    assert ids[0] == "b"
    # a(1/61) > c(1/62)
    assert ids == ["b", "a", "c"]

    by_id = {h["id"]: h["score"] for h in merged}
    assert by_id["b"] == round(1 / 61 + 1 / 62, 6)
    assert by_id["a"] == round(1 / 61, 6)
    assert by_id["c"] == round(1 / 62, 6)


def test_rrf_multi_list_beats_single():
    # x 在两个列表都靠前；y 仅在一个列表靠前
    merged = rag._rrf_merge([[_hit("x"), _hit("y")],
                             [_hit("x")]])
    assert merged[0]["id"] == "x"
    sc = {h["id"]: h["score"] for h in merged}
    assert sc["x"] > sc["y"]


def test_rrf_dedup_single_entry_per_id():
    merged = rag._rrf_merge([[_hit("a"), _hit("b"), _hit("c")],
                             [_hit("b"), _hit("c"), _hit("a")],
                             [_hit("c")]])
    ids = [h["id"] for h in merged]
    assert sorted(ids) == ["a", "b", "c"], "每个 id 仅保留一条"
    assert len(ids) == len(set(ids))
    # c 在三个列表均出现且整体靠前 → 应排第一
    assert ids[0] == "c"


def test_rrf_sorted_descending():
    merged = rag._rrf_merge([[_hit("a"), _hit("b"), _hit("c"), _hit("d")]])
    scores = [h["score"] for h in merged]
    assert scores == sorted(scores, reverse=True), "结果须按融合分降序"


def test_rrf_dedup_keeps_first_hit_object():
    # 同一 id 在不同列表中携带不同内容，去重应保留首次遇到的对象
    first = {"id": "z", "content": "FIRST"}
    second = {"id": "z", "content": "SECOND"}
    merged = rag._rrf_merge([[first], [second]])
    assert len(merged) == 1
    assert merged[0]["content"] == "FIRST"


# ── 4. ingest_text：入库 / 幂等 / chunks==chunks_fts 1:1 不变量 ──────────────────

_LONG_TEXT = "".join(
    f"第{i}号研报：宁德时代麒麟电池能量密度提升带动产业链上下游联动，"
    f"光伏与储能板块景气度持续上行，关注新能源汽车渗透率变化。"
    for i in range(40)
)


def test_ingest_creates_chunks_and_1to1():
    _reset_db()
    n = rag.ingest_text(
        _LONG_TEXT, source_id="unit:ingest1", source_type="article",
        title="测试研报", author="某分析师", skip_vectors=True,
    )
    assert n > 0, "长文本应产出至少一个块"
    c, f = _counts()
    assert c == n, "chunks 行数应等于新增块数"
    assert c == f, "chunks 与 chunks_fts 行数必须 1:1 一致（关键不变量）"


def test_ingest_idempotent_reingest_returns_zero():
    _reset_db()
    first = rag.ingest_text(
        _LONG_TEXT, source_id="unit:ingest2", source_type="article",
        skip_vectors=True,
    )
    assert first > 0
    c1, f1 = _counts()

    second = rag.ingest_text(
        _LONG_TEXT, source_id="unit:ingest2", source_type="article",
        skip_vectors=True,
    )
    assert second == 0, "相同 source_id 再次入库应去重，返回 0 新增"

    c2, f2 = _counts()
    assert (c2, f2) == (c1, f1), "幂等再入库后行数不应变化"
    assert c2 == f2, "再入库后 chunks 与 chunks_fts 仍须 1:1"


def test_ingest_empty_text_returns_zero():
    _reset_db()
    assert rag.ingest_text("   ", source_id="unit:empty", skip_vectors=True) == 0
    assert _counts() == (0, 0)


def test_ingest_distinct_sources_accumulate():
    _reset_db()
    a = rag.ingest_text("宁德时代麒麟电池产业链联动分析。" * 5,
                        source_id="unit:srcA", source_type="article",
                        skip_vectors=True)
    b = rag.ingest_text("光伏储能景气度持续上行的研判。" * 5,
                        source_id="unit:srcB", source_type="book",
                        skip_vectors=True)
    assert a > 0 and b > 0
    c, f = _counts()
    assert c == a + b
    assert c == f, "多来源累加后仍须 1:1"


# ── 5. collection_stats：统计反映已入库内容 ─────────────────────────────────────

def test_collection_stats_reflects_ingested():
    _reset_db()
    n_art = rag.ingest_text("新能源汽车渗透率分析研报内容。" * 4,
                           source_id="unit:stats:art", source_type="article",
                           skip_vectors=True)
    n_book = rag.ingest_text("价值投资经典书籍章节内容讲解。" * 4,
                            source_id="unit:stats:book", source_type="book",
                            skip_vectors=True)
    assert n_art > 0 and n_book > 0

    stats = rag.collection_stats()
    assert stats["total_chunks"] == n_art + n_book
    assert stats["by_type"].get("article") == n_art
    assert stats["by_type"].get("book") == n_book
    assert stats["book_count"] == 1, "只入了一个 book source_id"


def test_collection_stats_empty_db():
    _reset_db()
    stats = rag.collection_stats()
    assert stats["total_chunks"] == 0
    assert stats["by_type"] == {}
    assert stats["book_count"] == 0


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
