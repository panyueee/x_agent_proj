"""抓取指定账号的所有 following，写入 config.yaml 的 account_groups。"""
import os, time, requests, yaml

TARGET    = "aleabitoreddit"
GROUP_TAG = "serenity_following"
CONFIG    = "config.yaml"
KEY       = os.environ["THIRDPARTY_API_KEY"]
BASE      = "https://api.twitterapi.io"


def fetch_all_followings(username):
    names, cursor = [], None
    page = 0
    while True:
        page += 1
        params = {"userName": username, "count": 200}
        if cursor:
            params["cursor"] = cursor
        time.sleep(6)
        r = requests.get(
            BASE + "/twitter/user/followings",
            headers={"X-API-Key": KEY},
            params=params,
            timeout=20,
        )
        if r.status_code != 200:
            print(f"[warn] HTTP {r.status_code}: {r.text[:200]}")
            break
        data = r.json()
        batch = data.get("followings") or []
        if not batch:
            break
        for u in batch:
            name = u.get("userName") or u.get("screen_name")
            if name:
                names.append(name)
        cursor = data.get("next_cursor") or data.get("nextCursor")
        print(f"第 {page} 页，已获取 {len(names)} 个账号")
        if not cursor:
            break
    return names


print(f"正在抓取 @{TARGET} 的 following 列表 ...")
accounts = fetch_all_followings(TARGET)
print(f"共 {len(accounts)} 个账号")

with open(CONFIG, encoding="utf-8") as f:
    cfg = yaml.safe_load(f)

existing = set(cfg.setdefault("account_groups", {}).get(GROUP_TAG, []))
to_add   = [a for a in accounts if a not in existing]
cfg["account_groups"][GROUP_TAG] = list(existing) + to_add

with open(CONFIG, "w", encoding="utf-8") as f:
    yaml.dump(cfg, f, allow_unicode=True, sort_keys=False, default_flow_style=False)

print(f"新增 {len(to_add)} 个，已写入 {CONFIG}（group: {GROUP_TAG}）")
