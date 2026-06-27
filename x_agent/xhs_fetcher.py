"""小红书数据抓取层：xhs CLI + EasyOCR 图片文字提取。

依赖：
  - xhs CLI（uv tool install xiaohongshu-cli，需已登录）
  - easyocr（uv pip install easyocr）
"""
from __future__ import annotations

import subprocess
import datetime as dt
import requests
import yaml

from .fetcher import Tweet  # 复用 Tweet 数据结构，统一进入分类/存储流程

TMP_IMG  = "/tmp/xhs_ocr.jpg"
HEADERS  = {"Referer": "https://www.xiaohongshu.com", "User-Agent": "Mozilla/5.0"}
MAX_IMGS = 3   # 每条笔记最多 OCR 几张图

_ocr = None   # 延迟初始化，避免每次 import 都加载模型

def _get_ocr():
    global _ocr
    if _ocr is None:
        import easyocr
        print("[xhs] 加载 OCR 模型（首次约 30s）...")
        _ocr = easyocr.Reader(["ch_sim", "en"], gpu=False, verbose=False)
    return _ocr


def _run_xhs(*args) -> dict:
    result = subprocess.run(["xhs", *args], capture_output=True, text=True)
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
    ocr = _get_ocr()
    texts = []
    for img in image_list[:MAX_IMGS]:
        url = img.get("url_default") or img.get("url_pre") or ""
        if not url:
            continue
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            with open(TMP_IMG, "wb") as f:
                f.write(r.content)
            lines = ocr.readtext(TMP_IMG, detail=0)
            texts.extend(lines)
        except Exception as e:
            print(f"[xhs] OCR 失败: {e}")
    return " ".join(texts)


def _card_to_tweet(note_id: str, card: dict, label: str) -> Tweet:
    user     = card.get("user") or {}
    desc     = card.get("desc") or ""
    title    = card.get("title") or ""
    imgs     = card.get("image_list") or []
    ocr_text = _ocr_images(imgs) if imgs else ""
    text     = " ".join(filter(None, [title, desc, ocr_text]))

    created_at = _parse_time(card.get("last_update_time") or card.get("time") or 0)
    metrics = {
        "liked_count":     card.get("interact_info", {}).get("liked_count", 0),
        "collected_count": card.get("interact_info", {}).get("collected_count", 0),
        "comment_count":   card.get("interact_info", {}).get("comment_count", 0),
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
    data  = _run_xhs("read", note_id)
    items = data.get("data", {}).get("items") or []
    return items[0].get("note_card", {}) if items else {}


class XhsClient:
    """小红书抓取客户端，接口与 OfficialXClient 对齐。"""

    def search(self, query: str, max_results: int, since: dt.datetime, label: str) -> list[Tweet]:
        data  = _run_xhs("search", query, "--sort", "latest")
        items = (data.get("data") or {}).get("items") or []
        results = []
        for item in items[:max_results]:
            note_id = item.get("id") or ""
            if not note_id or "#" in note_id:   # 跳过广告占位
                continue
            card = _read_note(note_id)
            if not card:
                continue
            tw = _card_to_tweet(note_id, card, label)
            if tw.created_at and tw.created_at < since.strftime("%Y-%m-%dT%H:%M:%SZ"):
                continue
            results.append(tw)
        return results

    def user_posts(self, username: str, max_results: int, since: dt.datetime) -> list[Tweet]:
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
