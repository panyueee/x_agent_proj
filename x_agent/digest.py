"""把库里命中的信号汇总成一份 Markdown 摘要。"""
from __future__ import annotations

import json
import datetime as dt


def _format_pct(pct: float) -> str:
    """格式化涨跌幅，加上 + / - 符号和颜色前缀（纯文本用箭头区分）。"""
    if pct >= 0:
        return f"+{pct:.2f}%"
    return f"{pct:.2f}%"


def _market_section(store, market: str, title: str, limit: int = 30) -> list:
    """生成单一市场的行情 Markdown 行列表。"""
    try:
        rows = store.recent_price_bars(market, limit=limit)
    except Exception:
        return []
    if not rows:
        return []

    lines = [f"### {title}", ""]
    lines.append("| 代码 | 名称 | 最新价 | 涨跌幅 | 更新时间 |")
    lines.append("| ---- | ---- | ------: | ------: | -------- |")
    for row in rows:
        # row: (symbol, name, market, timestamp, open, high, low, close, volume, change_pct)
        symbol, name, _market, timestamp, _o, _h, _l, close, _vol, change_pct = row
        ts_short = timestamp[:16].replace("T", " ") if timestamp else "-"
        pct_str = _format_pct(change_pct)
        lines.append(f"| `{symbol}` | {name} | {close:.4g} | {pct_str} | {ts_short} |")
    lines.append("")
    return lines


def build_digest(store, path: str) -> str:
    rows = store.recent_signals(["strategy", "web3", "both"], limit=80)
    strat = [r for r in rows if r[4] in ("strategy", "both")]
    web3 = [r for r in rows if r[4] in ("web3", "both")]

    lines = [f"# X 资讯摘要 — {dt.datetime.utcnow():%Y-%m-%d %H:%M} UTC", ""]

    lines.append(f"## 📈 交易策略信号（{len(strat)} 条）")
    lines.append("")
    for author, text, url, _created, _cat, score, tickers, extracted in strat:
        tk = ", ".join(json.loads(tickers)) or "-"
        lines.append(f"- **@{author}** · {tk} · 评分 {score}")
        lines.append(f"  > {text.strip()[:240]}")
        ex = json.loads(extracted) if extracted else {}
        if ex:
            lines.append(
                f"  - 方向 `{ex.get('direction')}` | 入场 `{ex.get('entry')}` "
                f"| 目标 `{ex.get('target')}` | 止损 `{ex.get('stop')}` "
                f"| 置信 `{ex.get('confidence')}`"
            )
            if ex.get("thesis"):
                lines.append(f"  - 逻辑：{ex['thesis']}")
        lines.append(f"  - {url}")
    lines.append("")

    lines.append(f"## 🌐 Web3 资讯（{len(web3)} 条）")
    lines.append("")
    for author, text, url, _created, _cat, score, _tickers, _extracted in web3:
        lines.append(f"- **@{author}** · 评分 {score}")
        lines.append(f"  > {text.strip()[:240]}")
        lines.append(f"  - {url}")
    lines.append("")

    # ---- 市场行情板块（从 price_bars 表读取）----
    lines.append("## 💹 市场行情")
    lines.append("")
    lines += _market_section(store, "a_shares",  "A 股")
    lines += _market_section(store, "us_stocks", "美 股")
    lines += _market_section(store, "crypto",    "加密货币")
    lines += _market_section(store, "index",     "全球指数")

    out = "\n".join(lines)
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)
    return out
