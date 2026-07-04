#!/usr/bin/env python3
"""
ASR转写监控和优化脚本 — 实时追踪进度、资源使用、质量指标
"""
import json, os, sys, time, sqlite3, subprocess
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent


def log(m):
    print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


def get_asr_progress(course="70天共读"):
    """获取ASR完成进度"""
    done_file = ROOT / "data" / f"asr_done_{course}.json"
    audio_dir = ROOT / "data" / "asr_audio"

    mp3s = len([f for f in os.listdir(audio_dir) if f.lower().endswith((".mp3", ".m4a", ".wav"))])
    done = len(json.load(open(done_file))) if done_file.exists() else 0
    rejected = 0
    reject_file = ROOT / "data" / f"asr_rejected_{course}.log"
    if reject_file.exists():
        rejected = len(reject_file.read_text().strip().split('\n'))

    accepted = done - rejected
    return {
        "total": mp3s,
        "completed": done,
        "accepted": accepted,
        "rejected": rejected,
        "remaining": mp3s - done,
        "progress_pct": int(100 * done / mp3s) if mp3s else 0
    }


def get_rag_stats():
    """获取RAG数据库统计"""
    db_path = ROOT / "output" / "rag.db"
    if not db_path.exists():
        return None

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    # 统计ASR相关chunks
    asr_chunks = cursor.execute(
        "SELECT COUNT(*) FROM chunks WHERE source_id LIKE 'netdisk:asr:%'"
    ).fetchone()[0]

    # 统计70天共读
    course_chunks = cursor.execute(
        "SELECT COUNT(*) FROM chunks WHERE source_id LIKE '%70天共读%'"
    ).fetchone()[0]

    # 统计总chunks
    total_chunks = cursor.execute(
        "SELECT COUNT(*) FROM chunks"
    ).fetchone()[0]

    # 统计所有来源
    by_type = cursor.execute(
        "SELECT source_type, COUNT(*) FROM chunks GROUP BY source_type ORDER BY COUNT(*) DESC"
    ).fetchall()

    conn.close()

    return {
        "total_chunks": total_chunks,
        "asr_chunks": asr_chunks,
        "course_chunks": course_chunks,
        "by_source": {t: c for t, c in by_type}
    }


def get_system_status():
    """获取系统资源使用状态"""
    try:
        # CPU使用率
        result = subprocess.run(
            "ps aux | grep 'transcribe_asr' | grep -v grep | awk '{sum+=$3} END {print sum}'",
            shell=True, capture_output=True, text=True, timeout=5
        )
        cpu_usage = float(result.stdout.strip() or 0)

        # 内存使用
        result = subprocess.run(
            "ps aux | grep 'transcribe_asr' | grep -v grep | awk '{sum+=$6} END {print sum}'",
            shell=True, capture_output=True, text=True, timeout=5
        )
        mem_usage_kb = int(result.stdout.strip() or 0)
        mem_usage_gb = mem_usage_kb / 1024 / 1024

        # 进程数
        result = subprocess.run(
            "ps aux | grep 'transcribe_asr' | grep -v grep | wc -l",
            shell=True, capture_output=True, text=True, timeout=5
        )
        process_count = int(result.stdout.strip() or 0)

        return {
            "cpu_usage_pct": cpu_usage,
            "memory_usage_gb": mem_usage_gb,
            "process_count": process_count
        }
    except Exception as e:
        return {"error": str(e)}


def print_status():
    """打印完整状态报告"""
    log("\n" + "="*70)
    log("📊 ASR 转写实时监控面板")
    log("="*70)

    # ASR进度
    progress = get_asr_progress()
    log(f"✅ 已完成:    {progress['completed']}/{progress['total']} ({progress['progress_pct']}%)")
    log(f"   - 入库:     {progress['accepted']} 条")
    log(f"   - 质检拒:   {progress['rejected']} 条")
    log(f"   - 待处理:   {progress['remaining']} 条")

    # 系统资源
    sys_status = get_system_status()
    if "error" not in sys_status:
        log(f"\n💻 系统资源")
        log(f"   - CPU使用:  {sys_status['cpu_usage_pct']:.1f}%")
        log(f"   - 内存使用: {sys_status['memory_usage_gb']:.1f} GB / 48 GB")
        log(f"   - 进程数:   {sys_status['process_count']}")

    # RAG统计
    rag = get_rag_stats()
    if rag:
        log(f"\n📚 RAG 知识库统计")
        log(f"   - 总chunks:     {rag['total_chunks']:,}")
        log(f"   - ASR chunks:   {rag['asr_chunks']:,}")
        log(f"   - 70天共读:     {rag['course_chunks']:,}")

    # 速率估计
    if progress['completed'] > 0 and 'start_time' in globals():
        elapsed = time.time() - start_time
        rate = progress['completed'] / elapsed
        remaining_time = progress['remaining'] / rate if rate > 0 else 0
        log(f"\n⏱️  处理速率")
        log(f"   - 速率:      {rate:.1f} 文件/分钟")
        log(f"   - 预计完成:  {remaining_time/60:.1f} 分钟后")

    log("="*70 + "\n")


def watch_progress(interval=30):
    """持续监控进度"""
    log(f"🔍 开始监控（每 {interval} 秒更新一次）")
    global start_time
    start_time = time.time()
    prev_completed = 0

    try:
        while True:
            print_status()
            progress = get_asr_progress()

            # 检查是否完成
            if progress['completed'] == progress['total'] and progress['total'] > 0:
                log("🎉 全部处理完成！")
                break

            # 显示速率
            if progress['completed'] > prev_completed:
                new_items = progress['completed'] - prev_completed
                elapsed = time.time() - start_time
                log(f"✨ 新增入库：+{new_items} 条 (总耗时: {elapsed/60:.1f} 分钟)")
                prev_completed = progress['completed']

            time.sleep(interval)
    except KeyboardInterrupt:
        log("\n⏹️  监控已停止")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "watch":
        watch_progress(interval=int(sys.argv[2]) if len(sys.argv) > 2 else 30)
    else:
        print_status()
