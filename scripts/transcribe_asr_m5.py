#!/usr/bin/env python3
"""
音频转写入库（为 Apple Silicon / M5 Pro 准备）——70天共读等 mp3 → 文字 → RAG。

⚠️ 未在本机(Intel x86)验证：mlx-whisper 仅 Apple Silicon 可用。到 M5 上先跑 --limit 1
   肉眼看中文是否通顺、是否有 whisper 重复幻觉，确认后再全量。

M5 上准备（一次性）：
    brew install ffmpeg                     # Apple Silicon 有预编译包, 秒装
    .venv/bin/pip install mlx-whisper       # 神经引擎加速
    # 音频需在本机：M5 上重跑 scripts/asr_prep.py mp3 从网盘下(或从旧机拷 data/asr_audio/)

用法：
    .venv/bin/python scripts/transcribe_asr_m5.py --limit 1     # 先验1个(必做)
    .venv/bin/python scripts/transcribe_asr_m5.py               # 全量
    .venv/bin/python scripts/transcribe_asr_m5.py --dir data/asr_audio --course 70天共读

转写文本走 rag.text_quality 门槛(拦重复幻觉/乱码)才入库；不过关只记日志不入。
"""
from __future__ import annotations
import argparse, json, os, sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SSL_CERT_FILE", __import__("certifi").where())


def log(m): print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


def transcribe(audio_path: str) -> str:
    """优先 mlx-whisper(Apple Silicon)，回退 whisper.cpp 二进制。返回中文转写文本。"""
    # 1) mlx-whisper（M5 最优）
    try:
        import mlx_whisper
        r = mlx_whisper.transcribe(
            audio_path, path_or_hf_repo="mlx-community/whisper-large-v3-turbo",
            language="zh")
        return (r.get("text") or "").strip()
    except ImportError:
        pass
    # 2) whisper.cpp 二进制回退（需 ffmpeg + whisper-cli + 本地模型）
    import subprocess, tempfile
    ffmpeg = "./bin/ffmpeg" if Path("bin/ffmpeg").exists() else "ffmpeg"
    model = ROOT / "models" / "ggml-large-v3-turbo.bin"
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "a.wav"
        subprocess.run([ffmpeg, "-y", "-i", audio_path, "-ar", "16000", "-ac", "1", str(wav)],
                       capture_output=True, timeout=600)
        out = subprocess.run(["whisper-cli", "-m", str(model), "-l", "zh", "-nt", "-f", str(wav)],
                             capture_output=True, text=True, timeout=3600)
        return out.stdout.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="data/asr_audio")
    ap.add_argument("--course", default="70天共读")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

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

    ok = skip_bad = 0
    for i, fn in enumerate(mp3s, 1):
        if fn in done:
            continue
        meta = manifest.get(fn, {})
        lesson, book = meta.get("lesson_no", ""), meta.get("book_title", Path(fn).stem)
        log(f"[{i}/{len(mp3s)}] 转写 {fn} …")
        try:
            text = transcribe(str(adir / fn))
        except Exception as e:
            log(f"   ❌ 转写失败: {str(e)[:80]}"); continue
        good, reason = text_quality(text, min_chars=100)
        if not good:
            skip_bad += 1
            log(f"   ⚠ 质检未过({reason}, {len(text)}字)，不入库，留日志")
            with open(ROOT / "data" / f"asr_rejected_{args.course}.log", "a") as f:
                f.write(f"{fn}\t{reason}\t{len(text)}字\t{text[:100]}\n")
            done.add(fn); json.dump(sorted(done), open(done_file, "w"))
            continue
        n = ingest_text(
            text, source_id=f"netdisk:asr:{args.course}:{lesson or fn}",
            source_type="netdisk", title=f"第{lesson}课 {book}".strip(),
            author=f"华尔街见闻·{args.course}", skip_vectors=True,
            extra_meta={"publication": "华尔街见闻", "course": args.course,
                        "lesson": lesson, "book": book, "asr_quality": "ok",
                        "filename": meta.get("original", fn)})
        ok += 1
        done.add(fn); json.dump(sorted(done), open(done_file, "w"))
        log(f"   ✅ {book} → {n}块 ({len(text)}字)")
    log(f"完成：入库 {ok}，质检拒 {skip_bad}")


if __name__ == "__main__":
    main()
