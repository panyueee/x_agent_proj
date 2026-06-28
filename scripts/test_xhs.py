"""测试小红书抓取 + PaddleOCR 图片文字提取。"""
import subprocess, yaml, os, requests
import easyocr

QUERY  = "BTC 交易策略"
LIMIT  = 10
TMP    = "/tmp/xhs_img.jpg"
HEADERS = {"Referer": "https://www.xiaohongshu.com", "User-Agent": "Mozilla/5.0"}

print("加载 OCR 模型（首次需下载，约 200MB）...")
ocr = easyocr.Reader(["ch_sim", "en"], gpu=False, verbose=False)


def fetch_notes(query, limit):
    result = subprocess.run(
        ["xhs", "search", query, "--sort", "latest"],
        capture_output=True, text=True
    )
    data = yaml.safe_load(result.stdout)
    items = data.get("data", {}).get("items", [])
    return items[:limit]


def read_note(note_id):
    result = subprocess.run(
        ["xhs", "read", note_id],
        capture_output=True, text=True
    )
    data = yaml.safe_load(result.stdout)
    items = data.get("data", {}).get("items", [])
    return items[0].get("note_card", {}) if items else {}


def get_image_urls(note_card):
    urls = []
    for img in note_card.get("image_list", []):
        url = img.get("url_default") or img.get("url_pre")
        if url:
            urls.append(url)
    return urls[:3]


def extract_text_from_images(image_urls):
    texts = []
    for url in image_urls:
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            with open(TMP, "wb") as f:
                f.write(r.content)
            result = ocr.readtext(TMP, detail=0)
            texts.extend(result)
        except Exception as e:
            texts.append(f"[OCR失败: {e}]")
    return " ".join(texts)


def main():
    print(f"搜索「{QUERY}」前 {LIMIT} 条笔记...\n")
    notes = fetch_notes(QUERY, LIMIT)
    print(f"获取到 {len(notes)} 条\n{'='*60}")

    for i, item in enumerate(notes, 1):
        note_id   = item.get("id", "")
        base_card = item.get("note_card", {})
        print(f"[{i}/10] 读取 {note_id} ...", flush=True)
        card = read_note(note_id) or base_card

        title     = card.get("title") or base_card.get("display_title") or "(无标题)"
        desc      = card.get("desc", "")
        author    = card.get("user", {}).get("nickname", "")
        note_type = card.get("type", "")
        img_urls  = get_image_urls(card)

        print(f"[{i}] @{author} | type={note_type}")
        print(f"    标题: {title[:60]}")
        if desc:
            print(f"    描述: {desc[:100]}")
        print(f"    图片: {len(img_urls)} 张")

        if img_urls:
            print("    OCR提取中...", end="", flush=True)
            ocr_text = extract_text_from_images(img_urls)
            print(f"\n    图文: {ocr_text[:300]}")
        print()


if __name__ == "__main__":
    main()
