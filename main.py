"""主程序：加载配置 → 并行抓取 → 分类 → 入库 → 生成摘要，可定时循环。

用法：
    python main.py [--source x|xhs|tgb|finance|all] [config.yaml]

    --source x       只抓 X (Twitter)
    --source xhs     只抓小红书
    --source tgb     只抓淘股吧
    --source finance 只抓金融行情（A股/美股/加密货币）
    --source all     四路并行（默认）

环境变量：
    THIRDPARTY_API_KEY  第三方 X API
    ANTHROPIC_API_KEY   仅 use_llm: true 时需要
"""
from __future__ import annotations

import os
import re
import sys
import time
import argparse
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed

import yaml

from x_agent.fetcher import build_client, XClientError
from x_agent.classifier import classify, extract_with_llm
from x_agent.storage import Store
from x_agent.digest import build_digest
from x_agent.xhs_fetcher import XhsClient
from x_agent.tgb_fetcher import TgbClient
from x_agent.finance_fetcher import FinanceClient


def _expand_env(value):
    if isinstance(value, str):
        return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def load_config(path="config.yaml"):
    with open(path, encoding="utf-8") as f:
        return _expand_env(yaml.safe_load(f))


def fetch_x(cfg, client, since):
    collected = []
    # min_followers_skip：预留参数，目前读取备用，暂不过滤（0 = 不过滤）
    _min_followers_skip = cfg["fetch"].get("min_followers_skip", 0)

    # 若 client 支持不活跃账号缓存（ThirdPartyXClient），取出引用
    inactive = getattr(client, "inactive_accounts", None)

    groups = cfg.get("account_groups", {})
    if not groups and cfg.get("accounts"):
        groups = {"": cfg["accounts"]}
    for group_tag, acct_list in groups.items():
        for acct in acct_list:
            # 若账号在本轮已标记为不活跃，直接跳过，节省 API 请求
            clean_acct = acct.lstrip("@")
            if inactive is not None and clean_acct in inactive:
                print(f"[fetch_x] 跳过不活跃账号 @{clean_acct}")
                continue
            try:
                tweets = client.user_tweets(acct, cfg["fetch"]["max_per_account"], since)
                for tw in tweets:
                    tw.group_tag = group_tag
                collected += tweets
            except XClientError as e:
                print(f"[warn] X 账号 {acct}: {e}")
    for s in cfg.get("searches", []):
        try:
            collected += client.search(
                s["query"], cfg["fetch"]["max_per_search"], since, s.get("label", "")
            )
        except XClientError as e:
            print(f"[warn] X 搜索 {s.get('label')}: {e}")
    return collected


def fetch_xhs(cfg, since):
    xhs_cfg = cfg.get("xhs", {})
    if not xhs_cfg.get("enabled"):
        print("[xhs] 未启用，跳过")
        return []
    collected = []
    xhs = XhsClient()
    for s in xhs_cfg.get("searches", []):
        try:
            collected += xhs.search(
                s["query"], xhs_cfg.get("max_per_search", 10), since, s.get("label", "xhs")
            )
        except Exception as e:
            print(f"[warn] 小红书搜索 {s.get('label')}: {e}")
    for acct in xhs_cfg.get("accounts", []):
        try:
            collected += xhs.user_posts(acct, xhs_cfg.get("max_per_account", 10), since)
        except Exception as e:
            print(f"[warn] 小红书账号 {acct}: {e}")
    return collected


def fetch_tgb(cfg, since):
    tgb_cfg = cfg.get("tgb", {})
    if not tgb_cfg.get("enabled"):
        print("[tgb] 未启用，跳过")
        return []
    collected = []
    client = TgbClient()
    for user in tgb_cfg.get("users", []):
        uid = user.get("id") if isinstance(user, dict) else user
        if not uid:
            continue
        try:
            collected += client.user_posts(uid, tgb_cfg.get("max_per_user", 10), since)
        except Exception as e:
            print(f"[warn] 淘股吧用户 {uid}: {e}")
    for stock in tgb_cfg.get("stocks", []):
        code = stock.get("code") if isinstance(stock, dict) else stock
        if not code:
            continue
        try:
            collected += client.stock_posts(code, tgb_cfg.get("max_per_stock", 10), since)
        except Exception as e:
            print(f"[warn] 淘股吧个股 {code}: {e}")
    return collected


def fetch_finance(cfg, _since):
    """抓取金融行情数据（A股/美股/加密货币），返回 PriceBar 列表。"""
    fin_cfg = cfg.get("finance", {})
    if not fin_cfg.get("enabled"):
        print("[finance] 未启用，跳过")
        return []

    client = FinanceClient()
    bars = []

    # A 股
    a_list = fin_cfg.get("a_shares", [])
    if a_list:
        symbols = [item["code"] for item in a_list]
        names   = [item.get("name", item["code"]) for item in a_list]
        try:
            result = client.fetch_a_shares(symbols, names)
            print(f"[finance] A股实时行情 {len(result)} 条")
            bars += result
        except Exception as e:
            print(f"[finance] A股抓取失败: {e}")

    # 美股
    us_list = fin_cfg.get("us_stocks", [])
    if us_list:
        symbols = [item["symbol"] for item in us_list]
        try:
            result = client.fetch_us_stocks(symbols)
            print(f"[finance] 美股行情 {len(result)} 条")
            bars += result
        except Exception as e:
            print(f"[finance] 美股抓取失败: {e}")

    # 加密货币
    crypto_list = fin_cfg.get("crypto", [])
    if crypto_list:
        symbols = [item["symbol"] for item in crypto_list]
        try:
            result = client.fetch_crypto(symbols)
            print(f"[finance] 加密货币行情 {len(result)} 条")
            bars += result
        except Exception as e:
            print(f"[finance] 加密货币抓取失败: {e}")

    return bars


def run_once(cfg, client, store, source, llm=None):
    since = dt.datetime.utcnow() - dt.timedelta(hours=cfg["fetch"]["lookback_hours"])
    collected = []

    tasks = {}
    with ThreadPoolExecutor(max_workers=4) as exe:
        if source in ("x", "all") and client:
            tasks["X"] = exe.submit(fetch_x, cfg, client, since)
        if source in ("xhs", "all"):
            tasks["小红书"] = exe.submit(fetch_xhs, cfg, since)
        if source in ("tgb", "all"):
            tasks["淘股吧"] = exe.submit(fetch_tgb, cfg, since)
        if source in ("finance", "all"):
            tasks["金融行情"] = exe.submit(fetch_finance, cfg, since)

        finance_bars = []
        for name, future in tasks.items():
            try:
                data = future.result()
                if name == "金融行情":
                    # 行情数据走独立存储路径，不经过分类器
                    finance_bars = data
                    print(f"[{name}] 抓取 {len(data)} 条")
                else:
                    print(f"[{name}] 抓取 {len(data)} 条")
                    collected += data
            except Exception as e:
                print(f"[{name}] 抓取失败: {e}")

    # 保存行情数据（price_bars 表，无需分类）
    saved_bars = 0
    for bar in finance_bars:
        try:
            store.save_price_bar(bar)
            saved_bars += 1
        except Exception as e:
            print(f"[finance] 保存 {bar.symbol} 失败: {e}")
    if saved_bars:
        print(f"[finance] 已入库 {saved_bars} 条行情")

    new = kept = 0
    for tw in collected:
        if store.seen(tw.id):
            continue
        new += 1
        sig = classify(tw)
        if sig.category == "none":
            continue
        if llm and sig.category in ("strategy", "both"):
            sig.extracted = extract_with_llm(tw, cfg["classify"]["llm_model"], llm)
        store.save(tw, sig)
        kept += 1

    print(f"合计 {len(collected)} 条 · 新 {new} 条 · 命中信号 {kept} 条")


def parse_args():
    parser = argparse.ArgumentParser(description="X + 小红书 + 淘股吧 + 金融行情抓取 Agent")
    parser.add_argument(
        "--source", choices=["x", "xhs", "tgb", "finance", "all"], default="all",
        help="数据来源：x / xhs / tgb / finance / all（默认，四路并行）"
    )
    parser.add_argument("config", nargs="?", default="config.yaml")
    return parser.parse_args()


def main():
    args   = parse_args()
    cfg    = load_config(args.config)
    store  = Store(cfg["storage"]["db_path"])
    client = build_client(cfg) if args.source in ("x", "all") else None

    llm = None
    if cfg["classify"].get("use_llm"):
        import anthropic
        llm = anthropic.Anthropic()

    interval = cfg["fetch"].get("poll_interval_minutes", 0)
    while True:
        run_once(cfg, client, store, args.source, llm)
        build_digest(store, cfg["digest"]["output_path"])
        print(f"摘要已写入 {cfg['digest']['output_path']}")
        if not interval:
            break
        print(f"等待 {interval} 分钟后再次抓取 ...\n")
        time.sleep(interval * 60)


if __name__ == "__main__":
    main()
