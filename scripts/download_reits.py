#!/usr/bin/env python3
"""
中国公募 REITs 列表 + 行情历史下载（绕开 eastmoney，本环境 eastmoney reits_*_em 被墙）。

RACE 多个非 eastmoney 源，谁能返回数据就用谁：
  1. 集思录 jisilu           —— data/reits/ 需登录会员，返回 isError:403 → 不可用
  2. akshare（非 em 的 REITs）—— 本版仅有 reits_hist_em/reits_realtime_em（全 eastmoney）→ 不可用
  3. 上交所/深交所 官网      —— SSE commonQuery sqlId 返回空；SZSE https 被 SSL 阻断/503 → 不可用
  4. yfinance（单只 REIT）   —— 508xxx.SS / 180xxx.SZ 可取 OHLCV → 可用（作为历史兜底源）
  ★ 新浪财经 hq.sinajs.cn    —— 批量报价可"枚举发现" REITs 列表（不存在的代码返回空串自动过滤），
     money.finance.sina.com.cn K 线接口给出上市至今日线 → 列表 + 历史双赢，主源。

winner（本机实测）：新浪财经（列表发现 + 日线历史），yfinance 作为逐只历史兜底。

存储：data/reits_history/
  - reits_list.parquet            REITs 列表（code/symbol/name/现价/涨跌/成交等 + source）
  - history/<symbol>.parquet      单只日线 OHLCV（day/open/high/low/close/volume/source）
  - _summary.json                 运行汇总（winner / 各源失败原因 / 计数）

设计（无人值守）：
  - 每个网络调用都放进独立线程并 join(25s)，超时/异常即跳过（线程无法强杀，弃用其结果继续）
  - 断点续传：history/<symbol>.parquet 已存在且非空则跳过（--force 覆盖）
  - --status 查看已下载情况

用法（务必 .venv/bin/python）：
  .venv/bin/python scripts/download_reits.py
  .venv/bin/python scripts/download_reits.py --limit 5     # 只跑前 5 只历史（调试）
  .venv/bin/python scripts/download_reits.py --force       # 重下已存在历史
  .venv/bin/python scripts/download_reits.py --status
"""
from __future__ import annotations

# ── SSL：本机 requests/akshare/yfinance 需 certifi，否则 CERTIFICATE_VERIFY_FAILED ──
import os
os.environ["SSL_CERT_FILE"] = __import__("certifi").where()
os.environ.setdefault("REQUESTS_CA_BUNDLE", __import__("certifi").where())

import argparse
import json
import threading
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data" / "reits_history"
HIST_DIR = DATA_DIR / "history"
LIST_PARQUET = DATA_DIR / "reits_list.parquet"
SUMMARY_JSON = DATA_DIR / "_summary.json"
LOG_FILE = DATA_DIR / "download.log"

TIMEOUT_S = 25          # 每个网络调用的线程 join 超时
HTTP_TIMEOUT = 20       # 单次 http 超时（应小于 TIMEOUT_S）
KLINE_DATALEN = 3000    # 覆盖最早 REIT（508000 上市 2021-06-21，约 1200 行；给足冗余）

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# REITs 代码枚举范围（新浪批量报价发现；不存在的代码返回空串，自动过滤）
#   上交所基础设施 REITs：sh 508xxx
#   深交所基础设施 REITs：sz 180xxx
SH_RANGE = range(508000, 509000)
SZ_RANGE = range(180000, 181000)
CHUNK = 60              # 每批批量报价代码数


# ── 日志 ──────────────────────────────────────────────────────────────────────

def _log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ── 线程超时包装：网络调用放子线程 join(TIMEOUT_S)，超时/异常即弃用 ──────────────

def run_with_timeout(fn, timeout: float = TIMEOUT_S):
    """返回 (status, value)：status ∈ {'ok','timeout','error'}。超时线程无法强杀，弃其结果。"""
    box: dict = {}

    def target():
        try:
            box["v"] = fn()
            box["ok"] = True
        except Exception as e:  # noqa: BLE001
            box["err"] = e
            box["ok"] = False

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return "timeout", None
    if box.get("ok"):
        return "ok", box.get("v")
    return "error", box.get("err")


def _nonempty(p: Path) -> bool:
    return p.exists() and p.stat().st_size > 400


def _save(p: Path, df: pd.DataFrame) -> int:
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_parquet(p, index=False)
    except Exception:
        p = p.with_suffix(".csv.gz")
        df.to_csv(p, index=False, compression="gzip")
    return len(df)


# ── 源 A（主）：新浪批量报价——枚举发现 REITs 列表 ─────────────────────────────

def _sina_quote_chunk(symbols: list[str]) -> list[dict]:
    """symbols 形如 ['sh508000', 'sz180101']；返回存在的代码报价 dict 列表。"""
    url = "http://hq.sinajs.cn/list=" + ",".join(symbols)
    r = requests.get(url, headers={"User-Agent": UA, "Referer": "https://finance.sina.com.cn/"},
                     timeout=HTTP_TIMEOUT)
    r.encoding = "gbk"
    out: list[dict] = []
    for line in r.text.strip().split("\n"):
        line = line.strip()
        if not line.startswith("var hq_str_"):
            continue
        try:
            sym = line[len("var hq_str_"):line.index("=")]
            payload = line[line.index('"') + 1:line.rindex('"')]
        except ValueError:
            continue
        if not payload:                       # 空串 = 代码不存在
            continue
        f = payload.split(",")
        name = f[0].strip()
        if not name:
            continue

        def _fl(i):
            try:
                return float(f[i])
            except Exception:
                return None

        openp, prev, cur = _fl(1), _fl(2), _fl(3)
        # 剔除只有名字、无任何价格的僵尸条目
        if not any(v and v > 0 for v in (openp, prev, cur)):
            continue
        out.append({
            "symbol": sym,
            "code": sym[2:],
            "exchange": sym[:2],
            "name": name,
            "open": openp,
            "prev_close": prev,
            "price": cur,
            "high": _fl(4),
            "low": _fl(5),
            "volume": _fl(8),
            "amount": _fl(9),
            "date": f[30].strip() if len(f) > 30 else None,
            "time": f[31].strip() if len(f) > 31 else None,
        })
    return out


def discover_list_sina() -> tuple[pd.DataFrame | None, str]:
    """枚举 SH/SZ REITs 代码段，新浪批量报价发现存在的代码。返回 (df, note)。"""
    symbols = [f"sh{c}" for c in SH_RANGE] + [f"sz{c}" for c in SZ_RANGE]
    rows: list[dict] = []
    chunks = [symbols[i:i + CHUNK] for i in range(0, len(symbols), CHUNK)]
    n_timeout = n_err = 0
    for i, ch in enumerate(chunks):
        status, val = run_with_timeout(lambda ch=ch: _sina_quote_chunk(ch))
        if status == "ok" and val:
            rows.extend(val)
        elif status == "timeout":
            n_timeout += 1
        elif status == "error":
            n_err += 1
        if (i + 1) % 10 == 0:
            _log(f"  [list:sina] 进度 {i + 1}/{len(chunks)} 批，已发现 {len(rows)} 只")
        time.sleep(0.15)
    if not rows:
        return None, f"新浪批量报价未发现任何代码（timeout={n_timeout} err={n_err}）"
    df = pd.DataFrame(rows).drop_duplicates(subset=["symbol"]).sort_values("symbol")
    df["source"] = "sina"
    note = f"发现 {len(df)} 只（超时批 {n_timeout}，错误批 {n_err}）"
    return df, note


# ── 源 B（历史主）：新浪日线 K 线 ─────────────────────────────────────────────

def _sina_kline(symbol: str) -> pd.DataFrame | None:
    url = ("http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={KLINE_DATALEN}")
    r = requests.get(url, headers={"User-Agent": UA}, timeout=HTTP_TIMEOUT)
    txt = r.text.strip()
    if not txt or txt in ("null", "[]"):
        return None
    data = json.loads(txt)
    if not data:
        return None
    df = pd.DataFrame(data)
    for c in ("open", "high", "low", "close", "volume"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.rename(columns={"day": "day"})
    keep = [c for c in ("day", "open", "high", "low", "close", "volume") if c in df.columns]
    return df[keep]


# ── 源 C（历史兜底）：yfinance ────────────────────────────────────────────────

def _yf_symbol(symbol: str) -> str:
    # sh508000 -> 508000.SS ; sz180101 -> 180101.SZ
    return f"{symbol[2:]}.{'SS' if symbol.startswith('sh') else 'SZ'}"


def _yf_kline(symbol: str) -> pd.DataFrame | None:
    import yfinance as yf
    t = yf.Ticker(_yf_symbol(symbol))
    h = t.history(period="max", auto_adjust=False)
    if h is None or h.empty:
        return None
    h = h.reset_index()
    h.columns = [str(c) for c in h.columns]
    ren = {"Date": "day", "Open": "open", "High": "high", "Low": "low",
           "Close": "close", "Volume": "volume"}
    h = h.rename(columns=ren)
    h["day"] = pd.to_datetime(h["day"]).dt.strftime("%Y-%m-%d")
    keep = [c for c in ("day", "open", "high", "low", "close", "volume") if c in h.columns]
    return h[keep]


def fetch_history(symbol: str) -> tuple[pd.DataFrame | None, str]:
    """逐只 RACE：先新浪 K 线，失败再 yfinance。统一 schema day/open/high/low/close/volume。"""
    status, val = run_with_timeout(lambda: _sina_kline(symbol))
    if status == "ok" and val is not None and len(val):
        val["source"] = "sina"
        return val, "sina"
    reason_a = "空" if status == "ok" else status
    status, val = run_with_timeout(lambda: _yf_kline(symbol))
    if status == "ok" and val is not None and len(val):
        val["source"] = "yfinance"
        return val, "yfinance"
    reason_b = "空" if status == "ok" else status
    return None, f"sina={reason_a},yfinance={reason_b}"


# ── 主流程 ────────────────────────────────────────────────────────────────────

def build(limit: int | None, force: bool) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    HIST_DIR.mkdir(parents=True, exist_ok=True)

    failures: dict[str, str] = {
        "jisilu": "data/reits/ 需登录会员，返回 isError:403（本机实测）",
        "akshare": "本版 akshare 仅 reits_hist_em/reits_realtime_em（全 eastmoney，被墙）",
        "sse_szse": "SSE commonQuery REITs sqlId 返回空；SZSE https 被 SSL 阻断、http 返回 503",
    }

    # 1) 列表：新浪发现（RACE 的唯一存活列表源）
    _log("=== 发现 REITs 列表（源：新浪批量报价枚举）===")
    if _nonempty(LIST_PARQUET) and not force:
        lst = pd.read_parquet(LIST_PARQUET)
        _log(f"[list] 复用已存在 {LIST_PARQUET.name}：{len(lst)} 只")
        list_source = str(lst["source"].iloc[0]) if "source" in lst.columns and len(lst) else "sina"
    else:
        lst, note = discover_list_sina()
        if lst is None or lst.empty:
            _log(f"!! [list] 新浪发现失败：{note}")
            _write_summary(winner=None, list_source=None, list_rows=0,
                           hist_ok=0, hist_fail=0, sources={"sina": {"list": note}},
                           failures={**failures, "sina": note})
            _log("!! REITs 本环境不可得（无非 eastmoney 源返回列表）")
            return
        n = _save(LIST_PARQUET, lst)
        list_source = "sina"
        _log(f"[list] 新浪 {note} → 保存 {n} 只 {LIST_PARQUET.name}")

    symbols = list(lst["symbol"])
    if limit:
        symbols = symbols[:limit]

    # 2) 逐只历史：新浪 K 线为主，yfinance 兜底；断点续传
    _log(f"=== 下载逐只历史（{len(symbols)} 只，主源新浪 / 兜底 yfinance）===")
    src_count = {"sina": 0, "yfinance": 0}
    ok = fail = skipped = 0
    fail_detail: list[str] = []
    for i, sym in enumerate(symbols, 1):
        outp = HIST_DIR / f"{sym}.parquet"
        if _nonempty(outp) and not force:
            skipped += 1
            continue
        df, src = fetch_history(sym)
        if df is None or not len(df):
            fail += 1
            fail_detail.append(f"{sym}:{src}")
            _log(f"  [{i}/{len(symbols)}] {sym} 失败：{src}")
            continue
        df.insert(0, "symbol", sym)
        _save(outp, df)
        src_count[src] = src_count.get(src, 0) + 1
        ok += 1
        rng = f"{df['day'].min()}~{df['day'].max()}" if "day" in df.columns and len(df) else ""
        _log(f"  [{i}/{len(symbols)}] {sym} ✓ {src} {len(df)} 行 {rng}")
        time.sleep(0.12)

    winner = "sina" if src_count.get("sina", 0) >= src_count.get("yfinance", 0) else "yfinance"
    _log(f"=== 完成：列表源={list_source} 历史成功 {ok}（新浪 {src_count.get('sina',0)} / "
         f"yfinance {src_count.get('yfinance',0)}）跳过 {skipped} 失败 {fail} ===")

    _write_summary(
        winner=winner,
        list_source=list_source,
        list_rows=len(lst),
        hist_ok=ok,
        hist_fail=fail,
        sources={
            "sina": {"role": "列表发现 + 历史主源", "hist_files": src_count.get("sina", 0)},
            "yfinance": {"role": "历史兜底", "hist_files": src_count.get("yfinance", 0)},
        },
        failures=failures,
        hist_skipped=skipped,
        fail_detail=fail_detail[:50],
    )


def _write_summary(**kw) -> None:
    kw["updated_at"] = datetime.now().isoformat(timespec="seconds")
    try:
        SUMMARY_JSON.write_text(json.dumps(kw, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        _log(f"!! 写 summary 失败：{e}")


# ── 状态 ──────────────────────────────────────────────────────────────────────

def show_status() -> None:
    print(f"数据目录：{DATA_DIR}")
    if not DATA_DIR.exists():
        print("  (空)")
        return
    if _nonempty(LIST_PARQUET):
        lst = pd.read_parquet(LIST_PARQUET)
        src = lst["source"].iloc[0] if "source" in lst.columns and len(lst) else "?"
        print(f"  reits_list.parquet: {len(lst)} 只（源={src}）")
        if len(lst):
            print("    示例：" + "，".join(f"{r.code} {r.name}" for r in lst.head(6).itertuples()))
    else:
        print("  reits_list.parquet: 无")
    hist = sorted(HIST_DIR.glob("*.parquet")) + sorted(HIST_DIR.glob("*.csv.gz")) if HIST_DIR.exists() else []
    print(f"  history/: {len(hist)} 个文件")
    total = 0
    for f in hist[:5]:
        try:
            df = pd.read_parquet(f) if f.suffix == ".parquet" else pd.read_csv(f)
            total = len(df)
            rng = f"  {df['day'].min()}~{df['day'].max()}" if "day" in df.columns and len(df) else ""
            print(f"    {f.stem}: {len(df)} 行{rng}")
        except Exception as e:  # noqa: BLE001
            print(f"    {f.name}: 读取失败 {str(e)[:50]}")
    if SUMMARY_JSON.exists():
        print("  _summary.json:")
        try:
            s = json.loads(SUMMARY_JSON.read_text(encoding="utf-8"))
            print(f"    winner={s.get('winner')} 列表源={s.get('list_source')} "
                  f"列表 {s.get('list_rows')} 只 历史成功 {s.get('hist_ok')} 失败 {s.get('hist_fail')}")
        except Exception:
            pass


def main() -> None:
    ap = argparse.ArgumentParser(description="中国公募 REITs 列表+历史下载（绕开 eastmoney）")
    ap.add_argument("--limit", type=int, default=None, help="只下载前 N 只历史（调试）")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的列表/历史")
    ap.add_argument("--status", action="store_true", help="查看已下载情况")
    args = ap.parse_args()

    if args.status:
        show_status()
        return
    build(args.limit, args.force)


if __name__ == "__main__":
    main()
