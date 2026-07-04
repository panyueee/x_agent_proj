#!/usr/bin/env python3
"""
ASR后处理流程：转写完成 → 向量化 → 统计 → 生成报告
"""
import json, sqlite3, sys
from pathlib import Path
from datetime import datetime
from collections import defaultdict

ROOT = Path(__file__).parent.parent


def log(m):
    print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


def embed_asr_chunks(course="70天共读"):
    """对所有ASR chunks进行向量化"""
    log(f"🔍 开始向量化 {course} chunks...")

    from x_agent.rag import get_db, embed_and_save

    db = get_db()
    cursor = db.cursor()

    # 找出所有未向量化的ASR chunks
    chunks = cursor.execute("""
        SELECT id, content, source_id FROM chunks
        WHERE source_id LIKE 'netdisk:asr:%'
        AND source_id LIKE ?
        AND embedding IS NULL
    """, (f"%{course}%",)).fetchall()

    log(f"📋 发现 {len(chunks)} 条待向量化的chunks")

    if not chunks:
        log("✅ 无待处理chunks")
        return

    # 向量化
    batch_size = 100
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i+batch_size]
        texts = [c[1] for c in batch]
        chunk_ids = [c[0] for c in batch]

        try:
            embeddings = embed_and_save(texts, chunk_ids)
            log(f"   ✅ {i+len(batch)}/{len(chunks)} ({100*(i+len(batch))//len(chunks)}%)")
        except Exception as e:
            log(f"   ❌ 向量化失败: {e}")

    log(f"✅ 向量化完成")


def generate_asr_report(course="70天共读"):
    """生成详细的ASR处理报告"""
    log(f"\n📊 生成 {course} 处理报告...")

    db_path = ROOT / "output" / "rag.db"
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    # 基本统计
    chunks = cursor.execute("""
        SELECT COUNT(*) FROM chunks
        WHERE source_id LIKE 'netdisk:asr:%' AND source_id LIKE ?
    """, (f"%{course}%",)).fetchone()[0]

    # 按书籍统计
    books = cursor.execute("""
        SELECT
            JSON_EXTRACT(extra_meta, '$.book') as book,
            JSON_EXTRACT(extra_meta, '$.lesson') as lesson,
            COUNT(*) as chunk_count,
            SUM(LENGTH(content)) as total_chars
        FROM chunks
        WHERE source_id LIKE 'netdisk:asr:%' AND source_id LIKE ?
        GROUP BY book, lesson
        ORDER BY lesson
    """, (f"%{course}%",)).fetchall()

    # 生成报告
    report = f"""# {course} ASR 转写完成报告

生成时间: {datetime.now().isoformat()}

## 📊 统计概览

- **总Chunks**: {chunks:,}
- **总字数**: {sum(b[3] or 0 for b in books):,}
- **覆盖书籍**: {len(books)}本
- **平均每书**: {chunks/len(books):.0f} chunks ({sum(b[3] or 0 for b in books)/len(books):.0f} 字)

## 📚 书籍详细清单

| 序号 | 书籍 | Chunks | 字数 | 状态 |
|------|------|--------|------|------|
"""

    for lesson, book, chunk_count, chars in books:
        lesson = lesson or "N/A"
        book = book or "未命名"
        chars = chars or 0
        report += f"| {lesson} | {book} | {chunk_count} | {chars:,} | ✅ |\n"

    report += f"""

## 🎯 下一步

- [ ] 向量化检索优化
- [ ] 与已有RAG库合并检索
- [ ] 生成大师课学习指南
- [ ] 按讲师/主题重组索引

---
生成者: ASR后处理流程 v1.0
"""

    report_file = ROOT / "output" / f"asr_report_{course}_{datetime.now():%Y%m%d}.md"
    report_file.write_text(report, encoding="utf-8")
    log(f"✅ 报告已保存: {report_file}")

    return report


def verify_quality(course="70天共读"):
    """质量验证：检查质检拒绝率、内容分布等"""
    log(f"\n✔️  质量验证...")

    reject_file = ROOT / "data" / f"asr_rejected_{course}.log"
    done_file = ROOT / "data" / f"asr_done_{course}.json"

    done = len(json.load(open(done_file))) if done_file.exists() else 0
    rejected = 0
    if reject_file.exists():
        lines = reject_file.read_text().strip().split('\n')
        rejected = len([l for l in lines if l])

    accepted = done - rejected

    log(f"   - 总处理: {done}")
    log(f"   - 入库: {accepted} ({100*accepted//done if done else 0}%)")
    log(f"   - 拒绝: {rejected} ({100*rejected//done if done else 0}%)")

    # 检查拒绝原因分布
    if reject_file.exists():
        reasons = defaultdict(int)
        for line in reject_file.read_text().strip().split('\n'):
            if line:
                parts = line.split('\t')
                if len(parts) > 1:
                    reasons[parts[1]] += 1
        log(f"   - 拒绝原因: {dict(reasons)}")

    return {"total": done, "accepted": accepted, "rejected": rejected}


def main():
    course = sys.argv[1] if len(sys.argv) > 1 else "70天共读"

    log(f"🚀 开始后处理流程: {course}")

    # 1. 质量验证
    quality = verify_quality(course)

    # 2. 向量化
    embed_asr_chunks(course)

    # 3. 生成报告
    report = generate_asr_report(course)
    print("\n" + report)

    log(f"\n✅ 后处理完成！")


if __name__ == "__main__":
    main()
