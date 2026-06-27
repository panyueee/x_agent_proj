"""把库里命中的信号汇总成一份 Markdown 摘要。"""
from __future__ import annotations

import json
import datetime as dt


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

    out = "\n".join(lines)
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)
    return out
