import os, requests, json, time

key = os.environ["THIRDPARTY_API_KEY"]
base = "https://api.twitterapi.io"

# 先确认端点可用
time.sleep(6)
r = requests.get(
    base + "/twitter/user/followings",
    headers={"X-API-Key": key},
    params={"userName": "elonmusk", "count": 5},
    timeout=15,
)
print(r.status_code)
print(json.dumps(r.json(), indent=2, ensure_ascii=False)[:1000])
