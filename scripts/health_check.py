#!/usr/bin/env python3
"""
数据源健康检查：逐个数据源做一次最轻量的只读探测，输出存活状态。

适合每周定时跑，用来发现"静默挂掉"的抓取源（本项目大量依赖未公开接口，
平台一改字段/风控就会无声失效）。

用法（务必用项目 venv，裸 python 是坏的 anaconda 3.8）：
    .venv/bin/python scripts/health_check.py          # 人类可读表格
    .venv/bin/python scripts/health_check.py --json   # 机器可解析 JSON

退出码：被探测的源若有 FAIL → 非 0（便于 cron/CI 告警）；否则 0。
需要付费/密钥的源（X、天眼查、Dune、Anthropic）在缺 key 时标记 SKIP，绝不花钱。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

OK, FAIL, SKIP = "✅ OK", "❌ FAIL", "⏭️ SKIP"


def _timed(fn):
    """执行 fn()，返回 (status, latency_ms, note)。"""
    t0 = time.time()
    try:
        note = fn()
        return OK, int((time.time() - t0) * 1000), note or ""
    except Exception as e:  # noqa: BLE001 —— 健康检查要吞掉一切，逐源隔离
        msg = str(e).replace("\n", " ")[:80]
        return FAIL, int((time.time() - t0) * 1000), f"{type(e).__name__}: {msg}"


# ── 各数据源探测（free 的实跑，key-gated 的缺 key 即 SKIP）──────────────────────

def probe_finance_a():
    from x_agent.finance_fetcher import FinanceClient
    bars = FinanceClient().fetch_a_shares(["600519"], ["贵州茅台"])
    if not bars:
        raise RuntimeError("A股返回空")
    return f"600519 close={bars[0].close}"


def probe_finance_crypto():
    from x_agent.finance_fetcher import FinanceClient
    bars = FinanceClient().fetch_crypto(["BTC/USDT"])
    if not bars:
        raise RuntimeError("加密返回空")
    return f"BTC close={bars[0].close}"


def probe_finance_index():
    from x_agent.finance_fetcher import FinanceClient
    # 同时探测港/美两个指数，任一回来即算 OK（单个境外市场可能休市/限流空返回）
    bars = FinanceClient().fetch_indices([
        {"symbol": "HSI", "name": "恒生指数", "secid": "100.HSI"},
        {"symbol": "NDX", "name": "纳斯达克100", "secid": "107.NDX"},
    ])
    if not bars:
        raise RuntimeError("HSI/NDX 均返回空（东财指数端点可能变更或限流）")
    return f"{bars[0].symbol} close={bars[0].close}（{len(bars)}/2 指数）"


def probe_industry():
    from x_agent.industry_fetcher import IndustryClient
    evts = IndustryClient().fetch_company_news("宁德时代", max_results=3)
    return f"{len(evts)} 条新闻"


def probe_research():
    from x_agent.research_fetcher import ResearchClient
    reps = ResearchClient().fetch_reports_eastmoney("300750", max_results=3)
    return f"{len(reps)} 份研报"


def probe_xhs():
    # 小红书走 xiaohongshu-cli 子进程，通常需要已登录 cookie；探测失败多为未登录/无 cli
    import datetime as dt
    from x_agent.xhs_fetcher import XhsClient
    res = XhsClient().search("白酒", 3, dt.datetime(2000, 1, 1), "health")
    return f"{len(res)} 条笔记"


def probe_tgb_local():
    # 淘股吧本地 Playwright 较重，这里只检查 worker 脚本与依赖是否就绪，不实际抓取
    scraper = ROOT / "x_agent" / "_tgb_scraper.py"
    if not scraper.exists():
        raise RuntimeError("_tgb_scraper.py 缺失")
    import importlib.util
    if importlib.util.find_spec("playwright") is None:
        raise RuntimeError("playwright 未安装")
    return "脚本+playwright 就绪（未实抓）"


# (名称, 探测函数, 所需环境变量 key 或 None)
PROBES = [
    ("finance:A股(Sina)",      probe_finance_a,      None),
    ("finance:加密(gate.io)",  probe_finance_crypto, None),
    ("finance:指数(东财)",     probe_finance_index,  None),
    ("industry:东财新闻",      probe_industry,       None),
    ("research:东财研报",      probe_research,       None),
    ("xhs:小红书cli",          probe_xhs,            None),  # 失败常因未登录
    ("tgb:淘股吧(本地就绪)",   probe_tgb_local,      None),
    ("x:Twitter",              None,                 "THIRDPARTY_API_KEY"),
    ("qcc:天眼查",             None,                 "TYC_TOKEN"),
    ("dune:链上",              None,                 "DUNE_API_KEY"),
]


def run() -> list[dict]:
    results = []
    for name, fn, key in PROBES:
        if key and not os.environ.get(key):
            results.append({"source": name, "status": SKIP, "ms": 0,
                            "note": f"缺 {key}"})
            continue
        if fn is None:
            results.append({"source": name, "status": SKIP, "ms": 0,
                            "note": "无免费探测路径"})
            continue
        status, ms, note = _timed(fn)
        results.append({"source": name, "status": status, "ms": ms, "note": note})
    return results


def main() -> int:
    results = run()

    if "--json" in sys.argv:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        w = max(len(r["source"]) for r in results)
        print(f"\n{'数据源':<{w}}  状态      延迟ms  备注")
        print("─" * (w + 30))
        for r in results:
            print(f"{r['source']:<{w}}  {r['status']:<8} {r['ms']:>6}  {r['note']}")

    n_ok   = sum(1 for r in results if r["status"] == OK)
    n_fail = sum(1 for r in results if r["status"] == FAIL)
    n_skip = sum(1 for r in results if r["status"] == SKIP)
    print(f"\n汇总：{n_ok} OK / {n_fail} FAIL / {n_skip} SKIP")
    # 有被探测的源 FAIL → 非 0 退出，便于 cron/CI 告警
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
