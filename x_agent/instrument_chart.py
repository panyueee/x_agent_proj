"""按需 K 线图：把自然语言标的（"茅台"/"600519"/"BTC"/"IF主力"）确定性解析到某个
行情 parquet，渲染蜡烛图 PNG，供 RAG 提问时调出。

设计（见 advisor）：
- 解析走**确定性优先级**，不玩模糊：先精确代码 → 精确名称 → 名称子串；跨市场命中按
  MARKET_ORDER 定序。命中多个市场时（如 000001 撞 平安银行 与 上证指数），echo 里
  既报选中项、也报备选项（消歧安全网）。
- 股票只有代码没中文名：A 股用 data/a_share_names.json（baostock 批量元数据缓存）补名；
  港股/美股仅按代码。期货/汇率/加密/指数/ETF/FRED 本身带 name 列，可按名字搜。
- 复用 mplfinance，但直接 parquet→DataFrame→mpf.plot，不套旧的 PriceBar 结构。
- 列名各市场不一（REITs 用 day，其余 date；股票有 symbol）→ reader 统一归一化。
"""
from __future__ import annotations

import os
os.environ.setdefault("MPLBACKEND", "Agg")  # 服务端在工作线程绘图，禁用 GUI 后端
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"
CHART_DIR = ROOT / "output" / "charts"

# 市场优先级（跨市场撞代码时的定序；靠前者当选，其余进 echo 备选）
MARKET_ORDER = [
    "a", "hk", "us", "index", "etf", "futures_cn", "futures_global",
    "fx", "crypto", "cb", "reits", "bond", "fred",
]
# 各市场目录 + 是否带 name 列（带的读 name，不带的仅代码）
_DIRS = {
    "a": ("stock_history/a", False),
    "hk": ("stock_history/hk", False),
    "us": ("stock_history/us", False),
    "index": ("index_history", True),
    "etf": ("etf_history", True),
    "futures_cn": ("futures_history/cn", True),
    "futures_global": ("futures_history/global", True),
    "fx": ("fx_history", True),
    "crypto": ("crypto_history", True),
    "cb": ("cb_history", False),
    "reits": ("reits_history/history", False),
    "bond": ("bond_history", False),
    "fred": ("macro_history/fred", True),
}
# 口语尾巴（"茅台的K线走势图" → "茅台"）与前缀动词（"看一下茅台" → "茅台"）
_STRIP_TAIL = re.compile(r"(主力连续|主力合约|主力|连续|的?[日周月]?[kK]线|走势图?|行情|股价|价格|图|chart)+$")
_STRIP_HEAD = re.compile(r"^(看一?下|看看|给我|帮我|请|查一?下|查询|调出|来一?[张个]|显示|画|show|the)\s*")


@dataclass
class Match:
    market: str
    code: str          # 归一化主代码（文件识别用）
    name: str          # 展示名（无则等于 code）
    path: str
    def label(self) -> str:
        mk = {"a": "A股", "hk": "港股", "us": "美股", "index": "指数", "etf": "ETF",
              "futures_cn": "国内期货", "futures_global": "全球期货", "fx": "外汇",
              "crypto": "加密", "cb": "可转债", "reits": "REITs", "bond": "债券",
              "fred": "宏观"}.get(self.market, self.market)
        return f"{self.name}（{mk}:{self.code}）" if self.name != self.code else f"{self.code}（{mk}）"


@dataclass
class _Entry:
    market: str
    code: str
    name: str
    path: str
    keys: set = field(default_factory=set)   # 全部可匹配键（小写）


_INDEX: Optional[list] = None
_CJK_FONT: Optional[str] = None


def _cjk_font() -> Optional[str]:
    """注册并返回一个含中文字形的字体名（否则标题中文会显示成豆腐块）。"""
    global _CJK_FONT
    if _CJK_FONT is not None:
        return _CJK_FONT or None
    from matplotlib import font_manager as fm
    for p in ("/System/Library/Fonts/PingFang.ttc",
              "/System/Library/Fonts/Hiragino Sans GB.ttc",
              "/Library/Fonts/Arial Unicode.ttf",
              "/System/Library/Fonts/STHeiti Light.ttc"):
        if os.path.exists(p):
            try:
                fm.fontManager.addfont(p)
                _CJK_FONT = fm.FontProperties(fname=p).get_name()
                return _CJK_FONT
            except Exception:
                continue
    _CJK_FONT = ""
    return None


def _a_names() -> dict:
    f = DATA / "a_share_names.json"
    return json.load(open(f, encoding="utf-8")) if f.exists() else {}


def _codes_from_filename(market: str, stem: str) -> tuple[str, set]:
    """由文件名主干得出（主代码, 匹配键集合）。"""
    s = stem
    keys = {s.lower()}
    code = s
    if market == "a":                       # sh.600519 → 600519
        code = s.split(".")[-1]
        keys |= {code.lower(), s.lower()}
    elif market == "hk":                    # 0001.HK → 0001
        code = s.replace(".HK", "")
        keys |= {code.lower(), code.lstrip("0").lower(), s.lower()}
    elif market == "us":                    # AAPL
        code = s
    elif market in ("futures_cn",):         # A0
        code = s
        keys |= {s.rstrip("0").lower()}     # A0 → a
    elif market == "futures_global":        # BZ_F → BZ
        code = s.replace("_F", "")
        keys |= {code.lower()}
    elif market == "fx":                     # AUDJPY_X → AUDJPY
        code = s.replace("_X", "")
        keys |= {code.lower()}
    elif market == "crypto":                 # ADA-USD → ADA
        code = s.split("-")[0]
        keys |= {code.lower(), s.lower()}
    elif market == "index":                  # 000001_SS → 000001
        code = s.split("_")[0]
        keys |= {code.lower()}
    elif market == "etf":                    # 159611_电力ETF → 159611 (+名)
        parts = s.split("_", 1)
        code = parts[0]
        keys |= {code.lower()}
        if len(parts) > 1:
            keys.add(parts[1].lower())
    elif market == "reits":                  # sh508000 → 508000
        code = re.sub(r"^[a-z]{2}", "", s)
        keys |= {code.lower(), s.lower()}
    else:                                    # cb / bond：文件名整体
        code = s
    return code, {k for k in keys if k}


def _build_index() -> list:
    """扫描各市场，构建 [_Entry]。小市场读 name 列，股票用文件名(+A股名缓存)。"""
    import pandas as pd
    entries = []
    anames = _a_names()
    for market, (sub, has_name) in _DIRS.items():
        d = DATA / sub
        if not d.exists():
            continue
        for fn in os.listdir(d):
            if not fn.endswith(".parquet"):
                continue
            stem = fn[:-8]
            code, keys = _codes_from_filename(market, stem)
            name = code
            if market == "a":
                # a_share_names 键形如 sh.600519
                name = anames.get(stem, code)
                if name != code:
                    keys.add(name.lower())
            elif has_name:
                try:
                    s = pd.read_parquet(d / fn, columns=["name"])["name"].dropna()
                    if len(s):
                        name = str(s.iloc[0])
                        keys.add(name.lower())
                except Exception:
                    pass
            entries.append(_Entry(market, code, name, str(d / fn), keys))
    return entries


def _index() -> list:
    global _INDEX
    if _INDEX is None:
        _INDEX = _build_index()
    return _INDEX


def _clean_query(q: str) -> str:
    q = q.strip()
    for _ in range(4):                       # 反复剥前后缀（"看一下茅台的K线走势" → "茅台"）
        nq = _STRIP_HEAD.sub("", _STRIP_TAIL.sub("", q)).strip(" 　的")
        if nq == q:
            break
        q = nq
    return q


def resolve(query: str, max_alt: int = 2) -> tuple[Optional[Match], list[Match]]:
    """确定性解析：返回 (首选, 备选列表)。无命中返回 (None, [])。

    优先级：精确代码 > 精确名称 > 名称/键子串。同级按 MARKET_ORDER 定序。
    """
    q = _clean_query(query)
    if not q:
        return None, []
    ql = q.lower()
    idx = _index()
    order = {m: i for i, m in enumerate(MARKET_ORDER)}

    # 两桶：精确(ql 完全等于某个键：代码/代码变体/名称) > 子串(ql 是名称或某键的一部分)。
    # 备选只从赢家同一桶取——避免精确命中时被无关子串(ABTC/BTCC)挤掉真正的跨市场同码项。
    exact = [e for e in idx if ql in e.keys]
    sub = [e for e in idx if e not in exact
           and (ql in e.name.lower()
                or (len(e.name) >= 2 and e.name.lower() in ql)
                or any(ql in k for k in e.keys))]

    for bucket in (exact, sub):
        if not bucket:
            continue
        bucket = sorted(bucket, key=lambda e: (order.get(e.market, 99), e.code))
        first, rest = bucket[0], bucket[1:]
        m = lambda e: Match(e.market, e.code, e.name, e.path)
        return m(first), [m(e) for e in rest[:max_alt]]
    return None, []


def _read_ohlc(path: str):
    """parquet → 归一化 DataFrame（DatetimeIndex + Open/High/Low/Close/Volume）。"""
    import pandas as pd
    df = pd.read_parquet(path)
    low = {c.lower(): c for c in df.columns}
    dcol = next((low[c] for c in ("date", "日期", "day") if c in low), df.columns[0])
    df = df.rename(columns={low.get(k, k): k.capitalize()
                            for k in ("open", "high", "low", "close", "volume") if k in low})
    df["_d"] = pd.to_datetime(df[dcol], errors="coerce")
    df = df.dropna(subset=["_d"]).set_index("_d").sort_index()
    keep = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in df.columns]
    return df[keep]


def render_chart(query: str, months: int = 6, out_dir: Optional[str] = None) -> dict:
    """解析 query → 画 K 线 PNG。返回 {ok, png, matched, alts, echo, reason}。

    echo 面向用户：说明选中了哪个标的、以及有无同名/撞代码的备选（消歧安全网）。
    """
    first, alts = resolve(query)
    if first is None:
        return {"ok": False, "png": "", "matched": "", "alts": [],
                "echo": f"未找到与「{query}」匹配的标的", "reason": "no_match"}

    echo = f"已匹配：{first.label()}"
    if alts:
        echo += "；另有 " + "、".join(a.label() for a in alts)

    try:
        import mplfinance as mpf
        import pandas as pd
    except ImportError:
        return {"ok": False, "png": "", "matched": first.label(), "alts": [a.label() for a in alts],
                "echo": echo + "（未装 mplfinance，无法出图）", "reason": "no_mpf"}

    df = _read_ohlc(first.path)
    if months and len(df):
        cut = df.index.max() - pd.DateOffset(months=months)
        df = df[df.index >= cut]
    df = df[(df.get("Close", 0) > 0)] if "Close" in df.columns else df
    if df is None or df.empty:
        return {"ok": False, "png": "", "matched": first.label(), "alts": [a.label() for a in alts],
                "echo": echo + "（无有效K线数据）", "reason": "empty"}

    outd = Path(out_dir) if out_dir else CHART_DIR
    outd.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w.-]", "_", f"{first.market}_{first.code}")
    last = df.index.max().strftime("%Y%m%d")
    png = outd / f"{safe}_{last}_{months}m.png"
    has_vol = bool("Volume" in df.columns and (df["Volume"].fillna(0) > 0).any())
    try:
        font = _cjk_font()
        rc = {"font.family": font, "axes.unicode_minus": False} if font else {}
        style = mpf.make_mpf_style(base_mpf_style="charles", gridstyle="--", rc=rc)
        mpf.plot(df, type="candle", style=style, title=first.label(),
                 volume=has_vol, savefig=dict(fname=str(png), dpi=120, bbox_inches="tight"))
    except Exception as e:
        return {"ok": False, "png": "", "matched": first.label(), "alts": [a.label() for a in alts],
                "echo": echo + f"（绘图失败：{e}）", "reason": "plot_fail"}

    return {"ok": True, "png": str(png), "matched": first.label(),
            "alts": [a.label() for a in alts], "echo": echo, "reason": "ok"}


if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "茅台"
    r = render_chart(q)
    print(json.dumps(r, ensure_ascii=False, indent=2))
