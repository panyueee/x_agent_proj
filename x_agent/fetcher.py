"""X (Twitter) 数据抓取层。

统一接口，默认实现走官方 X API v2（按量付费）。
想换更便宜的第三方数据 API，只要实现同样的 user_tweets / search 方法即可。
"""
from __future__ import annotations

import time
import datetime as dt
from dataclasses import dataclass, field

import requests


@dataclass
class Tweet:
    id: str
    author: str
    author_id: str
    text: str
    created_at: str
    url: str
    metrics: dict = field(default_factory=dict)
    source_label: str = ""   # 来自哪个账号或哪条搜索
    group_tag: str = ""      # 账号分组标签，如 serenity_following


class XClientError(Exception):
    pass


class OfficialXClient:
    """官方 X API v2 客户端（pay-per-use）。"""

    BASE = "https://api.x.com/2"

    def __init__(self, bearer_token: str, min_interval: float = 1.2):
        if not bearer_token:
            raise XClientError("缺少 X_BEARER_TOKEN，请在环境变量里配置")
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {bearer_token}"
        self.min_interval = min_interval        # 简单节流，避免打满限流
        self._last_call = 0.0
        self._user_cache: dict[str, str] = {}

    # ---- 底层请求：节流 + 429 退避 ----
    def _get(self, path: str, params: dict) -> dict:
        wait = self.min_interval - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        for attempt in range(5):
            r = self.session.get(f"{self.BASE}{path}", params=params, timeout=30)
            self._last_call = time.time()
            if r.status_code == 429:
                reset = int(r.headers.get("x-rate-limit-reset", "0") or 0)
                sleep_for = max(reset - time.time(), 15) if reset else 15 * (attempt + 1)
                print(f"[X] 触发限流，等待 {sleep_for:.0f}s ...")
                time.sleep(sleep_for)
                continue
            if r.status_code >= 400:
                raise XClientError(f"HTTP {r.status_code}: {r.text[:300]}")
            return r.json()
        raise XClientError("重试多次仍被限流，已放弃本次请求")

    # ---- 把 handle 解析成 user id（带缓存，省读取费用）----
    def resolve_user(self, username: str) -> str:
        username = username.lstrip("@")
        if username in self._user_cache:
            return self._user_cache[username]
        data = self._get(f"/users/by/username/{username}", {"user.fields": "id"})
        uid = data.get("data", {}).get("id")
        if not uid:
            raise XClientError(f"找不到用户 @{username}")
        self._user_cache[username] = uid
        return uid

    # ---- 拉某个账号的近期推文 ----
    def user_tweets(self, username: str, max_results: int, since: dt.datetime) -> list[Tweet]:
        uid = self.resolve_user(username)
        params = {
            "max_results": min(max(max_results, 5), 100),
            "tweet.fields": "created_at,public_metrics",
            "exclude": "retweets,replies",
            "start_time": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        data = self._get(f"/users/{uid}/tweets", params)
        return [
            self._tweet_from(t, author=username, author_id=uid, label=f"@{username}")
            for t in data.get("data", [])
        ]

    # ---- 关键词搜索（recent search，近 7 天）----
    def search(self, query: str, max_results: int, since: dt.datetime, label: str) -> list[Tweet]:
        params = {
            "query": query,
            "max_results": min(max(max_results, 10), 100),
            "tweet.fields": "created_at,public_metrics,author_id",
            "expansions": "author_id",
            "user.fields": "username",
            "start_time": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        data = self._get("/tweets/search/recent", params)
        users = {u["id"]: u["username"] for u in data.get("includes", {}).get("users", [])}
        out = []
        for t in data.get("data", []):
            author = users.get(t.get("author_id"), "")
            out.append(self._tweet_from(t, author=author, author_id=t.get("author_id", ""), label=label))
        return out

    @staticmethod
    def _tweet_from(t: dict, author: str, author_id: str, label: str) -> Tweet:
        url = (
            f"https://x.com/{author}/status/{t['id']}"
            if author else f"https://x.com/i/status/{t['id']}"
        )
        return Tweet(
            id=t["id"],
            author=author,
            author_id=author_id,
            text=t.get("text", ""),
            created_at=t.get("created_at", ""),
            url=url,
            metrics=t.get("public_metrics", {}),
            source_label=label,
        )


class ThirdPartyXClient:
    """第三方数据 API 适配器 —— 默认对接 twitterapi.io。

    如需换供应商，只需修改 _get() 中的请求头鉴权方式，
    以及 _tweet_from_raw() 中的字段映射，接口签名不变。

    twitterapi.io 文档：https://docs.twitterapi.io
    注册后在控制台取得 API Key，按量计费，比官方 API 便宜。
    """

    # ---- 若换供应商，改这里的端点路径 ----
    _ENDPOINTS = {
        "user_tweets": "/twitter/user/tweets",       # GET ?userName=&count=&cursor=
        "search":      "/twitter/tweet/advanced_search",  # GET ?query=&queryType=Latest&cursor=
    }

    def __init__(self, base_url: str, api_key: str, min_interval: float = 6.0):
        if not (base_url and api_key):
            raise XClientError("第三方 API 需要 base_url 和 api_key，请检查环境变量")
        self.base_url = base_url.rstrip("/")
        self.min_interval = min_interval
        self._last_call = 0.0
        self.session = requests.Session()
        # twitterapi.io 用 X-API-Key；若换供应商按其文档改鉴权头
        self.session.headers.update({
            "X-API-Key": api_key,
            "Accept": "application/json",
        })
        # 本次运行内的不活跃账号缓存（每次进程重启自动清零）
        self.inactive_accounts = set()  # type: ignore[type-arg]

    def _get(self, endpoint: str, params: dict) -> dict:
        """带节流与 429 退避的 GET 请求。"""
        wait = self.min_interval - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        url = self.base_url + endpoint
        for attempt in range(5):
            r = self.session.get(url, params=params, timeout=30)
            self._last_call = time.time()
            if r.status_code == 429:
                sleep_for = 15 * (attempt + 1)
                reset = r.headers.get("x-rate-limit-reset")
                if reset:
                    sleep_for = max(int(reset) - time.time(), sleep_for)
                print(f"[3rd] 限流，等待 {sleep_for:.0f}s ...")
                time.sleep(sleep_for)
                continue
            if r.status_code >= 400:
                raise XClientError(f"HTTP {r.status_code}: {r.text[:300]}")
            return r.json()
        raise XClientError("重试多次仍被限流，已放弃本次请求")

    @staticmethod
    def _parse_created_at(raw: str) -> str:
        """把供应商返回的各种时间格式统一成 ISO8601 字符串。"""
        if not raw:
            return ""
        # twitterapi.io 返回 "Mon Jan 01 00:00:00 +0000 2024"
        for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                return dt.datetime.strptime(raw, fmt).strftime("%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                continue
        return raw  # 无法解析则原样保留

    @staticmethod
    def _tweet_from_raw(t: dict, label: str) -> Tweet:
        """把供应商原始字段映射成内部 Tweet。

        twitterapi.io 字段：id / text / createdAt / author.id / author.userName
                           viewCount / likeCount / retweetCount / replyCount
        若换供应商，只需在这里调整字段名。
        """
        author_obj = t.get("author") or {}
        author = author_obj.get("userName") or author_obj.get("username") or ""
        author_id = author_obj.get("id") or t.get("author_id") or ""
        tweet_id = str(t.get("id") or t.get("tweet_id") or "")
        url = t.get("url") or (
            f"https://x.com/{author}/status/{tweet_id}" if author else
            f"https://x.com/i/status/{tweet_id}"
        )
        metrics = {
            "like_count":    t.get("likeCount")    or t.get("favorite_count") or 0,
            "retweet_count": t.get("retweetCount") or t.get("retweet_count")  or 0,
            "reply_count":   t.get("replyCount")   or t.get("reply_count")    or 0,
            "view_count":    t.get("viewCount")    or t.get("views")          or 0,
        }
        return Tweet(
            id=tweet_id,
            author=author,
            author_id=str(author_id),
            text=t.get("text") or t.get("full_text") or "",
            created_at=ThirdPartyXClient._parse_created_at(t.get("createdAt") or t.get("created_at") or ""),
            url=url,
            metrics=metrics,
            source_label=label,
        )

    def _is_after(self, tweet: Tweet, since: dt.datetime) -> bool:
        """过滤早于 since 的推文（第三方 API 通常不支持 start_time 参数）。"""
        if not tweet.created_at:
            return True
        try:
            ts = dt.datetime.strptime(tweet.created_at, "%Y-%m-%dT%H:%M:%SZ")
            return ts >= since
        except ValueError:
            return True

    def user_tweets(self, username: str, max_results: int, since: dt.datetime) -> list[Tweet]:
        username = username.lstrip("@")
        params = {"userName": username, "count": min(max_results, 100)}
        data = self._get(self._ENDPOINTS["user_tweets"], params)
        # twitterapi.io 在 data.tweets 或顶层 tweets 里返回列表
        raw_list = data.get("tweets") or data.get("data", {}).get("tweets") or []
        results = []
        has_any = False   # API 是否返回了推文（不含转发）
        for t in raw_list:
            # 跳过转发（isRetweet）
            if t.get("isRetweet") or t.get("is_retweet"):
                continue
            has_any = True
            tw = self._tweet_from_raw(t, label=f"@{username}")
            if self._is_after(tw, since):
                results.append(tw)

        # 若 API 有返回推文，但全部早于 since，说明该账号近期不活跃，
        # 加入本次运行的内存黑名单，后续调用可直接跳过。
        if has_any and not results:
            self.inactive_accounts.add(username)
            print(f"[fetcher] @{username} 近期无新推文，已标记为不活跃，本轮跳过")

        return results[:max_results]

    def search(self, query: str, max_results: int, since: dt.datetime, label: str) -> list[Tweet]:
        params = {
            "query":     query,
            "queryType": "Latest",   # 按时间倒序
            "count":     min(max_results, 100),
        }
        data = self._get(self._ENDPOINTS["search"], params)
        raw_list = data.get("tweets") or data.get("data", {}).get("tweets") or []
        results = []
        for t in raw_list:
            if t.get("isRetweet") or t.get("is_retweet"):
                continue
            tw = self._tweet_from_raw(t, label=label)
            if self._is_after(tw, since):
                results.append(tw)
        return results[:max_results]


def build_client(cfg: dict):
    provider = cfg["x_api"].get("provider", "official")
    if provider == "official":
        return OfficialXClient(cfg["x_api"]["bearer_token"])
    if provider == "thirdparty":
        tp = cfg["x_api"]["thirdparty"]
        return ThirdPartyXClient(tp["base_url"], tp["api_key"])
    raise XClientError(f"未知的 provider: {provider}")
