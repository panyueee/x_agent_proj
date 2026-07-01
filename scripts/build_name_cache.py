#!/usr/bin/env python3
"""重建 A 股代码→名称缓存 data/a_share_names.json，供按需K线图按"茅台"等中文名解析标的。

这是 baostock 的**批量元数据**接口（query_stock_basic，一次全量返回），
不是逐股K线循环，不受之前 ~3900 只后限流卡住的影响，秒级完成。

用法：.venv/bin/python scripts/build_name_cache.py
K线模块 x_agent/instrument_chart.py 依赖此文件；缺失时 A 股会退化为仅按代码匹配。
"""
from __future__ import annotations
import json, sys
from pathlib import Path

OUT = Path(__file__).parent.parent / "data" / "a_share_names.json"


def main():
    import baostock as bs
    lg = bs.login()
    if lg.error_code != "0":
        print("baostock 登录失败:", lg.error_msg); sys.exit(1)
    rs = bs.query_stock_basic()
    m = {}
    while rs.error_code == "0" and rs.next():
        code, name = rs.get_row_data()[:2]   # code 形如 sh.600519
        m[code] = name
    bs.logout()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    json.dump(m, open(OUT, "w"), ensure_ascii=False)
    print(f"已写 {OUT}：{len(m)} 只，例 {list(m.items())[:3]}")


if __name__ == "__main__":
    main()
