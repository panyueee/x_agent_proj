"""
OCR 子进程 worker：流式处理整个 PDF，每页输出一行 JSON。

调用方式（由 rag.py 内部通过 subprocess 调用，不直接使用）：
    python scripts/ocr_worker.py <pdf_path> [dpi]

stdout 每页输出一行 JSON:
    {"page": 0, "text": "识别文字..."}

Vision 模型只加载一次，逐页处理，内存峰值 = 一页图片 + 模型本身。
"""
import sys
import json
import fitz


def _init_vision():
    """初始化 Vision 识别请求（只做一次）。"""
    try:
        import Vision
        req = Vision.VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLanguages_(["zh-Hans", "zh-Hant", "en-US"])
        req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        return req
    except Exception as e:
        print(f"[ocr_worker] Vision 初始化失败: {e}", file=sys.stderr)
        return None


def _ocr_png(png_bytes: bytes, req) -> str:
    try:
        import Vision
        import Quartz
        from Foundation import NSData

        ns_data = NSData.dataWithBytes_length_(png_bytes, len(png_bytes))
        src     = Quartz.CGImageSourceCreateWithData(ns_data, None)
        cg_img  = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)

        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg_img, {})
        handler.performRequests_error_([req], None)

        lines = []
        for obs in (req.results() or []):
            cands = obs.topCandidates_(1)
            if cands:
                lines.append(cands[0].string())
        return "\n".join(lines)
    except Exception as e:
        print(f"[ocr_worker] OCR 失败: {e}", file=sys.stderr)
        return ""


def main():
    if len(sys.argv) < 2:
        print('[ocr_worker] 用法: ocr_worker.py <pdf_path> [dpi] [start] [end]', file=sys.stderr)
        sys.exit(1)

    pdf_path   = sys.argv[1]
    dpi        = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    doc        = fitz.open(pdf_path)
    total      = len(doc)
    page_start = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    page_end   = int(sys.argv[4]) if len(sys.argv) > 4 else total

    req = _init_vision()

    for i in range(page_start, min(page_end, total)):
        try:
            pix  = doc[i].get_pixmap(dpi=dpi)
            png  = pix.tobytes("png")
            pix  = None               # 释放渲染缓冲
            text = _ocr_png(png, req) if req else ""
            png  = None               # 释放图片内存
        except Exception as e:
            print(f"[ocr_worker] p{i+1} 失败: {e}", file=sys.stderr)
            text = ""

        # 每页输出一行 JSON，主进程逐行读取写库
        sys.stdout.write(json.dumps({"page": i, "text": text}, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    doc.close()


if __name__ == "__main__":
    main()
