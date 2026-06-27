"""主程序：加载配置 → 抓取 → 分类 → 入库 → 生成摘要，可定时循环。

用法：
    python main.py [--source x|xhs|all] [config.yaml]

    --source x    只抓 X (Twitter)
    --source xhs  只抓小红书
    --source all  两者都抓（默认）

环境变量：
    X_BEARER_TOKEN      官方 X API（provider=official 时）
    THIRDPARTY_API_KEY  第三方 X API（provider=thirdparty 时）
    ANTHROPIC_API_KEY   仅 use_llm: true 时需要
"""
from __future__ import annotations

import os
import re
import sys
import time
import argparse
import datetime as dt

import yaml

from x_agent.fetcher import build_client, XClientError
from x_agent.classifier import classify, extract_with_llm
from x_agent.storage import Store
from x_agent.digest import build_digest
from x_agent.xhs_fetcher import XhsClient


def _expand_env(value):
    if isinstance(value, str):
        return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def load_config(path: str = "config.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return _expand_env(yaml.safe_load(f))


def fetch_x(cfg: dict, client, since: dt.datetime) -> list:
    collected = []
    groups = cfg.get("account_groups", {})
    if not groups and cfg.get("accounts"):
        groups = {"": cfg["accounts"]}
    for group_tag, acct_list in groups.items():
        for acct in acct_list:
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


def fetch_xhs(cfg: dict, since: dt.datetime) -> list:
    xhs_cfg = cfg.get("xhs", {})
    if not xhs_cfg.get("enabled"):
        print("[xhs] 未启用，跳过（config.yaml 中设 xhs.enabled: true 开启）")
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


def run_once(cfg: dict, client, store: Store, source: str, llm=None) -> None:
    since = dt.datetime.utcnow() - dt.timedelta(hours=cfg["fetch"]["lookback_hours"])
    collected = []

    if source in ("x", "all"):
        print("[X] 开始抓取...")
        x_data = fetch_x(cfg, client, since)
        print(f"[X] 抓取 {len(x_data)} 条")
        collected += x_data

    if source in ("xhs", "all"):
        print("[小红书] 开始抓取...")
        xhs_data = fetch_xhs(cfg, since)
        print(f"[小红书] 抓取 {len(xhs_data)} 条")
        collected += xhs_data

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
    parser = argparse.ArgumentParser(description="X + 小红书资讯抓取 Agent")
    parser.add_argument("--source", choices=["x", "xhs", "all"], default="all",
                        help="数据来源：x=只抓X，xhs=只抓小红书，all=全部（默认）")
    parser.add_argument("config", nargs="?", default="config.yaml", help="配置文件路径")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
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
