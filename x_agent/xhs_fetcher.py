"""小红书数据抓取层：xhs CLI + Apple Vision 图片文字提取。

依赖：
  - xhs CLI（uv tool install xiaohongshu-cli，需已登录）
  - pyobjc-framework-Vision（macOS 内置 OCR，中文支持极佳，无需模型下载）
"""
from __future__ import annotations

import concurrent.futures
import subprocess
import datetime as dt
import requests
import yaml

from .fetcher import Tweet  # 复用 Tweet 数据结构，统一进入分类/存储流程

TMP_IMG  = "/tmp/xhs_ocr.jpg"
HEADERS  = {"Referer": "https://www.xiaohongshu.com", "User-Agent": "Mozilla/5.0"}
MAX_IMGS = 3   # 每条笔记最多 OCR 几张图


def _apple_vision_ocr(image_path: str) -> str:
    """用 macOS 内置 Vision 框架识别图片文字，中英文均支持。"""
    try:
        import Vision
        from Foundation import NSURL
        url = NSURL.fileURLWithPath_(image_path)
        handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, {})
        req = Vision.VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLanguages_(["zh-Hans", "zh-Hant", "en-US"])
        req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        handler.performRequests_error_([req], None)
        results = req.results() or []
        return " ".join(r.topCandidates_(1)[0].string() for r in results if r.topCandidates_(1))
    except Exception as e:
        print(f"[xhs] Apple Vision OCR 失败: {e}")
        return ""


def _run_xhs(*args) -> dict:
    result = subprocess.run(["xhs", *args, "--yaml"], capture_output=True, text=True)
    try:
        return yaml.safe_load(result.stdout) or {}
    except Exception:
        return {}


def _parse_time(ms_timestamp) -> str:
    """毫秒时间戳 → ISO8601 字符串。"""
    try:
        ts = int(ms_timestamp) / 1000
        return dt.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return ""


def _ocr_images(image_list: list) -> str:
    texts = []
    seen_urls = set()
    for img in image_list[:MAX_IMGS]:
        url = img.get("url_default") or img.get("url_pre") or ""
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            with open(TMP_IMG, "wb") as f:
                f.write(r.content)
            text = _apple_vision_ocr(TMP_IMG)
            if text:
                texts.append(text)
        except Exception as e:
            print(f"[xhs] OCR 失败: {e}")
    return " ".join(texts)


def _parse_count(val) -> int:
    """把 '1.2万'、'3k'、123 统一转成整数。"""
    if val is None:
        return 0
    s = str(val).strip().replace(",", "")
    try:
        if "万" in s:
            return int(float(s.replace("万", "")) * 10000)
        if "k" in s.lower():
            return int(float(s.lower().replace("k", "")) * 1000)
        return int(float(s))
    except (ValueError, TypeError):
        return 0


def _card_to_tweet(note_id: str, card: dict, label: str) -> Tweet:
    user  = card.get("user") or {}
    desc  = card.get("desc") or ""
    title = card.get("title") or ""
    imgs  = card.get("image_list") or []

    # 视频笔记的图片通常只是封面缩略图，OCR 意义不大，跳过以节省时间
    if card.get("type") == "video":
        ocr_text = ""
    else:
        ocr_text = _ocr_images(imgs) if imgs else ""

    # 从 tag_list 提取话题词，丰富文本内容供分类器打分
    tags = card.get("tag_list") or []
    tag_text = " ".join(t.get("name", "") for t in tags if t.get("name"))
    text = " ".join(filter(None, [title, desc, ocr_text, tag_text]))

    # last_update_time 为毫秒时间戳；部分笔记用 time 字段；容错处理 0 值
    ts_ms = card.get("last_update_time") or card.get("time") or 0
    try:
        ts_ms = int(ts_ms)
    except (TypeError, ValueError):
        ts_ms = 0
    if ts_ms > 1_000_000_000_000:          # 毫秒级
        created_at = dt.datetime.utcfromtimestamp(ts_ms / 1000).strftime("%Y-%m-%dT%H:%M:%SZ")
    elif ts_ms > 1_000_000_000:            # 秒级（容错）
        created_at = dt.datetime.utcfromtimestamp(ts_ms).strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        created_at = ""

    # 用 _parse_count 统一处理字符串形式的数字（如 "1.2万"、"3k"）
    interact = card.get("interact_info") or {}
    metrics = {
        "liked_count":     _parse_count(interact.get("liked_count")),
        "collected_count": _parse_count(interact.get("collected_count")),
        "comment_count":   _parse_count(interact.get("comment_count")),
    }
    return Tweet(
        id=f"xhs_{note_id}",
        author=user.get("nickname") or user.get("nick_name") or "",
        author_id=user.get("user_id") or "",
        text=text,
        created_at=created_at,
        url=f"https://www.xiaohongshu.com/explore/{note_id}",
        metrics=metrics,
        source_label=label,
        group_tag="xiaohongshu",
    )


def _read_note(note_id: str) -> dict:
    """读取单条笔记详情。若 CLI 返回非 YAML 内容（如登录失效提示），返回空 dict 而不崩溃。"""
    try:
        data  = _run_xhs("read", note_id)
        items = data.get("data", {}).get("items") or []
        return items[0].get("note_card", {}) if items else {}
    except Exception as e:
        print(f"[xhs] _read_note({note_id}) 异常: {e}")
        return {}


class XhsClient:
    """小红书抓取客户端，接口与 OfficialXClient 对齐。"""

    def search(self, query: str, max_results: int, since: dt.datetime, label: str) -> list:
        data  = _run_xhs("search", query, "--sort", "latest")
        items = (data.get("data") or {}).get("items") or []
        results = []
        since_str = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        for item in items[:max_results]:
            note_id = item.get("id") or ""
            if not note_id or "#" in note_id:   # 跳过广告占位
                continue
            # 直接用搜索结果里的 note_card，避免逐条调 xhs read（慢 10x）
            card = dict(item.get("note_card") or {})
            if not card:
                continue
            # 搜索结果用 display_title 而非 title，统一映射
            if not card.get("title") and card.get("display_title"):
                card["title"] = card["display_title"]
            tw = _card_to_tweet(note_id, card, label)
            if tw.created_at and tw.created_at < since_str:
                continue
            results.append(tw)
        return results

    def user_posts(self, username: str, max_results: int, since: dt.datetime) -> list:
        data  = _run_xhs("user-posts", username)
        items = (data.get("data") or {}).get("items") or []
        results = []
        for item in items[:max_results]:
            note_id = item.get("id") or ""
            if not note_id or "#" in note_id:
                continue
            card = _read_note(note_id)
            if not card:
                continue
            tw = _card_to_tweet(note_id, card, f"@{username}")
            if tw.created_at and tw.created_at < since.strftime("%Y-%m-%dT%H:%M:%SZ"):
                continue
            results.append(tw)
        return results
