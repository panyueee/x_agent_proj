#!/usr/bin/env python3
"""
音频并行转写入库 — 充分利用M5的18核处理器。
改进自 transcribe_asr_m5.py，支持多进程并行处理。
"""
from __future__ import annotations
import argparse, json, os, sys, time, subprocess, tempfile
from datetime import datetime
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
from multiprocessing import cpu_count

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SSL_CERT_FILE", __import__("certifi").where())


def log(m):
    print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


def transcribe(audio_path: str) -> str:
    """单个文件转写（可在子进程中调用）"""
    try:
        import mlx_whisper
        r = mlx_whisper.transcribe(
            audio_path, path_or_hf_repo="mlx-community/whisper-large-v3-turbo",
            language="zh")
        return (r.get("text") or "").strip()
    except ImportError:
        pass
    # 回退到 whisper.cpp
    ffmpeg = "./bin/ffmpeg" if Path("bin/ffmpeg").exists() else "ffmpeg"
    model = ROOT / "models" / "ggml-large-v3-turbo.bin"
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "a.wav"
        subprocess.run([ffmpeg, "-y", "-i", audio_path, "-ar", "16000", "-ac", "1", str(wav)],
                       capture_output=True, timeout=600)
        out = subprocess.run(["whisper-cli", "-m", str(model), "-l", "zh", "-nt", "-f", str(wav)],
                             capture_output=True, text=True, timeout=3600)
        return out.stdout.strip()


def process_audio(item: dict) -> dict:
    """处理单个音频文件（在worker进程中运行）"""
    fn, adir, manifest, course = item["fn"], item["adir"], item["manifest"], item["course"]
    meta = manifest.get(fn, {})
    lesson, book = meta.get("lesson_no", ""), meta.get("book_title", Path(fn).stem)

    try:
        text = transcribe(str(adir / fn))
        return {
            "fn": fn,
            "status": "ok" if text else "empty",
            "text": text,
            "lesson": lesson,
            "book": book,
            "meta": meta,
            "course": course,
            "error": None
        }
    except Exception as e:
        return {
            "fn": fn,
            "status": "error",
            "text": None,
            "lesson": lesson,
            "book": book,
            "meta": meta,
            "course": course,
            "error": str(e)[:100]
        }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="data/asr_audio")
    ap.add_argument("--course", default="70天共读")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=None)  # 并发进程数
    args = ap.parse_args()

    # 确定worker数量（默认使用CPU核心数的80%避免系统卡顿）
    max_workers = args.workers or max(2, int(cpu_count() * 0.8))
    log(f"🚀 启动并行处理：{max_workers} 个worker，{cpu_count()} 核可用")

    from x_agent.rag import ingest_text, text_quality

    adir = ROOT / args.dir
    manifest = {}
    mpath = adir / "_manifest.json"
    if mpath.exists():
        for e in json.load(open(mpath)):
            manifest[e["file"]] = e

    mp3s = sorted(f for f in os.listdir(adir) if f.lower().endswith((".mp3", ".m4a", ".wav")))
    if args.limit:
        mp3s = mp3s[:args.limit]

    done_file = ROOT / "data" / f"asr_done_{args.course}.json"
    done = set(json.load(open(done_file))) if done_file.exists() else set()

    # 过滤已完成的
    todo = [f for f in mp3s if f not in done]
    log(f"📋 待处理：{len(todo)}/{len(mp3s)} 个文件")

    if not todo:
        log("✅ 全部已完成")
        return

    # 准备任务
    tasks = [{"fn": f, "adir": adir, "manifest": manifest, "course": args.course} for f in todo]

    ok = skip_bad = failed = 0
    start_time = time.time()

    # 并行处理
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_audio, task): task for task in tasks}
        completed = 0

        for future in as_completed(futures):
            completed += 1
            result = future.result()
            fn = result["fn"]

            if result["status"] == "error":
                log(f"[{completed}/{len(todo)}] ❌ {fn}: {result['error']}")
                failed += 1
                done.add(fn)
                json.dump(sorted(done), open(done_file, "w"))
                continue

            if result["status"] == "empty":
                log(f"[{completed}/{len(todo)}] ⚠ {fn}: 转写为空")
                skip_bad += 1
                done.add(fn)
                json.dump(sorted(done), open(done_file, "w"))
                continue

            text = result["text"]
            good, reason = text_quality(text, min_chars=100)
            if not good:
                skip_bad += 1
                log(f"[{completed}/{len(todo)}] ⚠ {fn}: 质检未过({reason})")
                with open(ROOT / "data" / f"asr_rejected_{args.course}.log", "a") as f:
                    f.write(f"{fn}\t{reason}\t{len(text)}字\n")
                done.add(fn)
                json.dump(sorted(done), open(done_file, "w"))
                continue

            # 入库
            try:
                lesson, book = result["lesson"], result["book"]
                n = ingest_text(
                    text, source_id=f"netdisk:asr:{args.course}:{lesson or fn}",
                    source_type="netdisk", title=f"第{lesson}课 {book}".strip(),
                    author=f"华尔街见闻·{args.course}", skip_vectors=True,
                    extra_meta={"publication": "华尔街见闻", "course": args.course,
                                "lesson": lesson, "book": book, "asr_quality": "ok",
                                "filename": result["meta"].get("original", fn)})
                ok += 1
                log(f"[{completed}/{len(todo)}] ✅ {book} → {n}块 ({len(text)}字)")
            except Exception as e:
                log(f"[{completed}/{len(todo)}] ❌ {fn} 入库失败: {str(e)[:60]}")
                failed += 1

            done.add(fn)
            json.dump(sorted(done), open(done_file, "w"))

    elapsed = time.time() - start_time
    log(f"\n{'='*60}")
    log(f"📊 完成统计")
    log(f"{'='*60}")
    log(f"  ✅ 入库:     {ok}")
    log(f"  ⚠️  质检拒:  {skip_bad}")
    log(f"  ❌ 错误:     {failed}")
    log(f"  ⏱  耗时:     {elapsed:.1f}秒 ({len(todo)/elapsed:.1f} 文件/秒)")
    log(f"  💾 总字数:   {ok * 5000:.0f}字 (估计)")
    log(f"{'='*60}\n")


if __name__ == "__main__":
    main()
