"""
split_text 分块函数回归测试。

锁定的历史 bug：分隔符逻辑曾在窗口前部选中一个孤立的 "\n\n" 切出碎块，
随后 overlap 减法导致 start 后退、回退机制每次只前进 1 个字符，
最终把一本 641 页的书炸成 11.7 万个平均 69 字的近似重复碎块
（正常应约 1300 块、平均 ~450 字）。

修复后：任何分隔符断点切出的块不得小于 chunk_size//2，且保证每轮向前推进。

本文件既可用 pytest 运行：
    python -m pytest tests/test_chunking.py -v
也可直接当脚本运行（无 pytest 依赖）：
    python tests/test_chunking.py
"""
from __future__ import annotations

import hashlib
import os
import re
import sys

# 让 `import x_agent.rag` 在任意 cwd 下都能工作
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from x_agent.rag import CHUNK_OVERLAP, CHUNK_SIZE, split_text


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def _preprocess(text: str) -> str:
    """复刻 split_text 的预处理：strip + 折叠 3+ 连续换行为 2 个。"""
    return re.sub(r"\n{3,}", "\n\n", text.strip())


def _nows(s: str) -> str:
    """去掉所有空白字符，用于按内容（忽略空白）比较。"""
    return re.sub(r"\s+", "", s)


def _reconstruct_nows(chunks: list[str]) -> str:
    """
    通过相邻块的重叠区域把分块拼回原文（忽略空白）。
    每块先去空白，再用「前一块的最长后缀 == 当前块的最长前缀」消重叠拼接。
    用于验证「无丢失、无空洞」的完整覆盖。
    """
    norm = [_nows(c) for c in chunks]
    norm = [c for c in norm if c]
    if not norm:
        return ""
    out = norm[0]
    for cur in norm[1:]:
        max_ov = min(len(out), len(cur))
        k = 0
        for j in range(max_ov, 0, -1):
            if out[-j:] == cur[:j]:
                k = j
                break
        out += cur[k:]
    return out


def _varied(nchars: int) -> str:
    """
    生成确定性但非周期的字符串（哈希链），不含任何分隔符。
    用于覆盖率/重叠测试：避免均匀重复字符导致重叠匹配过度消重的假象。
    """
    out, seed = [], b"chunk-seed"
    while sum(len(s) for s in out) < nchars:
        seed = hashlib.md5(seed).hexdigest().encode()
        out.append(seed.decode())
    return "".join(out)[:nchars]


def _is_substring_of(chunk: str, text: str) -> bool:
    """chunk 来自 text[start:end].strip()，其核心内容必为 text 的子串。"""
    return chunk in text


def _make_realistic_blob() -> str:
    """
    一段较长的英文文本，含大量单 "\n" 换行（每行一句），
    并夹杂偶发的孤立 "\n\n"（段落分隔）。模拟真实书籍排版。
    """
    lines = []
    for i in range(400):
        # 每行约 40~60 字符，英文句号 "." 不是分隔符，只有换行才是
        lines.append(
            f"This is line number {i} describing some financial market behaviour today."
        )
        # 每 7 行插入一个孤立的段落分隔，制造「窗口前部出现 \n\n」的场景
        if i % 7 == 0:
            lines.append("")  # 产生一个空行 → "\n\n"
    return "\n".join(lines)


def _make_pathological() -> str:
    """
    专门复现 bug 模式：每个 ~480 字符的窗口里，前部出现一个孤立 "\n\n"，
    其后跟着约 450 字符、仅含单 "\n" 分隔的内容。
    修复前这种排版会触发碎块洪流。
    """
    segments = []
    for _ in range(40):
        head = "intro phrase about the topic here"          # ~33 chars，无分隔符
        body_lines = [f"detail token group {j:02d} on the matter here" for j in range(11)]
        body = "\n".join(body_lines)                          # ~11 行单 \n，约 450 chars
        segments.append(head + "\n\n" + body)                 # 前部孤立 \n\n
    return "\n".join(segments)


# ── 1. 核心回归：块数有界、平均长度合理 ───────────────────────────────────────

def test_realistic_blob_not_a_flood():
    text = _make_realistic_blob()
    processed = _preprocess(text)
    chunks = split_text(processed)

    # 期望块数 ≈ len / (chunk_size - overlap)，允许小倍数松弛，但绝不能 10x
    expected = len(processed) / (CHUNK_SIZE - CHUNK_OVERLAP)
    assert len(chunks) <= expected * 3, (
        f"块数 {len(chunks)} 远超期望 {expected:.0f}，疑似碎块洪流"
    )
    assert len(chunks) >= expected * 0.4, (
        f"块数 {len(chunks)} 异常偏少，分块可能未生效"
    )

    avg = sum(len(c) for c in chunks) / len(chunks)
    assert avg > CHUNK_SIZE // 3, f"平均块长 {avg:.0f} 过小（应 > {CHUNK_SIZE // 3}）"


def test_pathological_pattern_no_tiny_fragment_flood():
    text = _make_pathological()
    processed = _preprocess(text)
    chunks = split_text(processed)

    # bug 版本下平均块长约 69 字、块数成千上万；修复后必须明显更大、更少
    avg = sum(len(c) for c in chunks) / len(chunks)
    assert avg > CHUNK_SIZE // 3, (
        f"病态输入平均块长仅 {avg:.0f}，疑似碎块洪流（bug 复发）"
    )

    # 直接对「碎块」计数：远小于 min_chunk 的块不应大量出现
    min_chunk = CHUNK_SIZE // 2
    tiny = [c for c in chunks if len(c) < min_chunk]
    # 末块允许偏小；但碎块占比绝不能高
    assert len(tiny) <= 2, f"出现 {len(tiny)} 个碎块（< {min_chunk} 字），bug 复发"

    expected = len(processed) / (CHUNK_SIZE - CHUNK_OVERLAP)
    assert len(chunks) <= expected * 3, (
        f"病态输入块数 {len(chunks)} 远超期望 {expected:.0f}"
    )


def test_isolated_early_separator_does_not_cut_tiny_chunk():
    """单个窗口：前部 30 字 + '\n\n' + 450 字单 '\n'。第一块不应是 30 字碎块。"""
    head = "x" * 30
    body = "\n".join(["y" * 44 for _ in range(10)])  # ~450 chars
    text = head + "\n\n" + body
    chunks = split_text(text)
    assert chunks, "不应返回空"
    # 第一块必须 >= min_chunk，而不是在早期 \n\n 处切出 30 字碎块
    assert len(chunks[0]) >= CHUNK_SIZE // 2, (
        f"第一块仅 {len(chunks[0])} 字，在早期 \\n\\n 处被错误切碎"
    )


# ── 2. 前进性 / 终止性 / 完整覆盖 ──────────────────────────────────────────────

def test_terminates_and_full_coverage():
    text = _make_realistic_blob()
    processed = _preprocess(text)
    chunks = split_text(processed)  # 能返回即证明已终止（循环保证前进）
    assert chunks
    # 消重叠拼接后（忽略空白）应完整还原原文，无丢失、无空洞
    assert _reconstruct_nows(chunks) == _nows(processed)


def test_full_coverage_no_separator_run():
    """无任何分隔符的长串：硬切应平铺、带 overlap，拼接后完整还原、无丢失。"""
    text = _varied(2300)
    chunks = split_text(text)
    assert len(chunks) >= 2
    assert _reconstruct_nows(chunks) == _nows(text)


def test_terminates_on_pathological():
    chunks = split_text(_make_pathological())
    assert chunks  # 返回即证明无死循环
    assert _reconstruct_nows(chunks) == _nows(_preprocess(_make_pathological()))


# ── 3. 边界用例 ───────────────────────────────────────────────────────────────

def test_empty_string():
    assert split_text("") == []


def test_whitespace_only():
    assert split_text("   \n\n  \t \n ") == []


def test_shorter_than_chunk_size():
    text = "a short paragraph with no special separators inside"
    chunks = split_text(text)
    # 整段必须完整出现在首块（不被切碎），且块数极少
    assert chunks[0] == text
    assert len(chunks) <= 2
    for c in chunks:
        assert _is_substring_of(c, text)


def test_exactly_chunk_size():
    text = _varied(CHUNK_SIZE)
    chunks = split_text(text)
    # 正好等于 chunk_size：整段作为首块完整出现，块数极少（不爆块）
    assert chunks[0] == text
    assert len(chunks) <= 2
    for c in chunks:
        assert _is_substring_of(c, text)


def test_no_separators_one_long_run():
    text = _varied(1700)
    chunks = split_text(text)
    assert len(chunks) >= 2
    # 所有块都不超过 chunk_size；硬切时存在满块
    assert all(len(c) <= CHUNK_SIZE for c in chunks)
    assert any(len(c) == CHUNK_SIZE for c in chunks)
    # 完整覆盖、无丢失
    assert _reconstruct_nows(chunks) == _nows(text)


def test_chinese_with_full_stop_separator():
    # 每句不同（带序号），用 "。" 分隔，凑到远超 chunk_size
    text = "".join(
        f"第{i}号公告宁德时代发布新一代麒麟电池能量密度提升带动产业链上下游联动。"
        for i in range(60)
    )
    processed = _preprocess(text)
    chunks = split_text(processed)
    assert len(chunks) >= 2
    # 在 "。" 处断开：除最后一块外，每块都应以 "。" 结尾
    for c in chunks[:-1]:
        assert c.endswith("。"), f"中文块未在句号处断开: ...{c[-10:]!r}"
    avg = sum(len(c) for c in chunks) / len(chunks)
    assert avg > CHUNK_SIZE // 3
    assert _reconstruct_nows(chunks) == _nows(processed)


def test_custom_chunk_size_and_overlap():
    text = _varied(1000)
    cs, ov = 100, 20
    chunks = split_text(text, chunk_size=cs, overlap=ov)
    assert len(chunks) >= 2
    # 自定义参数下：无块超过 cs，存在满块，且满块按 (cs-ov) 步进重叠
    assert all(len(c) <= cs for c in chunks)
    assert any(len(c) == cs for c in chunks)
    for prev, cur in zip(chunks, chunks[1:]):
        if len(prev) == cs:
            assert prev[-ov:] == cur[:ov]
    assert _reconstruct_nows(chunks) == _nows(text)


# ── 4. 重叠行为 ───────────────────────────────────────────────────────────────

def test_overlap_shared_chars_no_separator():
    """无分隔符硬切时，相邻块应精确共享 overlap 个字符。"""
    text = "".join(chr(ord("A") + (i % 26)) for i in range(2000))
    chunks = split_text(text)
    assert len(chunks) >= 2
    for prev, cur in zip(chunks, chunks[1:]):
        # 仅检查前一块是满块（== chunk_size）的情形
        if len(prev) == CHUNK_SIZE:
            assert prev[-CHUNK_OVERLAP:] == cur[:CHUNK_OVERLAP], "相邻块未共享 overlap 字符"


def test_overlap_custom_value():
    text = "".join(chr(ord("a") + (i % 26)) for i in range(1500))
    cs, ov = 200, 40
    chunks = split_text(text, chunk_size=cs, overlap=ov)
    for prev, cur in zip(chunks, chunks[1:]):
        if len(prev) == cs:
            assert prev[-ov:] == cur[:ov]


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
