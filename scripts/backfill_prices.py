"""
全市场历史日K回填脚本（A股 + 可选美股/加密）。

功能：
  - 自动获取全量A股代码（~5300只）
  - 多线程并发拉取，默认3线程
  - 断点续传：已有数据的品种自动跳过
  - 批量写入（每只股票一次性 executemany，减少 I/O）
  - 进度实时显示，预计剩余时间

用法：
  python scripts/backfill_prices.py                    # A股全市场，1年
  python scripts/backfill_prices.py --workers 5        # 5线程（加速但有限速风险）
  python scripts/backfill_prices.py --start 20240101   # 自定义起始日期
  python scripts/backfill_prices.py --force            # 忽略已有数据强制重拉
  python scripts/backfill_prices.py --us               # 同时回填美股
  python scripts/backfill_prices.py --crypto           # 同时回填加密货币
  python scripts/backfill_prices.py --symbols 600519,300750  # 只拉指定品种
"""
from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ── 默认参数 ──
DEFAULT_DB       = "output/x_agent.db"
DEFAULT_START    = (dt.date.today() - dt.timedelta(days=365)).strftime("%Y%m%d")
DEFAULT_END      = dt.date.today().strftime("%Y%m%d")
DEFAULT_WORKERS  = 3
REQUEST_DELAY    = 0.8   # 每次请求后等待秒数（per worker）

# ── 美股监控列表（可按需扩展）──
US_SYMBOLS = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META"]

# ── 加密监控列表 ──
CRYPTO_SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"]

# ── 线程安全计数器 ──
_lock       = threading.Lock()
_done       = 0
_skipped    = 0
_failed     = 0
_total      = 0
_t0         = 0.0


def _progress(symbol: str, status: str) -> None:
    global _done, _skipped, _failed
    with _lock:
        if status == "ok":      _done    += 1
        elif status == "skip":  _skipped += 1
        elif status == "fail":  _failed  += 1
        elapsed  = time.time() - _t0
        finished = _done + _skipped + _failed
        rate     = finished / elapsed if elapsed > 0 else 0
        remain   = (_total - finished) / rate if rate > 0 else 0
        pct      = finished / _total * 100 if _total else 0
        eta      = str(dt.timedelta(seconds=int(remain)))
        print(
            f"\r[{pct:5.1f}%] {finished}/{_total}  "
            f"✅{_done} ⏭{_skipped} ❌{_failed}  "
            f"ETA {eta}  last={symbol[:8]:<8} {status}",
            end="", flush=True
        )


def _get_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _already_has_data(conn: sqlite3.Connection, symbol: str, min_rows: int = 200) -> bool:
    """品种已有足够历史数据则跳过。"""
    n = conn.execute(
        "SELECT COUNT(*) FROM price_bars WHERE symbol=? AND LENGTH(timestamp)=10",
        (symbol,)
    ).fetchone()[0]
    return n >= min_rows


def _batch_insert(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    """批量写入，跳过已存在的行（INSERT OR IGNORE）。"""
    conn.executemany(
        "INSERT OR IGNORE INTO price_bars "
        "(symbol, market, timestamp, open, high, low, close, volume, change_pct, name, fetched_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()


# ──────────────────────────────────────────────────────────────
# A股回填
# ──────────────────────────────────────────────────────────────
def _fetch_a_share(symbol: str, start: str, end: str,
                   conn: sqlite3.Connection, force: bool) -> str:
    try:
        import akshare as ak
        if not force and _already_has_data(conn, symbol):
            return "skip"

        df = ak.stock_zh_a_hist(
            symbol=symbol, period="daily",
            start_date=start, end_date=end,
            adjust="qfq"
        )
        if df is None or df.empty:
            return "fail"

        fetched_at = dt.datetime.utcnow().isoformat()
        rows = []
        for _, r in df.iterrows():
            date_str = str(r.get("日期", ""))[:10]
            if not date_str:
                continue
            rows.append((
                symbol, "A",
                date_str,
                float(r.get("开盘", 0) or 0),
                float(r.get("最高", 0) or 0),
                float(r.get("最低", 0) or 0),
                float(r.get("收盘", 0) or 0),
                float(r.get("成交量", 0) or 0) * 100,  # 手→股
                float(r.get("涨跌幅", 0) or 0),
                "",   # name 留空（可后续 JOIN stock_info）
                fetched_at,
            ))

        if rows:
            _batch_insert(conn, rows)
            return "ok"
        return "fail"

    except Exception as e:
        return f"fail"
    finally:
        time.sleep(REQUEST_DELAY)


# ──────────────────────────────────────────────────────────────
# 美股回填
# ──────────────────────────────────────────────────────────────
def _fetch_us_share(symbol: str, start: str, end: str,
                    conn: sqlite3.Connection, force: bool) -> str:
    try:
        import yfinance as yf
        if not force and _already_has_data(conn, symbol):
            return "skip"

        start_fmt = f"{start[:4]}-{start[4:6]}-{start[6:]}"
        end_fmt   = f"{end[:4]}-{end[4:6]}-{end[6:]}"
        df = yf.download(symbol, start=start_fmt, end=end_fmt,
                         progress=False, auto_adjust=True)
        if df is None or df.empty:
            return "fail"

        fetched_at = dt.datetime.utcnow().isoformat()
        rows = []
        prev_close = None
        for date_idx, row in df.iterrows():
            date_str = str(date_idx)[:10]
            close = float(row[("Close", symbol)] if ("Close", symbol) in row.index else row.get("Close", 0) or 0)
            change_pct = ((close - prev_close) / prev_close * 100) if prev_close else 0.0
            prev_close = close
            rows.append((
                symbol, "US",
                date_str,
                float(row.get(("Open",   symbol), row.get("Open",   0)) or 0),
                float(row.get(("High",   symbol), row.get("High",   0)) or 0),
                float(row.get(("Low",    symbol), row.get("Low",    0)) or 0),
                close,
                float(row.get(("Volume", symbol), row.get("Volume", 0)) or 0),
                change_pct,
                "",
                fetched_at,
            ))

        if rows:
            _batch_insert(conn, rows)
            return "ok"
        return "fail"

    except Exception:
        return "fail"
    finally:
        time.sleep(0.2)  # yfinance 不需要太长延迟


# ──────────────────────────────────────────────────────────────
# 加密回填（用 CCXT 或 AKShare）
# ──────────────────────────────────────────────────────────────
def _fetch_crypto(symbol: str, start: str, end: str,
                  conn: sqlite3.Connection, force: bool) -> str:
    try:
        if not force and _already_has_data(conn, symbol):
            return "skip"

        # 尝试用 ccxt（币安接口，更稳定）
        try:
            import ccxt
            exchange = ccxt.binance({"enableRateLimit": True})
            since = int(dt.datetime.strptime(start, "%Y%m%d").timestamp() * 1000)
            ohlcv = exchange.fetch_ohlcv(symbol, "1d", since=since, limit=500)
            if not ohlcv:
                return "fail"

            fetched_at = dt.datetime.utcnow().isoformat()
            end_dt = dt.datetime.strptime(end, "%Y%m%d")
            rows = []
            for candle in ohlcv:
                ts, o, h, l, c, v = candle
                date_str = dt.datetime.utcfromtimestamp(ts / 1000).strftime("%Y-%m-%d")
                if dt.datetime.strptime(date_str, "%Y-%m-%d") > end_dt:
                    break
                rows.append((symbol, "CRYPTO", date_str, o, h, l, c, v, 0.0, "", fetched_at))

            if rows:
                _batch_insert(conn, rows)
                return "ok"

        except ImportError:
            # 退回 AKShare
            import akshare as ak
            base = symbol.replace("/USDT", "").lower()
            fn_map = {
                "btc": ak.crypto_hist_btc_em,
                "eth": ak.crypto_hist_eth_em,
                "sol": ak.crypto_hist_sol_em,
                "bnb": ak.crypto_hist_bnb_em,
            }
            fn = fn_map.get(base)
            if fn is None:
                return "fail"
            df = fn()
            if df is None or df.empty:
                return "fail"
            fetched_at = dt.datetime.utcnow().isoformat()
            rows = []
            for _, r in df.iterrows():
                date_str = str(r.iloc[0])[:10]
                rows.append((
                    symbol, "CRYPTO", date_str,
                    float(r.get("开盘价", 0) or 0),
                    float(r.get("最高价", 0) or 0),
                    float(r.get("最低价", 0) or 0),
                    float(r.get("收盘价", 0) or 0),
                    float(r.get("成交量", 0) or 0),
                    0.0, "", fetched_at,
                ))
            if rows:
                _batch_insert(conn, rows)
                return "ok"

        return "fail"
    except Exception:
        return "fail"
    finally:
        time.sleep(REQUEST_DELAY)


# ──────────────────────────────────────────────────────────────
# 获取全量A股代码
# ──────────────────────────────────────────────────────────────
def _all_a_symbols() -> list[str]:
    import akshare as ak
    print("正在获取全量A股代码列表（来自东方财富实时行情，约需1-4分钟）...", flush=True)
    df = ak.stock_zh_a_spot_em()
    symbols = df["代码"].astype(str).str.zfill(6).tolist()
    print(f"共 {len(symbols)} 只A股", flush=True)
    return symbols


# ──────────────────────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────────────────────
def run(args) -> None:
    global _total, _t0

    db_path = args.db
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    # 确保表和索引存在
    conn_main = _get_db(db_path)
    conn_main.execute("""
        CREATE TABLE IF NOT EXISTS price_bars(
          symbol TEXT, market TEXT, timestamp TEXT,
          open REAL, high REAL, low REAL, close REAL,
          volume REAL, change_pct REAL, name TEXT, fetched_at TEXT,
          PRIMARY KEY (symbol, timestamp)
        )
    """)
    conn_main.execute("CREATE INDEX IF NOT EXISTS idx_pb_symbol ON price_bars(symbol)")
    conn_main.execute("CREATE INDEX IF NOT EXISTS idx_pb_market  ON price_bars(market)")
    conn_main.commit()
    conn_main.close()

    start = args.start
    end   = DEFAULT_END

    # ── 构建任务列表 ──
    tasks: list[tuple[str, str]] = []   # (symbol, market)

    if args.symbols:
        for s in args.symbols.split(","):
            s = s.strip()
            if s:
                tasks.append((s, "A"))
    else:
        a_syms = _all_a_symbols()
        tasks.extend((s, "A") for s in a_syms)

    if args.us:
        tasks.extend((s, "US") for s in US_SYMBOLS)

    if args.crypto:
        tasks.extend((s, "CRYPTO") for s in CRYPTO_SYMBOLS)

    _total = len(tasks)
    _t0    = time.time()

    print(f"\n开始回填：{_total} 个品种  workers={args.workers}  "
          f"{start}~{end}  force={args.force}")
    print(f"预计时间：{_total * REQUEST_DELAY / args.workers / 60:.0f}~"
          f"{_total * (REQUEST_DELAY + 1.5) / args.workers / 60:.0f} 分钟\n")

    def _worker(task):
        symbol, market = task
        # 每个线程独立 conn（SQLite WAL 模式支持多写者）
        conn = _get_db(db_path)
        try:
            if market == "A":
                status = _fetch_a_share(symbol, start, end, conn, args.force)
            elif market == "US":
                status = _fetch_us_share(symbol, start, end, conn, args.force)
            else:
                status = _fetch_crypto(symbol, start, end, conn, args.force)
        finally:
            conn.close()
        _progress(symbol, status)
        return status

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_worker, t): t for t in tasks}
        for _ in as_completed(futures):
            pass

    # 最终统计
    elapsed = time.time() - _t0
    conn = _get_db(db_path)
    total_rows = conn.execute(
        "SELECT COUNT(*) FROM price_bars WHERE LENGTH(timestamp)=10"
    ).fetchone()[0]
    unique_syms = conn.execute(
        "SELECT COUNT(DISTINCT symbol) FROM price_bars WHERE LENGTH(timestamp)=10"
    ).fetchone()[0]
    conn.close()

    print(f"\n\n{'='*60}")
    print(f"完成！耗时 {dt.timedelta(seconds=int(elapsed))}")
    print(f"  ✅ 成功写入：{_done} 个品种")
    print(f"  ⏭  已有跳过：{_skipped} 个品种")
    print(f"  ❌ 失败：    {_failed} 个品种")
    print(f"  📊 price_bars 总计：{total_rows:,} 行 / {unique_syms} 个品种")
    print(f"{'='*60}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="全市场历史日K回填")
    parser.add_argument("--db",      default=DEFAULT_DB,    help="SQLite 路径")
    parser.add_argument("--start",   default=DEFAULT_START, help="起始日期 YYYYMMDD")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="并发线程数")
    parser.add_argument("--force",   action="store_true",   help="强制重拉（忽略已有数据）")
    parser.add_argument("--us",      action="store_true",   help="同时回填美股")
    parser.add_argument("--crypto",  action="store_true",   help="同时回填加密货币")
    parser.add_argument("--symbols", default="",            help="只拉指定品种（逗号分隔）")
    args = parser.parse_args()
    run(args)
