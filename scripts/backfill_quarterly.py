"""
全市场季报数据回填（营业总收入 + 自由现金流）。

用法：
  .venv/bin/python scripts/backfill_quarterly.py              # 全量，3线程
  .venv/bin/python scripts/backfill_quarterly.py --workers 5  # 加速
  .venv/bin/python scripts/backfill_quarterly.py --force      # 忽略已有数据
  .venv/bin/python scripts/backfill_quarterly.py --symbols 600519,000858

断点续传：已有数据的品种自动跳过（有任意一条季报记录即跳过）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import sqlite3
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

DEFAULT_DB      = "output/x_agent.db"
DEFAULT_WORKERS = 3
REQUEST_DELAY   = 0.5

_lock    = threading.Lock()
_done    = 0
_skipped = 0
_failed  = 0
_total   = 0
_t0      = 0.0


def _progress(symbol: str, status: str) -> None:
    global _done, _skipped, _failed
    with _lock:
        if status == "ok":      _done    += 1
        elif status == "skip":  _skipped += 1
        else:                   _failed  += 1
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
            end="", flush=True,
        )


def _get_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _already_done(conn: sqlite3.Connection, symbol: str) -> bool:
    n = conn.execute(
        "SELECT COUNT(*) FROM quarterly_financials WHERE symbol=?", (symbol,)
    ).fetchone()[0]
    return n > 0


def _suffix(code: str) -> str:
    return code + (".SH" if code.startswith(("6", "9")) else ".SZ")


def _fetch_one(symbol: str, conn: sqlite3.Connection, force: bool) -> str:
    try:
        import akshare as ak

        if not force and _already_done(conn, symbol):
            return "skip"

        df = ak.stock_financial_analysis_indicator_em(
            symbol=_suffix(symbol), indicator="按报告期"
        )
        if df is None or df.empty:
            return "fail"

        def _f(row, col):
            v = row.get(col)
            try:
                f = float(v)
                return None if (math.isnan(f) or math.isinf(f)) else f
            except (TypeError, ValueError):
                return None

        fetched_at = dt.datetime.utcnow().isoformat()
        rows = []
        for _, row in df.iterrows():
            report_date = str(row.get("REPORT_DATE", ""))[:10]
            if not report_date or report_date == "nan":
                continue
            rows.append((
                symbol,
                report_date,
                _f(row, "TOTALOPERATEREVE"),
                _f(row, "FCFF_BACK"),
                fetched_at,
            ))

        if not rows:
            return "fail"

        conn.executemany(
            "INSERT OR REPLACE INTO quarterly_financials "
            "(symbol, report_date, total_revenue, per_share_cf, fetched_at) "
            "VALUES (?,?,?,?,?)",
            rows,
        )
        conn.commit()
        return "ok"

    except Exception:
        return "fail"
    finally:
        time.sleep(REQUEST_DELAY)


def _all_a_symbols(db_path: str) -> list[str]:
    """从 price_bars 取已有日K的全部A股代码。"""
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT DISTINCT symbol FROM price_bars WHERE market='A' ORDER BY symbol"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def run(args) -> None:
    global _total, _t0

    db_path = args.db
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    # 确保表存在
    conn0 = _get_conn(db_path)
    conn0.execute("""
        CREATE TABLE IF NOT EXISTS quarterly_financials(
          symbol        TEXT,
          report_date   TEXT,
          total_revenue REAL,
          per_share_cf  REAL,
          fetched_at    TEXT,
          PRIMARY KEY (symbol, report_date)
        )
    """)
    conn0.execute("CREATE INDEX IF NOT EXISTS idx_qf_symbol ON quarterly_financials(symbol)")
    conn0.commit()
    conn0.close()

    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        print("从 price_bars 读取全量A股代码...", flush=True)
        symbols = _all_a_symbols(db_path)
        print(f"共 {len(symbols)} 只", flush=True)

    _total = len(symbols)
    _t0    = time.time()

    print(f"\n开始回填季报：{_total} 只  workers={args.workers}  force={args.force}")
    print(f"预计时间：约 {_total * 3 / args.workers / 60:.0f} 分钟\n")

    def _worker(symbol: str) -> str:
        conn = _get_conn(db_path)
        try:
            status = _fetch_one(symbol, conn, args.force)
        finally:
            conn.close()
        _progress(symbol, status)
        return status

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_worker, s): s for s in symbols}
        for _ in as_completed(futures):
            pass

    elapsed = time.time() - _t0
    conn = _get_conn(db_path)
    total_rows = conn.execute("SELECT COUNT(*) FROM quarterly_financials").fetchone()[0]
    total_syms = conn.execute("SELECT COUNT(DISTINCT symbol) FROM quarterly_financials").fetchone()[0]
    conn.close()

    print(f"\n\n{'='*60}")
    print(f"完成！耗时 {dt.timedelta(seconds=int(elapsed))}")
    print(f"  ✅ 成功写入：{_done} 只")
    print(f"  ⏭  已有跳过：{_skipped} 只")
    print(f"  ❌ 失败：    {_failed} 只")
    print(f"  📊 quarterly_financials 总计：{total_rows:,} 行 / {total_syms} 只")
    print(f"{'='*60}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="全市场季报数据回填")
    parser.add_argument("--db",      default=DEFAULT_DB)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--force",   action="store_true")
    parser.add_argument("--symbols", default="", help="指定品种，逗号分隔")
    args = parser.parse_args()
    run(args)
