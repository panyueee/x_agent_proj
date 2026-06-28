"""小红书数据抓取层 —— 直接调用小红书 Web API（借鉴 MediaCrawler 设计思路）。

【Cookie 获取方式】
1. 用浏览器打开 https://www.xiaohongshu.com 并登录
2. 按 F12 打开开发者工具 → Network（网络）标签
3. 随意点击一个笔记或刷新页面，在请求列表里找任意一条对 xiaohongshu.com 的请求
4. 在请求头（Request Headers）里找到 "Cookie:" 那一行，复制整段值
5. 在终端执行：export XHS_COOKIE='<粘贴的 cookie 字符串>'
6. 之后运行 python main.py 即可

Cookie 有效期约 30 天，过期后重新按以上步骤获取并重新 export。

【X-Sign 签名说明】
小红书 API 请求需要 x-s / x-t 等签名字段。MediaCrawler 通过 Playwright 注入本地 JS
来生成签名（sign_with_xhshow）。本模块采用相同思路：若环境变量 XHS_SIGN_JS 指向签名
脚本，则用 subprocess 调用 Node.js 生成签名；否则退回到无签名模式（仅搜索接口可用）。

【借鉴 MediaCrawler 的机制】
- Cookie 字符串注入到请求头，通过 pong 接口验证登录态
- 错误码区分：登录失效 / IP 封锁 / 笔记不存在 / 被限流
- 指数退避重试（最多 3 次，限流时退避更久）
- 互动数过滤（点赞+收藏+评论 < 5 直接丢弃）
- metrics 里补充 like_count 和 collect_count
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import time

import requests

from .fetcher import Tweet  # 复用 Tweet 数据结构

# ---- 常量 ----
_BASE_URL = "https://www.xiaohongshu.com"
_API_BASE  = "https://edith.xiaohongshu.com"   # XHS 移动端 / Web API 网关

# 错误码（参考 MediaCrawler 注释）
_CODE_NOT_LOGGED_IN   = -1                 # 未登录或 Cookie 失效（可能因平台而异）
_CODE_IP_BLOCK        = 300012             # IP 被封禁
_CODE_NOTE_NOT_FOUND_LIST = (-510000, -510001)  # 笔记不存在 / 内容异常
_CODE_CAPTCHA_LIST    = (461, 471)         # 触发验证码

# 最小互动数阈值（点赞+收藏+评论，低于此值丢弃）
_MIN_INTERACTION = 5

# 请求间隔（秒）：避免触发限流
_MIN_INTERVAL = 2.0

# 重试参数
_MAX_RETRY = 3
_RETRY_BASE_DELAY = 5  # 秒，指数退避基础


# ---- 自定义异常 ----

class XhsError(Exception):
    """小红书抓取通用异常。"""
    pass


class XhsCookieExpiredError(XhsError):
    """Cookie 失效或未登录。"""
    pass


class XhsRateLimitError(XhsError):
    """被限流。"""
    pass


class XhsIPBlockError(XhsError):
    """IP 被封禁。"""
    pass


# ---- 工具函数 ----

def _parse_count(val):
    # type: (object) -> int
    """把 '1.2万'、'3k'、'123' 统一转成整数。"""
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


def _parse_time(ts_ms):
    # type: (object) -> str
    """毫秒时间戳 → ISO8601 字符串（UTC）。"""
    try:
        ts = int(ts_ms)
    except (TypeError, ValueError):
        return ""
    if ts > 1_000_000_000_000:       # 毫秒级
        ts = ts / 1000
    elif ts > 1_000_000_000:         # 秒级（容错）
        pass
    else:
        return ""
    return dt.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_cookie():
    # type: () -> str
    """从环境变量 XHS_COOKIE 读取 cookie 字符串。"""
    cookie = os.environ.get("XHS_COOKIE", "").strip()
    if not cookie:
        print(
            "[xhs] 警告：未设置 XHS_COOKIE 环境变量。\n"
            "      请先登录小红书，用 F12 复制 Cookie 头，然后：\n"
            "      export XHS_COOKIE='<your cookie string>'"
        )
    return cookie


def _build_headers(cookie):
    # type: (str) -> dict
    """构建请求头，注入 Cookie（参考 MediaCrawler 的头部设置）。"""
    return {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer":          "https://www.xiaohongshu.com/",
        "Origin":           "https://www.xiaohongshu.com",
        "Accept":           "application/json, text/plain, */*",
        "Accept-Language":  "zh-CN,zh;q=0.9,en;q=0.8",
        "Content-Type":     "application/json;charset=UTF-8",
        "Cookie":           cookie,
    }


def _try_get_sign(uri, data, cookie):
    # type: (str, object, str) -> dict
    """
    尝试通过外部 Node.js 脚本生成小红书签名字段（x-s / x-t 等）。

    若环境变量 XHS_SIGN_JS 未指向有效脚本，返回空 dict（无签名模式）。
    MediaCrawler 使用 sign_with_xhshow() 函数注入 Playwright 页面生成签名，
    此处简化为调用独立 Node.js 脚本，与 Playwright 版本思路一致。

    脚本约定：
      node $XHS_SIGN_JS <uri> <data_json> <cookie>
      stdout 输出 JSON，包含 {"x-s": "...", "x-t": "...", ...}
    """
    sign_js = os.environ.get("XHS_SIGN_JS", "").strip()
    if not sign_js or not os.path.isfile(sign_js):
        return {}
    try:
        data_str = json.dumps(data, ensure_ascii=False) if data else ""
        result = subprocess.run(
            ["node", sign_js, uri, data_str, cookie],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout.strip())
    except Exception as e:
        print(f"[xhs] 签名脚本调用失败（非致命）: {e}")
    return {}


def _check_response(resp_json, context=""):
    # type: (dict, str) -> None
    """
    检查 API 响应，按错误码抛出对应异常（借鉴 MediaCrawler 的错误码分类）。

    - Cookie 失效 / 未登录 → XhsCookieExpiredError（打印明确提示）
    - IP 封禁              → XhsIPBlockError
    - 验证码触发           → XhsRateLimitError（视作限流）
    - 其他错误             → XhsError
    """
    code = resp_json.get("code", 0)
    msg  = resp_json.get("msg") or resp_json.get("message") or ""

    if code == 0 or code is None:
        return  # 正常

    if code in _CODE_CAPTCHA_LIST:
        raise XhsRateLimitError(f"[xhs] 触发验证码（code={code}），已被限流 {context}")

    if code == _CODE_IP_BLOCK:
        raise XhsIPBlockError(f"[xhs] IP 被封禁（code={code}），请更换网络或代理 {context}")

    if code in _CODE_NOTE_NOT_FOUND_LIST:
        # 笔记不存在属于正常情况，静默跳过
        raise XhsError(f"[xhs] 笔记不存在或内容异常（code={code}）{context}")

    # 若返回登录相关错误（-1、"未登录"等），提示更换 Cookie
    login_hints = ("login", "auth", "未登录", "请登录", "session", "unauthorized")
    if code == _CODE_NOT_LOGGED_IN or any(h in msg.lower() for h in login_hints):
        print(
            f"[xhs] Cookie 已失效！（code={code}, msg={msg!r}）\n"
            "      请重新登录小红书，复制新的 Cookie 并重新 export XHS_COOKIE=..."
        )
        raise XhsCookieExpiredError(f"Cookie 失效 code={code}")

    # 其他错误
    raise XhsError(f"[xhs] API 错误 code={code}, msg={msg!r} {context}")


def _card_to_tweet(note_id, card, label):
    # type: (str, dict, str) -> Tweet
    """把小红书 note_card 映射到统一的 Tweet 数据结构。"""
    user   = card.get("user") or {}
    desc   = card.get("desc") or ""
    title  = card.get("title") or ""

    # 话题标签
    tags     = card.get("tag_list") or []
    tag_text = " ".join(t.get("name", "") for t in tags if t.get("name"))
    text     = " ".join(filter(None, [title, desc, tag_text]))

    # 时间戳
    ts_ms      = card.get("last_update_time") or card.get("time") or 0
    created_at = _parse_time(ts_ms)

    # 互动数（参考 MediaCrawler 字段命名，同时保留 like_count 别名）
    interact = card.get("interact_info") or {}
    like_count    = _parse_count(interact.get("liked_count"))
    collect_count = _parse_count(interact.get("collected_count"))
    comment_count = _parse_count(interact.get("comment_count"))

    metrics = {
        "liked_count":     like_count,
        "collected_count": collect_count,
        "comment_count":   comment_count,
        # 以下两个别名与 Twitter 端统一，方便分类器使用
        "like_count":      like_count,
        "collect_count":   collect_count,
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


def _interaction_total(metrics):
    # type: (dict) -> int
    """计算总互动数（点赞 + 收藏 + 评论）。"""
    return (
        metrics.get("liked_count", 0)
        + metrics.get("collected_count", 0)
        + metrics.get("comment_count", 0)
    )


# ---- 核心 HTTP 客户端 ----

class _XhsHttpClient:
    """
    小红书 Web API 的底层 HTTP 封装。

    借鉴 MediaCrawler 的 XiaoHongShuClient：
    - Cookie 注入请求头（不走浏览器，直接 HTTP）
    - 可选签名（通过外部 Node.js 脚本生成 x-s / x-t）
    - 错误码分类处理
    - 指数退避重试（最多 _MAX_RETRY 次）
    """

    def __init__(self):
        # type: () -> None
        self._cookie      = _load_cookie()
        self._session     = requests.Session()
        self._last_call   = 0.0
        self._update_headers()

    def _update_headers(self):
        # type: () -> None
        """刷新请求头中的 Cookie（cookie 失效后可调用此方法更新）。"""
        self._session.headers.update(_build_headers(self._cookie))

    def refresh_cookie(self, new_cookie):
        # type: (str) -> None
        """动态刷新 Cookie（运行时调用，无需重启）。"""
        self._cookie = new_cookie.strip()
        self._update_headers()
        print("[xhs] Cookie 已刷新。")

    def _throttle(self):
        # type: () -> None
        """简单节流：保证两次请求间隔不低于 _MIN_INTERVAL 秒。"""
        wait = _MIN_INTERVAL - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

    def _do_request(self, method, path, params=None, json_body=None):
        # type: (str, str, dict, object) -> dict
        """
        发送单次 HTTP 请求，注入签名头，返回解析后的 JSON。

        不含重试逻辑（由 _request 包装）。
        """
        url = _API_BASE + path

        # 尝试生成签名头（无签名时跳过，对部分接口仍可用）
        sign_headers = _try_get_sign(path, json_body, self._cookie)
        if sign_headers:
            self._session.headers.update(sign_headers)

        if method.upper() == "GET":
            resp = self._session.get(url, params=params, timeout=30)
        else:
            body_str = json.dumps(json_body, separators=(",", ":"), ensure_ascii=False)
            resp = self._session.post(url, data=body_str, timeout=30)

        resp.raise_for_status()
        return resp.json()

    def _request(self, method, path, params=None, json_body=None):
        # type: (str, str, dict, object) -> dict
        """
        带节流、错误分类、指数退避重试的 HTTP 请求。

        - 限流（XhsRateLimitError）：指数退避后重试，最多 _MAX_RETRY 次
        - Cookie 失效 / IP 封禁：直接上抛，不重试
        - 网络错误：退避重试，最多 _MAX_RETRY 次
        """
        for attempt in range(_MAX_RETRY + 1):
            self._throttle()
            try:
                data = self._do_request(method, path, params=params, json_body=json_body)
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout) as exc:
                if attempt >= _MAX_RETRY:
                    raise XhsError(f"网络错误，已重试 {_MAX_RETRY} 次：{exc}") from exc
                delay = _RETRY_BASE_DELAY * (2 ** attempt)
                print(f"[xhs] 网络错误（第 {attempt + 1} 次），{delay}s 后重试：{exc}")
                time.sleep(delay)
                continue
            except requests.exceptions.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else 0
                if status == 429:
                    if attempt >= _MAX_RETRY:
                        raise XhsRateLimitError("被限流且重试次数已耗尽") from exc
                    delay = _RETRY_BASE_DELAY * (2 ** attempt)
                    print(f"[xhs] HTTP 429 限流（第 {attempt + 1} 次），{delay}s 后重试")
                    time.sleep(delay)
                    continue
                raise XhsError(f"HTTP 错误 {status}: {exc}") from exc

            # 检查业务层错误码
            try:
                _check_response(data, context=f"path={path}")
            except XhsRateLimitError:
                if attempt >= _MAX_RETRY:
                    raise
                delay = _RETRY_BASE_DELAY * (2 ** attempt)
                print(f"[xhs] 被限流（第 {attempt + 1} 次），{delay}s 后重试")
                time.sleep(delay)
                continue
            except (XhsCookieExpiredError, XhsIPBlockError):
                raise   # 这两类错误不重试，直接上抛
            except XhsError:
                raise   # 其他业务错误也直接上抛

            return data

        raise XhsError(f"请求 {path} 在 {_MAX_RETRY} 次重试后仍失败")

    # ---- 业务接口 ----

    def pong(self):
        # type: () -> bool
        """
        验证 Cookie 是否有效（参考 MediaCrawler 的 pong() 方法）。

        请求"我的"用户信息接口；若抛 XhsCookieExpiredError 则返回 False。
        """
        try:
            self._request("GET", "/api/sns/web/v1/user/selfinfo")
            return True
        except XhsCookieExpiredError:
            return False
        except XhsError:
            # 其他错误（如网络超时）不能断定 Cookie 失效
            return True

    def search_notes(self, keyword, page=1, page_size=20, sort="general"):
        # type: (str, int, int, str) -> dict
        """
        关键词搜索笔记（POST /api/sns/web/v1/search/notes）。

        借鉴 MediaCrawler 的 get_note_by_keyword() 实现：
        - 请求体为 JSON（separators 不带空格，与浏览器行为一致）
        - sort: "general"（综合）/ "time_descending"（最新）/ "popularity_descending"（最热）
        """
        payload = {
            "keyword":   keyword,
            "page":      page,
            "page_size": page_size,
            "search_id": "",
            "sort":      sort,
            "note_type": 0,   # 0=全部；1=视频；2=图文
        }
        return self._request("POST", "/api/sns/web/v1/search/notes", json_body=payload)

    def get_user_notes(self, user_id, cursor=""):
        # type: (str, str) -> dict
        """
        获取指定用户的笔记列表（GET /api/sns/web/v1/user_posted）。

        借鉴 MediaCrawler 的游标翻页设计（has_more + cursor）。
        """
        params = {
            "user_id": user_id,
            "cursor":  cursor,
            "num":     30,
            "image_formats": "jpg,webp,avif",
        }
        return self._request("GET", "/api/sns/web/v1/user_posted", params=params)

    def get_note_detail(self, note_id, xsec_token="", xsec_source="pc_search"):
        # type: (str, str, str) -> dict
        """
        获取笔记详情（POST /api/sns/web/v1/feed）。

        MediaCrawler 通过 xsec_token + xsec_source 访问受保护笔记，
        此处保留这两个字段作为可选参数。
        """
        payload = {
            "source_note_id": note_id,
            "image_formats":  ["jpg", "webp", "avif"],
            "extra":          {"need_body_topic": "1"},
            "xsec_source":    xsec_source,
            "xsec_token":     xsec_token,
        }
        return self._request("POST", "/api/sns/web/v1/feed", json_body=payload)


# ---- 结果解析 ----

def _extract_note_id(item):
    # type: (dict) -> str
    """从搜索结果条目中提取 note_id，跳过广告占位（含 # 的 id）。"""
    note_id = ""
    # 搜索结果条目通常包含 id 或 note_id
    note_id = item.get("id") or item.get("note_id") or ""
    # note_card 里也可能有
    if not note_id:
        card = item.get("note_card") or {}
        note_id = card.get("note_id") or ""
    if "#" in str(note_id):
        return ""   # 广告占位
    return str(note_id)


def _parse_search_items(resp_data):
    # type: (dict) -> list
    """从搜索 API 响应中提取笔记条目列表。"""
    data   = resp_data.get("data") or {}
    items  = data.get("items") or []
    return items


def _parse_user_notes_items(resp_data):
    # type: (dict) -> tuple
    """
    从用户笔记 API 响应中提取 (items, cursor, has_more)。

    返回 tuple: (list[dict], str, bool)
    """
    data     = resp_data.get("data") or {}
    items    = data.get("notes") or data.get("items") or []
    cursor   = data.get("cursor") or ""
    has_more = bool(data.get("has_more", False))
    return items, cursor, has_more


def _note_card_from_item(item):
    # type: (dict) -> dict
    """从搜索/用户条目中取出 note_card（兼容不同 API 字段）。"""
    return item.get("note_card") or item


def _note_card_from_detail(resp_data):
    # type: (dict) -> dict
    """从 feed 详情响应中取出 note_card。"""
    data  = resp_data.get("data") or {}
    items = data.get("items") or []
    if not items:
        return {}
    return items[0].get("note_card") or {}


# ---- 公开客户端 ----

class XhsClient:
    """
    小红书抓取客户端，接口与 OfficialXClient / ThirdPartyXClient 对齐。

    改进点（相对旧版 xhs CLI 方案）：
    1. 直接调用 XHS Web API，不依赖 xhs CLI 工具
    2. Cookie 从环境变量 XHS_COOKIE 读取，支持运行时 refresh_cookie() 刷新
    3. 区分 Cookie 失效 / IP 封禁 / 限流，打印不同提示
    4. 限流时指数退避重试（最多 3 次）
    5. 互动数 < 5 的帖子直接丢弃
    6. metrics 包含 like_count / collect_count 别名
    """

    def __init__(self):
        # type: () -> None
        self._http = _XhsHttpClient()

    def refresh_cookie(self, cookie):
        # type: (str) -> None
        """运行时动态刷新 Cookie（无需重启进程）。"""
        self._http.refresh_cookie(cookie)

    def check_login(self):
        # type: () -> bool
        """检查当前 Cookie 是否有效（True = 已登录）。"""
        return self._http.pong()

    def search(self, query, max_results, since, label):
        # type: (str, int, dt.datetime, str) -> list
        """
        关键词搜索，返回 list[Tweet]。

        - 按"最新"排序，优先获取近期内容
        - 互动数 < _MIN_INTERACTION 的帖子丢弃
        - 早于 since 的帖子丢弃
        """
        since_str = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        results   = []
        page      = 1

        while len(results) < max_results:
            try:
                resp = self._http.search_notes(
                    keyword=query, page=page, page_size=20, sort="time_descending"
                )
            except XhsCookieExpiredError:
                # Cookie 失效，_check_response 已打印提示，直接终止本轮搜索
                break
            except XhsIPBlockError as e:
                print(f"[xhs] search({query!r}) 中断：{e}")
                break
            except XhsError as e:
                print(f"[xhs] search({query!r}) 错误：{e}")
                break

            items = _parse_search_items(resp)
            if not items:
                break

            found_old = False
            for item in items:
                note_id = _extract_note_id(item)
                if not note_id:
                    continue

                card = _note_card_from_item(item)

                # 尝试用详情 API 补充更多字段（互动数等），失败则用列表数据
                try:
                    detail_resp = self._http.get_note_detail(note_id)
                    detail_card = _note_card_from_detail(detail_resp)
                    if detail_card:
                        card = detail_card
                except XhsError:
                    pass   # 详情获取失败则直接用列表数据

                tw = _card_to_tweet(note_id, card, label)

                # 时间过滤
                if tw.created_at and tw.created_at < since_str:
                    found_old = True
                    continue

                # 互动数过滤（点赞+收藏+评论 < 5 丢弃）
                if _interaction_total(tw.metrics) < _MIN_INTERACTION:
                    continue

                results.append(tw)
                if len(results) >= max_results:
                    break

            # 如果本页有早于 since 的帖子，说明更老的内容也不需要，停止翻页
            if found_old:
                break

            # 搜索 API 无游标，只能按页数翻页；最多翻 5 页避免过量请求
            page += 1
            if page > 5:
                break

        return results

    def user_posts(self, username, max_results, since):
        # type: (str, int, dt.datetime) -> list
        """
        获取指定用户的近期笔记，返回 list[Tweet]。

        username 可以是小红书用户名或用户数字 ID（以 user_ 开头）。
        若平台不支持用户名直接查询，需先通过搜索或其他途径获取 user_id。
        """
        since_str = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        results   = []
        cursor    = ""
        label     = f"@{username}"

        # 若 username 以 "user_" 开头，认为是 user_id；否则尝试用搜索定位
        if username.startswith("user_") or username.isdigit():
            user_id = username
        else:
            user_id = self._resolve_user_id(username)
            if not user_id:
                print(f"[xhs] 无法解析用户名 {username!r} 的 user_id，跳过")
                return []

        while len(results) < max_results:
            try:
                resp = self._http.get_user_notes(user_id, cursor=cursor)
            except XhsCookieExpiredError:
                break
            except XhsIPBlockError as e:
                print(f"[xhs] user_posts({username!r}) 中断：{e}")
                break
            except XhsError as e:
                print(f"[xhs] user_posts({username!r}) 错误：{e}")
                break

            items, new_cursor, has_more = _parse_user_notes_items(resp)
            if not items:
                break

            found_old = False
            for item in items:
                note_id = _extract_note_id(item)
                if not note_id:
                    continue

                card = _note_card_from_item(item)
                tw   = _card_to_tweet(note_id, card, label)

                if tw.created_at and tw.created_at < since_str:
                    found_old = True
                    continue

                if _interaction_total(tw.metrics) < _MIN_INTERACTION:
                    continue

                results.append(tw)
                if len(results) >= max_results:
                    break

            if found_old or not has_more:
                break

            cursor = new_cursor

        return results

    def _resolve_user_id(self, username):
        # type: (str) -> str
        """
        通过关键词搜索尝试解析用户名对应的 user_id。

        这是 XHS Web API 的局限：无官方"用户名 → ID"接口，
        只能通过搜索结果里的 user 字段反查。
        """
        try:
            resp  = self._http.search_notes(keyword=username, page=1, page_size=5)
            items = _parse_search_items(resp)
            for item in items:
                card = _note_card_from_item(item)
                user = card.get("user") or {}
                uid  = user.get("user_id") or ""
                nick = user.get("nickname") or user.get("nick_name") or ""
                # 模糊匹配：昵称包含 username 或完全一致
                if uid and (nick == username or username.lower() in nick.lower()):
                    return uid
        except XhsError as e:
            print(f"[xhs] _resolve_user_id({username!r}) 失败：{e}")
        return ""
