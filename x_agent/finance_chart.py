"""K线图生成模块：将 PriceBar 列表渲染为 mplfinance 蜡烛图 PNG。

依赖：mplfinance、pandas（通常随 mplfinance 一起安装）
"""
from __future__ import annotations

import os
import datetime as dt
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from .finance_fetcher import PriceBar


def save_kline_chart(bars: List, symbol: str, output_dir: str = "./charts") -> str:
    """
    将 PriceBar 列表保存为蜡烛图 PNG。

    参数：
        bars:       PriceBar 列表（按时间升序排列）
        symbol:     品种代码（用于文件名和标题），如 "BTC/USDT"
        output_dir: 输出目录，不存在时自动创建

    返回：保存的 PNG 文件绝对路径。失败时返回空字符串。
    """
    if not bars:
        print(f"[chart] {symbol}: 无K线数据，跳过图表生成")
        return ""

    try:
        import mplfinance as mpf
        import pandas as pd
    except ImportError:
        print("[chart] mplfinance 未安装，K线图跳过。请运行: pip install mplfinance")
        return ""

    # 确保输出目录存在
    os.makedirs(output_dir, exist_ok=True)

    # 构建 DataFrame（mplfinance 要求列名为 Open/High/Low/Close/Volume，索引为 DatetimeIndex）
    records = []
    for bar in bars:
        try:
            ts = dt.datetime.strptime(bar.timestamp, "%Y-%m-%dT%H:%M:%SZ")
        except (ValueError, AttributeError):
            try:
                ts = dt.datetime.fromisoformat(bar.timestamp.replace("Z", "+00:00"))
                ts = ts.replace(tzinfo=None)
            except Exception:
                ts = dt.datetime.utcnow()
        records.append({
            "Date":   ts,
            "Open":   bar.open,
            "High":   bar.high,
            "Low":    bar.low,
            "Close":  bar.close,
            "Volume": bar.volume,
        })

    df = pd.DataFrame(records)
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date").sort_index()

    # 过滤全 0 行（可能是缺数据的占位行）
    df = df[(df["High"] > 0) | (df["Close"] > 0)]
    if df.empty:
        print(f"[chart] {symbol}: 过滤后无有效数据，跳过")
        return ""

    # 生成文件名
    safe_sym = symbol.replace("/", "_").replace(":", "_")
    today = dt.datetime.utcnow().strftime("%Y%m%d")
    filename = f"{safe_sym}_{today}.png"
    filepath = os.path.join(output_dir, filename)

    # 绘图
    try:
        style = mpf.make_mpf_style(base_mpf_style="charles", gridstyle="--")
        mpf.plot(
            df,
            type="candle",
            style=style,
            title=symbol,
            volume=True,
            savefig=dict(fname=filepath, dpi=120, bbox_inches="tight"),
        )
        print(f"[chart] 已保存: {filepath}")
        return os.path.abspath(filepath)
    except Exception as e:
        print(f"[chart] {symbol} 图表保存失败: {e}")
        return ""
