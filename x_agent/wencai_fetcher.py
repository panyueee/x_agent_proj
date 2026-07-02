"""同花顺问财自然语言选股数据源（基于 pywencai 库）。

用法：config.yaml 配置 `wencai:` section（enabled / queries / max_per_query），
主程序调用 run_queries(cfg) 得到标准化 dict 列表，再经 storage 写入 wencai_picks 表。

依赖说明：
  - pywencai 内部用 PyExecJS 跑同花顺的 JS 加密（生成 hexin-v 请求头），需要外部 node。
    本机默认 PATH 里的 /usr/local/bin/node 是 v12（太老会执行失败），
    模块加载时会自动把 nvm 的 node v22 目录插到 PATH 最前（见 _ensure_node）。

实测记录（2026-07-02）：
  - 返回 pandas.DataFrame；列随查询动态变化，指标列常带日期后缀，如 "市盈率(pe)[20260702]"
  - 通用列：股票代码（600115.SH）/ 股票简称 / 最新价 / 最新涨跌幅 / code / market_code
  - 单次查询约 10~30 秒；查询无结果或语句解析失败时返回 None（不抛异常）
  - 连续 3 个查询未见验证码 / 封禁迹象；保守起见查询间默认 sleep 3 秒
"""
from __future__ import annotations

import os
import re
import time
import datetime as dt

# nvm 安装的新版 node 候选目录（PyExecJS 需要，按顺序取第一个存在的）
_NODE_DIR_CANDIDATES = [
    os.path.expanduser("~/.nvm/versions/node/v22.22.2/bin"),
]

# 指标列名里的日期后缀，如 "市盈率(pe)[20260702]" → "市盈率(pe)"
_DATE_SUFFIX_RE = re.compile(r"\[\d{8}\]$")

# 这些列单独提出为标准字段，不放进 metrics
_CORE_COLS = {"股票代码", "股票简称", "最新价", "最新涨跌幅", "code", "market_code"}


def _ensure_node() -> None:
    """把可用的新版 node 目录顶到 PATH 最前，供 PyExecJS 调用。

    注意：nvm 目录可能本来就在 PATH 里但排在 /usr/local/bin（老 v12）之后，
    所以不能只判断"是否在 PATH 中"，要看实际解析到的 node 是不是候选目录的。
    """
    import shutil
    for d in _NODE_DIR_CANDIDATES:
        node = os.path.join(d, "node")
        if not os.path.isfile(node):
            continue
        if shutil.which("node") != node:
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
        return


def _clean_key(col: str) -> str:
    """去掉指标列名的日期后缀，让不同交易日的同一指标 key 一致。"""
    return _DATE_SUFFIX_RE.sub("", str(col)).strip()


def _to_float(val):
    """尽力转 float；转不了返回 None（问财数值列常是字符串）。"""
    try:
        if val is None:
            return None
        f = float(val)
        return f if f == f else None   # 过滤 NaN
    except (TypeError, ValueError):
        return None


def _normalize_row(row: dict, query: str, label: str,
                   date_str: str, fetched_at: str) -> dict | None:
    """把 DataFrame 的一行标准化为统一 dict；缺股票代码时返回 None。"""
    full_code = str(row.get("股票代码") or "").strip()
    code = str(row.get("code") or "").strip() or full_code.split(".")[0]
    if not code:
        return None
    # 其余指标列收进 metrics（去掉日期后缀，值统一转 str 以便 JSON 序列化）
    metrics = {}
    for k, v in row.items():
        if k in _CORE_COLS or v is None:
            continue
        metrics[_clean_key(k)] = str(v)
    return {
        "code": code,
        "full_code": full_code,
        "name": str(row.get("股票简称") or "").strip(),
        "price": _to_float(row.get("最新价")),
        "change_pct": _to_float(row.get("最新涨跌幅")),
        "metrics": metrics,
        "query": query,
        "label": label,
        "date": date_str,
        "fetched_at": fetched_at,
    }


def run_queries(cfg: dict) -> list[dict]:
    """执行 config 里 wencai.queries 的全部查询，返回标准化选股记录列表。

    单条查询失败只打印警告不中断；wencai.enabled 为假时直接返回空列表。
    """
    wc_cfg = (cfg or {}).get("wencai", {})
    if not wc_cfg.get("enabled"):
        print("[wencai] 未启用，跳过")
        return []

    _ensure_node()
    try:
        import pywencai
    except ImportError as e:
        print(f"[wencai] pywencai 未安装（{e}），跳过")
        return []

    max_per_query = int(wc_cfg.get("max_per_query", 20))
    sleep_sec = float(wc_cfg.get("sleep_sec", 3))
    now = dt.datetime.utcnow()
    date_str = now.strftime("%Y-%m-%d")
    fetched_at = now.isoformat()

    results: list[dict] = []
    queries = wc_cfg.get("queries", []) or []
    for i, item in enumerate(queries):
        # 支持两种写法：纯字符串，或 {query: ..., label: ...}
        if isinstance(item, dict):
            query = str(item.get("query") or "").strip()
            label = str(item.get("label") or "").strip()
        else:
            query, label = str(item).strip(), ""
        if not query:
            continue
        try:
            df = pywencai.get(query=query, loop=False)
        except Exception as e:
            print(f"[wencai] 查询失败「{query}」: {e}")
            continue
        if df is None or not hasattr(df, "iterrows"):
            # 问财对无结果/解析失败的语句返回 None
            print(f"[wencai] 「{query}」无结果")
            continue
        n = 0
        for _, row in df.head(max_per_query).iterrows():
            rec = _normalize_row(row.to_dict(), query, label, date_str, fetched_at)
            if rec:
                results.append(rec)
                n += 1
        print(f"[wencai] 「{query}」命中 {n} 只（原始 {len(df)} 行）")
        # 查询间隔，避免触发同花顺风控（最后一个查询后不用睡）
        if sleep_sec > 0 and i < len(queries) - 1:
            time.sleep(sleep_sec)
    return results
