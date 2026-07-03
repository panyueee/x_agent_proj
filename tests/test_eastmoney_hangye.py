"""
scripts/ingest_eastmoney_hangye.py 回归测试（全 mock 网络，不触真实 DB / 不下载 PDF）。

覆盖：
  - 参数构造：个股 qType=0+code、行业 qType=1+industryCode、主题 qType=1（无过滤）
  - 记录 → source_id / pdf_url / extra_meta（report_type、股票/行业字段齐全）
  - 主题关键词客户端过滤（title 或 industryName 命中）
  - collect_industry 路由：纯数字走 industryCode 精确过滤；关键词走翻页+客户端过滤
  - iter_list 翻页：按 TotalPage 收敛、遇空页停、max_pages 上限
  - 断点续传：done-list 里已有 source_id 的记录被跳过（dry_run 也跳）

运行：
    .venv/bin/python -m pytest tests/test_eastmoney_hangye.py -v
"""
from __future__ import annotations

import importlib.util
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_spec = importlib.util.spec_from_file_location(
    "ingest_eastmoney_hangye", os.path.join(_ROOT, "scripts", "ingest_eastmoney_hangye.py"))
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


# ── 代表性 report/list 记录（按实测字段裁剪）──────────────────────────────────
GEGU_REC = {
    "title": "2026年一季报点评：营收稳定增长",
    "stockName": "拓普集团", "stockCode": "601689",
    "industryName": "", "industryCode": "",
    "orgSName": "华金证券", "researcher": "黄程保",
    "emRatingName": "买入", "publishDate": "2026-05-14 00:00:00.000",
    "infoCode": "AP202605141822307401",
}
HANGYE_REC = {
    "title": "汽车行业研究周报：政策齐发",
    "stockName": "", "stockCode": "",
    "industryName": "汽车零部件", "industryCode": "481",
    "orgSName": "申港证券", "researcher": "曹旭特",
    "emRatingName": "", "publishDate": "2026-07-02 00:00:00.000",
    "infoCode": "AP202607021826700000",
}
ROBOT_REC = {  # 主题“机器人”只在标题命中，industryName 是别的行业
    "title": "人形机器人产业链深度：执行器放量",
    "stockName": "", "stockCode": "",
    "industryName": "通用设备", "industryCode": "459",
    "orgSName": "东吴证券", "researcher": "周尔双",
    "emRatingName": "", "publishDate": "2026-06-20 00:00:00.000",
    "infoCode": "AP202606201826600001",
}


# ── 纯函数 ───────────────────────────────────────────────────────────────────
def test_stock_params():
    assert mod.stock_params("601689", "2026-01-01", "2026-06-30") == {
        "qType": 0, "code": "601689",
        "beginTime": "2026-01-01", "endTime": "2026-06-30"}


def test_industry_params():
    p = mod.industry_params("481", "2026-01-01", "2026-06-30")
    assert p["qType"] == 1 and p["industryCode"] == "481"


def test_keyword_params_has_no_filter():
    p = mod.keyword_params("2026-01-01", "2026-06-30")
    assert p["qType"] == 1
    assert "code" not in p and "industryCode" not in p


def test_source_id_and_pdf_url():
    assert mod.source_id_of(GEGU_REC) == "research:em:AP202605141822307401"
    assert mod.pdf_url_of(GEGU_REC) == \
        "https://pdf.dfcfw.com/pdf/H3_AP202605141822307401_1.pdf"


def test_extra_meta_gegu():
    m = mod.extra_meta_of(GEGU_REC, "gegu")
    assert m["report_type"] == "gegu"
    assert m["stock_code"] == "601689" and m["stock_name"] == "拓普集团"
    assert m["org"] == "华金证券" and m["date"] == "2026-05-14"
    assert m["rating"] == "买入" and m["info_code"] == "AP202605141822307401"


def test_extra_meta_hangye():
    m = mod.extra_meta_of(HANGYE_REC, "hangye")
    assert m["report_type"] == "hangye"
    assert m["industry_code"] == "481" and m["industry"] == "汽车零部件"
    assert m["stock_code"] == ""  # 行业研报无个股


def test_title_matches():
    assert mod.title_matches(ROBOT_REC, "机器人")          # 命中标题
    assert mod.title_matches(HANGYE_REC, "汽车零部件")      # 命中 industryName
    assert not mod.title_matches(GEGU_REC, "机器人")


# ── iter_list 翻页 ───────────────────────────────────────────────────────────
def _paged_fetch(pages):
    """构造一个假的 fetch_list：pages 是每页 data 列表；TotalPage=len(pages)。"""
    calls = []

    def fake(_s, params):
        calls.append(params)
        no = params.get("pageNo", 1)
        data = pages[no - 1] if 1 <= no <= len(pages) else []
        return {"hits": sum(len(p) for p in pages),
                "TotalPage": len(pages), "data": data}

    return fake, calls


def test_iter_list_paginates(monkeypatch):
    fake, calls = _paged_fetch([[GEGU_REC, HANGYE_REC], [ROBOT_REC]])
    monkeypatch.setattr(mod, "fetch_list", fake)
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    out = list(mod.iter_list(None, {"qType": 1}))
    assert len(out) == 3
    assert [c["pageNo"] for c in calls] == [1, 2]


def test_iter_list_max_pages(monkeypatch):
    fake, calls = _paged_fetch([[GEGU_REC], [HANGYE_REC], [ROBOT_REC]])
    monkeypatch.setattr(mod, "fetch_list", fake)
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    out = list(mod.iter_list(None, {"qType": 1}, max_pages=1))
    assert len(out) == 1  # 只翻第 1 页
    assert [c["pageNo"] for c in calls] == [1]


# ── collect_industry 路由 ────────────────────────────────────────────────────
def test_collect_industry_numeric_uses_industrycode(monkeypatch):
    seen = {}

    def fake(_s, params):
        seen.update(params)
        return {"TotalPage": 1, "data": [HANGYE_REC]}

    monkeypatch.setattr(mod, "fetch_list", fake)
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    recs = mod.collect_industry(None, "481", "2026-01-01", "2026-06-30", 30)
    assert seen.get("industryCode") == "481"   # 走服务端精确过滤
    assert len(recs) == 1


def test_collect_industry_keyword_filters_clientside(monkeypatch):
    def fake(_s, params):
        # 关键词模式：无 industryCode，返回混合记录
        assert "industryCode" not in params
        return {"TotalPage": 1, "data": [GEGU_REC, HANGYE_REC, ROBOT_REC]}

    monkeypatch.setattr(mod, "fetch_list", fake)
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    recs = mod.collect_industry(None, "机器人", "2026-01-01", "2026-06-30", 30)
    assert len(recs) == 1 and recs[0]["infoCode"] == ROBOT_REC["infoCode"]


def test_collect_industry_keyword_warns_on_truncation(monkeypatch, capsys):
    # TotalPage(150) > max_pages(30) → 必须打印覆盖不全告警，别静默少覆盖
    def fake(_s, params):
        return {"TotalPage": 150, "data": [ROBOT_REC]}

    monkeypatch.setattr(mod, "fetch_list", fake)
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    mod.collect_industry(None, "机器人", "2026-01-01", "2026-06-30", 30)
    assert "窗口未覆盖完" in capsys.readouterr().out


# ── 断点续传去重 ─────────────────────────────────────────────────────────────
def test_dedup_skips_done(monkeypatch, capsys):
    # dry_run=True 不触网/不入库；done 里已有 GEGU 的 sid → 应被跳过
    done = {mod.source_id_of(GEGU_REC): {"done": 1}}
    got = mod.ingest_records(
        None, [dict(GEGU_REC), dict(HANGYE_REC)],
        report_type="hangye", done=done, limit=0, dry_run=True)
    out = capsys.readouterr().out
    assert got == 1                              # 只剩 HANGYE
    assert HANGYE_REC["title"] in out
    assert GEGU_REC["title"] not in out          # 已 done 的不出现
