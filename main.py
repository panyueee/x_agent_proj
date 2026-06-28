"""主程序：加载配置 → 并行抓取 → 分类 → 入库 → 生成摘要，可定时循环。

用法：
    python main.py [--source x|xhs|tgb|finance|industry|research|pipeline|all] [config.yaml]

    --source x        只抓 X (Twitter)
    --source xhs      只抓小红书
    --source tgb      只抓淘股吧
    --source finance  只抓金融行情（A股/美股/加密货币）
    --source industry 只跑产业链深挖（消费 pending industry_trigger 事件）
    --source research 只跑研报跟进（消费 pending research_trigger 事件）
    --source pipeline X + 产业链 + 研报 三步联动（先抓 X，再自动触发后续）
    --source all      六路并行（默认）
    --source factor   因子模型（toraniko）
    --source rag      将 DB 中文章/研报/信号批量入库 RAG 向量库
    --source weread   抓取微信读书书单并入库 RAG（需 weread.enabled: true）

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
from x_agent.finance_fetcher import FinanceClient, AKShareClient
from x_agent.industry_fetcher import IndustryClient, IndustryNode
from x_agent.research_fetcher import ResearchClient
from x_agent.pipeline import run_pipeline, run_industry_step, run_research_step
from x_agent.qcc_fetcher import build_qcc_client, QccClientError, ListedCompanyClient
from x_agent.dune_fetcher import build_dune_client
from x_agent.psych_analyzer import PsychAnalyzer
from x_agent.portfolio_optimizer import run_optimizer
from x_agent.risk_analyzer import run_risk_optimizer, compute_risk_report
from x_agent.factor_model import run_factor_model, print_data_checklist


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
    # lookback_days 覆盖全局 since（全量模式专用）
    lookback_days = tgb_cfg.get("lookback_days")
    if lookback_days:
        since = dt.datetime.utcnow() - dt.timedelta(days=int(lookback_days))
    collected = []
    client = TgbClient()
    for user in tgb_cfg.get("users", []):
        uid = user.get("id") if isinstance(user, dict) else user
        if not uid:
            continue
        try:
            posts = client.user_posts(uid, tgb_cfg.get("max_per_user", 10), since)
            print(f"[tgb] 用户 {uid} 帖子 {len(posts)} 篇")
            collected += posts
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

    # 全球指数（HSI、NDX 等）
    indices_list = fin_cfg.get("indices", [])
    if indices_list:
        try:
            result = client.fetch_indices(indices_list)
            print(f"[finance] 指数行情 {len(result)} 条")
            bars += result
        except Exception as e:
            print(f"[finance] 指数抓取失败: {e}")

    return bars


def run_akshare_extras(cfg, store):
    """
    抓取 AKShare 专属数据（龙虎榜、北向资金），东财未覆盖。
    每轮调用一次；失败时打印警告但不中断主流程。
    返回北向资金净流入（亿元）或 None（失败时）。
    """
    fin_cfg = cfg.get("finance", {})
    if not fin_cfg.get("enabled"):
        return None

    ak_client = AKShareClient()
    north_flow_val = None

    # ── 龙虎榜（前一日数据）──
    try:
        records = ak_client.get_dragon_tiger(days=1)
        saved_dt = 0
        for rec in records:
            try:
                store.save_dragon_tiger(rec)
                saved_dt += 1
            except Exception as e:
                print(f"[akshare] 龙虎榜入库失败 {rec.get('code', '')}: {e}")
        if records:
            print(f"[akshare] 龙虎榜入库 {saved_dt}/{len(records)} 条")
    except Exception as e:
        print(f"[akshare] 龙虎榜抓取异常: {e}")

    # ── 北向资金（当日）──
    try:
        flow = ak_client.get_north_flow()
        if flow is not None:
            today_str = dt.date.today().isoformat()
            store.save_north_flow(today_str, flow)
            north_flow_val = flow
    except Exception as e:
        print(f"[akshare] 北向资金抓取异常: {e}")

    # ── 季报数据（营收 + 每股现金流，每季更新一次）──
    try:
        fin_cfg = cfg.get("finance", {})
        a_symbols = [item["code"] for item in fin_cfg.get("a_shares", [])]
        if a_symbols:
            import calendar
            today = dt.date.today()
            # 季报披露月：3/4月（年报）、7/8月（半年报）、10/11月（三季报）、1月（三季报补披）
            # 简化判断：每季末检查一次（每季第一个月首日触发，跨季时更新）
            current_quarter_end = f"{today.year}-{(((today.month - 1) // 3) * 3 + 3):02d}-30"
            # 用第一只股票判断是否有本季数据
            latest = store.latest_quarterly_date(a_symbols[0])
            if latest and latest >= current_quarter_end[:7]:
                print(f"[akshare] 季报数据本季已入库（{latest}），跳过")
            else:
                print(f"[akshare] 开始抓取季报数据（{len(a_symbols)} 只A股）...")
                records = ak_client.fetch_quarterly_financials(a_symbols)
                if records:
                    saved = store.save_quarterly_financials(records)
                    print(f"[akshare] 季报数据入库 {saved} 条")
    except Exception as e:
        print(f"[akshare] 季报数据抓取异常: {e}")

    # ── 全市场基本面快照（市值 + 市净率，每日一次）──
    try:
        today_str = dt.date.today().isoformat()
        latest = store.latest_fundamentals_date()
        if latest == today_str:
            print(f"[akshare] fundamentals 今日已入库（{today_str}），跳过")
        else:
            records = ak_client.fetch_fundamentals(today_str)
            if records:
                saved = store.save_fundamentals_batch(records)
                print(f"[akshare] fundamentals 入库 {saved} 条（{today_str}）")
    except Exception as e:
        print(f"[akshare] fundamentals 抓取异常: {e}")

    return north_flow_val


def _run_rag_ingest(store, cfg):
    """将数据库中的 tweets / 研报 / 微信读书批量入库到 RAG 向量库。"""
    from x_agent.rag import ingest_article, collection_stats

    rag_cfg = cfg.get("rag", {})
    conn = store.conn
    added_total = 0

    # 1. 推文/信号
    if rag_cfg.get("ingest_tweets", True):
        try:
            rows = conn.execute(
                """SELECT t.id, t.author, t.text, t.url, t.created_at,
                          s.category
                   FROM tweets t
                   LEFT JOIN signals s ON s.tweet_id = t.id
                   WHERE LENGTH(t.text) > 50
                   ORDER BY t.created_at DESC LIMIT 500"""
            ).fetchall()
            n_tw = 0
            for tid, author, text, url, created, cat in rows:
                label = f"@{author}" + (f" [{cat}]" if cat else "")
                n_tw += ingest_article(url=url or f"tweet:{tid}",
                                       content=text, title=label,
                                       author=author or "", published_at=created or "",
                                       source_type="article")
            added_total += n_tw
            print(f"[rag] 推文/信号 入库 {n_tw} 新块（共 {len(rows)} 条）")
        except Exception as e:
            print(f"[rag] 推文入库跳过: {e}")

    # 2. 研报
    if rag_cfg.get("ingest_reports", True):
        try:
            rows = conn.execute(
                "SELECT report_id, title, summary, analyst, published_at FROM research_reports "
                "WHERE summary IS NOT NULL AND LENGTH(summary) > 30 "
                "ORDER BY published_at DESC LIMIT 200"
            ).fetchall()
            n_rep = 0
            for rid, title, summary, analyst, pub in rows:
                n_rep += ingest_article(url=f"report:{rid}", content=summary,
                                        title=title or "", author=analyst or "",
                                        published_at=pub or "", source_type="report")
            added_total += n_rep
            print(f"[rag] 研报 入库 {n_rep} 新块（共 {len(rows)} 篇）")
        except Exception as e:
            print(f"[rag] 研报入库跳过: {e}")

    # 3. 微信读书（需要 weread.enabled: true 且配置了书单）
    if rag_cfg.get("ingest_weread", True):
        _run_weread_ingest(cfg, verbose=False)

    after = collection_stats()
    print(f"[rag] 入库完成：新增 {added_total} 块，总计 {after['total_chunks']} 块")
    print(f"[rag] 数据库状态: {after['by_type']}")


def _run_weread_ingest(cfg, verbose=True):
    """抓取 config.yaml weread.books 列表并入库 RAG。"""
    wr_cfg = cfg.get("weread", {})
    if not wr_cfg.get("enabled", False):
        if verbose:
            print("[weread] weread.enabled=false，跳过（在 config.yaml 中设为 true 并配置 cookie）")
        return

    books = wr_cfg.get("books", [])
    if not books:
        if verbose:
            print("[weread] weread.books 列表为空，跳过")
        return

    try:
        from x_agent.weread_fetcher import WeReadClient, ingest_book_to_rag
        client = WeReadClient(cfg=cfg)
    except Exception as e:
        print(f"[weread] 初始化失败: {e}")
        return

    max_ch   = wr_cfg.get("max_chapters", 999)
    sleep_s  = wr_cfg.get("sleep_sec", 0.5)
    total = 0
    for b in books:
        bid = b.get("id", "")
        if not bid:
            continue
        try:
            n = ingest_book_to_rag(bid, client,
                                   title=b.get("title", bid),
                                   author=b.get("author", ""),
                                   max_chapters=max_ch)
            total += n
        except Exception as e:
            print(f"[weread] 《{b.get('title', bid)}》入库失败: {e}")

    print(f"[weread] 完成，共入库 {total} 块（{len(books)} 本书）")


def run_once(cfg, client, store, source, llm=None):
    # ── pipeline 模式：X 抓取完后自动触发产业链→研报联动 ──
    if source == "pipeline":
        since = dt.datetime.utcnow() - dt.timedelta(hours=cfg["fetch"]["lookback_hours"])
        if client:
            tweets = fetch_x(cfg, client, since)
            _save_tweets(tweets, store, cfg, llm)
        run_pipeline(store, cfg, llm_client=llm)
        return

    # ── 独立模块模式 ──
    if source == "industry":
        nodes = fetch_industry(cfg, None)
        for node in nodes:
            store.save_industry_node(node)
        print(f"[industry] 保存节点 {len(nodes)} 个")
        return

    if source == "research":
        reports = fetch_research(cfg, None)
        for r in reports:
            store.save_report(r)
        print(f"[research] 保存研报 {len(reports)} 篇")
        return

    if source == "qcc":
        run_qcc(cfg, store)
        return

    if source == "psych":
        run_psych(cfg, store, llm)
        return

    if source == "portfolio":
        result = run_optimizer(store, cfg)
        if result:
            store.save_portfolio_weights(result)
        return

    if source == "risk":
        result = run_risk_optimizer(store, cfg)
        if result:
            store.save_portfolio_weights(result)
        else:
            # 价格不足时仍打印现有风险指标
            report = compute_risk_report(store, cfg)
            if report:
                for sym, m in report.items():
                    print(f"  {sym}: vol={m['vol_ann']:.1%} max_dd={m['max_dd']:.1%} "
                          f"ret={m['total_ret']:.1%} ({m['n_days']}日)")
        return

    if source == "factor":
        print_data_checklist(store, cfg)
        result = run_factor_model(store, cfg)
        if result:
            store.save_factor_returns(result["factor_returns"])
            print(f"[factor] 因子收益率已写入 DB（{result['n_symbols']} 只 × {result['factor_returns'].shape[0]} 天）")
            from x_agent.digest import build_digest
            build_digest(store, "./output/digest.md")
            print("[factor] digest.md 已更新")
        return

    if source == "rag":
        _run_rag_ingest(store, cfg)
        return

    if source == "weread":
        _run_weread_ingest(cfg, verbose=True)
        return

    # ── 常规抓取模式 ──
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
                    finance_bars = data
                    print(f"[{name}] 抓取 {len(data)} 条")
                else:
                    print(f"[{name}] 抓取 {len(data)} 条")
                    collected += data
            except Exception as e:
                print(f"[{name}] 抓取失败: {e}")

    saved_bars = 0
    for bar in finance_bars:
        try:
            store.save_price_bar(bar)
            saved_bars += 1
        except Exception as e:
            print(f"[finance] 保存 {bar.symbol} 失败: {e}")
    if saved_bars:
        print(f"[finance] 已入库 {saved_bars} 条行情")

    # AKShare 专属数据（龙虎榜、北向资金）
    north_flow_val = None
    if source in ("finance", "all"):
        north_flow_val = run_akshare_extras(cfg, store)

    _save_tweets(collected, store, cfg, llm)

    # all 模式：X 抓完后顺便扫描联动触发，并跑企查查
    if source == "all":
        from x_agent.pipeline import scan_x_for_triggers
        n = scan_x_for_triggers(store, cfg)
        if n:
            print(f"[pipeline] 检测到 {n} 条新触发，运行 `--source pipeline` 可深挖")
        run_qcc(cfg, store)

    # 链上聪明钱（dune / all 模式）
    if source in ("dune", "all"):
        run_dune(cfg, store)

    # 市场恐慌分析（psych / all 模式）
    if source in ("psych", "all"):
        run_psych(cfg, store, llm)

    # 组合权重优化（portfolio / all 模式）
    if source in ("portfolio", "all"):
        result = run_optimizer(store, cfg)
        if result:
            store.save_portfolio_weights(result)
            top = sorted(result["weights"].items(), key=lambda x: x[1], reverse=True)[:5]
            top_str = "  ".join(f"{s}={w:.1%}" for s, w in top if w > 0.001)
            print(f"[portfolio] {result['method']}  TOP5: {top_str}")

    # 北向资金摘要输出（finance / all 模式均打印）
    if north_flow_val is not None:
        sign = "+" if north_flow_val >= 0 else ""
        print(f"[摘要] 北向资金：今日净流入 {sign}{north_flow_val:.1f}亿")


def _save_tweets(collected, store, cfg, llm):
    """分类并入库推文列表，返回 (new, kept) 计数。"""
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
    if collected:
        print(f"合计 {len(collected)} 条 · 新 {new} 条 · 命中信号 {kept} 条")


def fetch_industry(cfg, _since):
    """跑产业链深挖：拉取配置中所有产业链的板块成分股和新闻，直接入库。"""
    industry_cfg = cfg.get("industry", {})
    if not industry_cfg.get("enabled"):
        print("[industry] 未启用，跳过")
        return []
    client = IndustryClient()
    nodes = []
    for chain in industry_cfg.get("chains", []):
        chain_name = chain["name"]
        # 保存配置里的核心节点
        for stock in chain.get("core_stocks", []):
            nodes.append(IndustryNode(
                code=stock["code"], name=stock["name"],
                role=stock.get("role", "core"), chain=chain_name,
            ))
        # 拉取板块成分股
        if chain.get("sector_code"):
            try:
                stocks = client.fetch_sector_stocks(chain["sector_code"], max_results=30)
                for s in stocks:
                    nodes.append(IndustryNode(
                        code=s["code"], name=s["name"],
                        role="core", chain=chain_name,
                    ))
            except Exception as e:
                print(f"[industry] 板块 {chain_name} 抓取失败: {e}")
    return nodes


def run_qcc(cfg, store):
    """拉取企查查企业工商信息与人员数据并入库。"""
    qcc_cfg = cfg.get("qcc", {})
    if not qcc_cfg.get("enabled"):
        print("[qcc] 未启用，跳过")
        return

    client = None
    try:
        client = build_qcc_client(cfg)
    except QccClientError as e:
        print(f"[tyc] 天眼查客户端未配置（{e}），跳过非上市公司，继续抓上市公司")

    total_companies = 0
    total_persons = 0
    for item in (qcc_cfg.get("watch_companies", []) if client else []):
        tyc_id = item.get("tyc_id", "")
        name = item.get("name", tyc_id)
        if not tyc_id and not name:
            continue
        try:
            if tyc_id:
                company, persons = client.fetch_by_id(tyc_id)
            else:
                company, persons = client.fetch_by_name(name)
            if company:
                store.save_company({
                    "credit_code": company.credit_code,
                    "name": company.name or name,
                    "legal_rep": company.legal_rep,
                    "reg_capital": company.reg_capital,
                    "established": company.established,
                    "status": company.status,
                    "industry": company.industry,
                    "address": company.address,
                    "phone": company.phone,
                    "email": company.email,
                    "scope": company.scope,
                    "raw_json": company.raw_json,
                })
                total_companies += 1
            credit_code = company.credit_code if company else (tyc_id or name)
            for p in persons:
                if not p.name:
                    continue
                store.save_company_person(
                    credit_code=credit_code,
                    name=p.name, role=p.role, title=p.title,
                    share_ratio=p.share_ratio, invest_amount=p.invest_amount,
                )
                total_persons += 1
            print(f"[tyc] {name}：法人={company.legal_rep if company else '?'}，"
                  f"人员 {len(persons)} 条")
        except Exception as e:
            print(f"[tyc] {name or tyc_id} 抓取失败: {e}")

    print(f"[tyc] 完成，企业 {total_companies} 家，人员 {total_persons} 条入库")

    # ── 上市公司路：东方财富免费接口 ──
    listed = qcc_cfg.get("listed_companies", [])
    if listed:
        lc = ListedCompanyClient()
        lc_companies = lc_persons = 0
        for item in listed:
            code = item.get("code", "")
            name = item.get("name", code)
            if not code:
                continue
            try:
                company, persons = lc.fetch_all(code, name)
                if company:
                    store.save_company({
                        "credit_code": company.credit_code,
                        "name": company.name or name,
                        "legal_rep": company.legal_rep,
                        "reg_capital": company.reg_capital,
                        "established": company.established,
                        "status": company.status,
                        "industry": company.industry,
                        "address": company.address,
                        "phone": company.phone,
                        "email": company.email,
                        "scope": company.scope,
                        "raw_json": company.raw_json,
                    })
                    lc_companies += 1
                credit_code = company.credit_code if company else f"listed_{code}"
                for p in persons:
                    if not p.name:
                        continue
                    store.save_company_person(
                        credit_code=credit_code,
                        name=p.name, role=p.role, title=p.title,
                        share_ratio=p.share_ratio,
                    )
                    lc_persons += 1
                print(f"[listed] {name}：法人={company.legal_rep if company else '?'}，"
                      f"高管+股东 {len(persons)} 条")
            except Exception as e:
                print(f"[listed] {name} 抓取失败: {e}")
        print(f"[listed] 完成，上市公司 {lc_companies} 家，人员 {lc_persons} 条入库")


def fetch_research(cfg, _since):
    """跑研报跟进：拉取 config 里 watch_stocks 的研报和供应商动态。"""
    research_cfg = cfg.get("research", {})
    if not research_cfg.get("enabled"):
        print("[research] 未启用，跳过")
        return []
    client = ResearchClient()
    reports = []
    for ws in research_cfg.get("watch_stocks", []):
        code = ws.get("code", "")
        if not code:
            continue
        try:
            rpts = client.fetch_reports_eastmoney(code, max_results=10)
            reports.extend(rpts)
            print(f"[research] {ws.get('name', code)} 研报 {len(rpts)} 篇")
        except Exception as e:
            print(f"[research] {code} 研报抓取失败: {e}")
    return reports


def _append_north_flow_to_digest(store, digest_path):
    """在摘要文件末尾追加北向资金一行（若当日有数据）。"""
    try:
        nf = store.latest_north_flow()
        if not nf:
            return
        today_str = dt.date.today().isoformat()
        # 只追加当日数据
        if nf.get("date", "") != today_str:
            return
        val = nf["net_flow_b"]
        sign = "+" if val >= 0 else ""
        line = "\n\n---\n**北向资金**：今日净流入 {}{:.1f}亿\n".format(sign, val)
        with open(digest_path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        print(f"[digest] 北向资金追加失败: {e}")


def run_dune(cfg, store):
    """拉取链上聪明钱/鲸鱼异动数据（Dune Analytics），存入 tweets 表。"""
    client = build_dune_client(cfg)
    if not client:
        return
    dune_cfg = cfg.get("dune", {})
    min_sm    = float(dune_cfg.get("min_usd_smart_money", 500_000))
    min_whale = float(dune_cfg.get("min_usd_whale", 1_000_000))
    tweets = []
    for fn, kwargs in [
        (client.fetch_smart_money,  {"min_usd": min_sm}),
        (client.fetch_whale_alerts, {"min_usd": min_whale}),
        (client.fetch_btc_holders,  {}),
    ]:
        try:
            tweets += fn(**kwargs)
        except Exception as e:
            print(f"[dune] 抓取失败（{fn.__name__}）: {e}")
    saved = 0
    for tw in tweets:
        if store.seen(tw.id):
            continue
        from x_agent.classifier import Signal
        store.save(tw, Signal(category="web3", score=5, tickers=[], extracted=""))
        saved += 1
    print(f"[dune] 链上数据：共 {len(tweets)} 条，新入库 {saved} 条")


def run_psych(cfg, store, llm=None):
    """计算市场恐慌/贪婪指数并入库，可选 LLM 心理解读。"""
    psych_cfg = cfg.get("psych", {})
    if not psych_cfg.get("enabled", False):
        return

    analyzer = PsychAnalyzer(store, psych_cfg)
    lookback = int(psych_cfg.get("lookback_hours", 24))
    data = analyzer.compute_panic_index(lookback_hours=lookback)

    if psych_cfg.get("use_llm") and llm:
        model = cfg.get("classify", {}).get("llm_model", "claude-sonnet-4-6")
        data  = analyzer.run_llm_synthesis(data, llm, model)

    store.save_panic_snapshot(data)

    score  = data["panic_score"]
    signal = data["contrarian_signal"]
    emotion = data["dominant_emotion"]
    # 打印简易温度计
    filled = int(score / 5)
    bar = "█" * filled + "░" * (20 - filled)
    signal_cn = {"buy": "逆向买入预警", "sell": "逆向减仓预警"}.get(signal, "无信号")
    print(
        f"[心理] Panic Index: {score:.0f}/100  [{bar}]  "
        f"{emotion}  {signal_cn} | "
        f"恐慌帖 {data['fear_count']} / 贪婪帖 {data['greed_count']} / "
        f"总扫描 {data['total_posts']} 条"
    )
    llm_report = data.get("llm_report") or {}
    if llm_report.get("crowd_psychology"):
        print(f"[心理] {llm_report['crowd_psychology']}")
    if llm_report.get("contrarian_rationale"):
        print(f"[心理] 逆向逻辑: {llm_report['contrarian_rationale']}")


def parse_args():
    parser = argparse.ArgumentParser(description="X + 小红书 + 淘股吧 + 金融行情 + 产业链 + 研报 + 链上 Agent")
    parser.add_argument(
        "--source",
        choices=["x", "xhs", "tgb", "finance", "industry", "research", "pipeline",
                 "qcc", "dune", "psych", "portfolio", "risk", "factor", "rag", "weread", "all"],
        default="all",
        help="数据来源（默认 all）",
    )
    parser.add_argument("config", nargs="?", default="config.yaml")
    return parser.parse_args()


def main():
    args   = parse_args()
    cfg    = load_config(args.config)
    store  = Store(cfg["storage"]["db_path"])
    needs_x_client = args.source in ("x", "all", "pipeline")
    client = None
    if needs_x_client:
        try:
            client = build_client(cfg)
        except Exception as e:
            print(f"[warn] X 客户端初始化失败（{e}），跳过 X 抓取")

    llm = None
    if cfg["classify"].get("use_llm"):
        import anthropic
        llm = anthropic.Anthropic()

    interval = cfg["fetch"].get("poll_interval_minutes", 0)
    while True:
        run_once(cfg, client, store, args.source, llm)
        build_digest(store, cfg["digest"]["output_path"])
        # 在摘要文件末尾追加北向资金一行（若有数据）
        _append_north_flow_to_digest(store, cfg["digest"]["output_path"])
        print(f"摘要已写入 {cfg['digest']['output_path']}")
        if not interval:
            break
        print(f"等待 {interval} 分钟后再次抓取 ...\n")
        time.sleep(interval * 60)


if __name__ == "__main__":
    main()
