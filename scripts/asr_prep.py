#!/usr/bin/env python3
"""ASR 准备：下 whisper.cpp 模型 + 70天共读 36 个 mp3。ffmpeg 未就绪也能先下。"""
import os, re, json, sys, time
from pathlib import Path
os.environ["SSL_CERT_FILE"] = __import__("certifi").where()
import requests

ROOT = Path(__file__).parent.parent
MODELS = ROOT / "models"; MODELS.mkdir(exist_ok=True)
AUDIO = ROOT / "data" / "asr_audio"; AUDIO.mkdir(parents=True, exist_ok=True)


def log(m): print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def dl_model():
    dest = MODELS / "ggml-large-v3-turbo.bin"
    if dest.exists() and dest.stat().st_size > 1_000_000_000:
        log(f"模型已存在 {dest.stat().st_size//1024//1024}MB，跳过"); return
    for url in ["https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin",
                "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.bin"]:
        try:
            log(f"下模型 {url.split('/')[-1]} ...")
            with requests.get(url, stream=True, timeout=1800) as r:  # 需要代理达 huggingface
                r.raise_for_status()
                sz = 0
                with open(dest, "wb") as f:
                    for ch in r.iter_content(1 << 20):
                        f.write(ch); sz += len(ch)
            log(f"✅ 模型 {sz//1024//1024}MB → {dest}"); return
        except Exception as e:
            log(f"模型源失败 {str(e)[:60]}")
    log("❌ 模型全部源失败")


def dl_mp3():
    S = requests.Session(); S.trust_env = False
    tok = json.load(open(ROOT / "output/baidu_token.json")); at = tok["access_token"]
    UA = {"User-Agent": "pan.baidu.com"}
    base = "/GL12/华尔街见闻/70天共读35本金融经典"
    items = S.get("https://pan.baidu.com/rest/2.0/xpan/file", params={
        "method": "list", "access_token": at, "dir": base, "limit": 1000, "web": "1"},
        headers=UA, timeout=30).json().get("list", [])
    mp3s = [f for f in items if f["server_filename"].lower().endswith(".mp3")]
    log(f"发现 {len(mp3s)} 个 mp3")
    manifest = []
    for f in mp3s:
        name = f["server_filename"]
        m = re.match(r"第(\d+)课[：:]?\s*(.*)", name)
        lesson = m.group(1) if m else ""
        book = (m.group(2) if m else name)
        book = re.split(r"唯一\d|（更多|（拼课|\[防断更", book)[0].strip()
        safe = f"{lesson.zfill(2)}_{re.sub(r'[^0-9A-Za-z一-鿿]', '', book)[:20]}.mp3"
        dest = AUDIO / safe
        manifest.append({"file": safe, "lesson_no": lesson, "book_title": book, "original": name})
        if dest.exists() and dest.stat().st_size > 0:
            continue
        try:
            m2 = S.get("https://pan.baidu.com/rest/2.0/xpan/multimedia", params={
                "method": "filemetas", "access_token": at, "fsids": json.dumps([f["fs_id"]]),
                "dlink": "1"}, headers=UA, timeout=30).json()
            dlink = m2["list"][0]["dlink"] + f"&access_token={at}"
            with S.get(dlink, headers=UA, timeout=600, stream=True) as r:
                with open(dest, "wb") as out:
                    for ch in r.iter_content(1 << 20):
                        out.write(ch)
            log(f"  ✅ {safe} ({dest.stat().st_size//1024//1024}MB)")
        except Exception as e:
            log(f"  ❌ {name[:30]}: {str(e)[:50]}")
    json.dump(manifest, open(AUDIO / "_manifest.json", "w"), ensure_ascii=False, indent=2)
    log(f"manifest 写入 {len(manifest)} 条")


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    if what in ("model", "all"): dl_model()
    if what in ("mp3", "all"): dl_mp3()
    log("ASR 准备完成")
