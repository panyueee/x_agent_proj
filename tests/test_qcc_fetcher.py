"""qcc_fetcher.py 解析逻辑回归测试。

覆盖：
  - 股票代码 → 东方财富 SECUCODE / SH-SZ 前缀的归一化
  - 天眼查 TyanchaClient：state/error_code 校验、各接口 JSON → dataclass 解析
  - 工商信息接口(1001) error_code != 0 抛 TycClientError
  - 东方财富 ListedCompanyClient：十大股东/实际控制人/经营范围解析
  - build_qcc_client 从配置 / 环境变量读取 token

约束：不做任何真实网络请求，全部用 unittest.mock 打桩 session，喂入仿真响应体。

本文件既可用 pytest 运行：
    .venv/bin/python -m pytest tests/test_qcc_fetcher.py -v
也可直接当脚本运行（无 pytest 依赖）：
    .venv/bin/python tests/test_qcc_fetcher.py
"""
from __future__ import annotations

import os
import sys

# 让 `import x_agent.qcc_fetcher` 在任意 cwd 下都能工作
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest

from x_agent import qcc_fetcher as qf
from x_agent.qcc_fetcher import (
    CompanyInfo,
    PersonInfo,
    TyanchaClient,
    TycClientError,
    QccClientError,
    ListedCompanyClient,
    build_qcc_client,
    _em_secucode,
    _em_prefix_code,
)


# ── 测试替身 ──────────────────────────────────────────────────────────────────

class _FakeResp:
    """仿 requests.Response：暴露 json() / raise_for_status()。"""

    def __init__(self, json_data=None, raise_exc=None):
        self._json = json_data
        self._raise_exc = raise_exc

    def raise_for_status(self):
        if self._raise_exc is not None:
            raise self._raise_exc

    def json(self):
        return self._json


class _FakeSession:
    """按调用顺序依次返回预置响应，记录每次 get/post 的参数。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []   # [("get"/"post", url, kwargs), ...]
        self.headers = {}

    def _next(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self._responses:
            raise AssertionError("session 调用次数超过预置响应数")
        return self._responses.pop(0)

    def get(self, url, **kwargs):
        return self._next("get", url, **kwargs)

    def post(self, url, **kwargs):
        return self._next("post", url, **kwargs)


def _tyc_client_with(responses):
    c = TyanchaClient("fake-token")
    c.session = _FakeSession(responses)
    return c


def _listed_client_with(responses):
    c = ListedCompanyClient()
    c.session = _FakeSession(responses)
    return c


# ── 1. 代码归一化 ─────────────────────────────────────────────────────────────

def test_em_secucode_sh_for_6_and_9():
    assert _em_secucode("600519") == "600519.SH"
    assert _em_secucode("900001") == "900001.SH"


def test_em_secucode_sz_otherwise():
    assert _em_secucode("000858") == "000858.SZ"
    assert _em_secucode("300750") == "300750.SZ"


def test_em_secucode_strips_whitespace():
    assert _em_secucode("  600519 ") == "600519.SH"


def test_em_prefix_code():
    assert _em_prefix_code("600519") == "SH600519"
    assert _em_prefix_code("900001") == "SH900001"
    assert _em_prefix_code("000858") == "SZ000858"
    assert _em_prefix_code(" 300750 ") == "SZ300750"


# ── 2. TyanchaClient 初始化 & 异常别名 ───────────────────────────────────────

def test_client_requires_token():
    with pytest.raises(TycClientError):
        TyanchaClient("")


def test_qcc_client_error_is_alias():
    assert QccClientError is TycClientError


def test_client_sets_auth_header():
    c = TyanchaClient("abc")
    assert c.session.headers.get("Authorization") == "token abc"


# ── 3. _get：state 校验 ──────────────────────────────────────────────────────

def test_get_raises_when_state_not_ok():
    c = _tyc_client_with([_FakeResp(json_data={"state": "error", "message": "额度不足"})])
    with pytest.raises(TycClientError) as ei:
        c._get("/x", {})
    assert "额度不足" in str(ei.value)


def test_get_returns_data_when_ok():
    c = _tyc_client_with([_FakeResp(json_data={"state": "ok", "data": {"k": 1}})])
    assert c._get("/x", {}) == {"state": "ok", "data": {"k": 1}}


# ── 4. search_company ────────────────────────────────────────────────────────

def test_search_company_parses_items():
    payload = {"state": "ok", "data": {"items": [
        {
            "id": 12345,
            "creditCode": "91110000XXX",
            "name": "示例科技有限公司",
            "legalPersonName": "张三",
            "regStatus": "存续",
            "estiblishTime": "2010-05-06 00:00:00",
            "regCapital": "1000万",
        },
    ]}}
    c = _tyc_client_with([_FakeResp(json_data=payload)])
    rows = c.search_company("示例")
    assert len(rows) == 1
    r = rows[0]
    assert r["id"] == "12345"          # id 转为字符串
    assert r["credit_code"] == "91110000XXX"
    assert r["name"] == "示例科技有限公司"
    assert r["legal_rep"] == "张三"
    assert r["status"] == "存续"
    assert r["established"] == "2010-05-06"   # 截取前 10 位
    assert r["reg_capital"] == "1000万"


def test_search_company_empty_items():
    c = _tyc_client_with([_FakeResp(json_data={"state": "ok", "data": {}})])
    assert c.search_company("无") == []


# ── 5. get_company_detail ────────────────────────────────────────────────────

def test_get_company_detail_parses():
    item = {
        "creditCode": "91110000ABC",
        "name": "甲公司",
        "legalPersonName": "李四",
        "regCapital": "5000万",
        "estiblishTime": "2005-01-02 00:00:00",
        "regStatus": "在业",
        "industry": "软件",
        "regLocation": "北京市海淀区",
        "phoneNumber": "010-0000",
        "email": "a@b.com",
        "businessScope": "技术开发" * 200,   # 超长 → 截 500
    }
    c = _tyc_client_with([_FakeResp(json_data={"state": "ok", "data": item})])
    info = c.get_company_detail("999")
    assert isinstance(info, CompanyInfo)
    assert info.credit_code == "91110000ABC"
    assert info.name == "甲公司"
    assert info.legal_rep == "李四"
    assert info.established == "2005-01-02"
    assert info.industry == "软件"
    assert len(info.scope) == 500


def test_get_company_detail_empty_returns_none():
    c = _tyc_client_with([_FakeResp(json_data={"state": "ok", "data": {}})])
    assert c.get_company_detail("999") is None


def test_get_company_detail_credit_code_fallback():
    # creditCode 缺失 → 回退 tyc_<id>
    item = {"name": "乙公司"}
    c = _tyc_client_with([_FakeResp(json_data={"state": "ok", "data": item})])
    info = c.get_company_detail("888")
    assert info.credit_code == "tyc_888"


# ── 6. get_shareholders / executives / investments ──────────────────────────

def test_get_shareholders_parses_and_skips_nameless():
    result = [
        {"name": "股东甲", "holderType": "法人股东", "stockPercent": "30%", "shouldCapi": "300万"},
        {"holderName": "股东乙", "stockPercent": "20%"},   # 用 holderName 兜底
        {"stockPercent": "10%"},                            # 无名 → 跳过
    ]
    c = _tyc_client_with([_FakeResp(json_data={"state": "ok", "data": {"result": result}})])
    persons = c.get_shareholders("1")
    assert [p.name for p in persons] == ["股东甲", "股东乙"]
    assert persons[0].role == "shareholder"
    assert persons[0].share_ratio == "30%"
    assert persons[0].invest_amount == "300万"
    assert persons[0].title == "法人股东"


def test_get_executives_role_detection():
    result = [
        {"name": "王董事长", "staffTypeName": "董事长"},
        {"name": "赵经理", "staffTypeName": "总经理"},
        {"staffName": "钱监事", "typeJoin": "监事"},   # 用 staffName / typeJoin 兜底
        {"staffTypeName": "无名职位"},                 # 无名 → 跳过
    ]
    c = _tyc_client_with([_FakeResp(json_data={"state": "ok", "data": {"result": result}})])
    persons = c.get_executives("1")
    assert len(persons) == 3
    assert persons[0].role == "legal_rep"   # 董事长 → legal_rep
    assert persons[1].role == "executive"
    assert persons[2].name == "钱监事"


def test_get_investments_parses():
    result = [
        {"name": "被投甲", "percent": "51%", "amount": "510万"},
        {"companyName": "被投乙", "percent": "49%"},   # companyName 兜底
        {"percent": "1%"},                              # 无名 → 跳过
    ]
    c = _tyc_client_with([_FakeResp(json_data={"state": "ok", "data": {"result": result}})])
    persons = c.get_investments("1")
    assert [p.name for p in persons] == ["被投甲", "被投乙"]
    assert persons[0].role == "investor"
    assert "51%" in persons[0].title
    assert persons[0].invest_amount == "510万"


# ── 7. 工商信息接口(1001) ─────────────────────────────────────────────────────

def test_get_ic_raises_when_error_code_nonzero():
    c = _tyc_client_with([_FakeResp(json_data={"error_code": 300000, "reason": "无数据"})])
    with pytest.raises(TycClientError) as ei:
        c._get_ic("某公司")
    assert "无数据" in str(ei.value)


def test_get_ic_ok():
    c = _tyc_client_with([_FakeResp(json_data={"error_code": 0, "result": {"name": "X"}})])
    assert c._get_ic("X") == {"error_code": 0, "result": {"name": "X"}}


def test_get_company_ic_parses_all_sections():
    item = {
        "creditCode": "91110000IC",
        "name": "工商公司",
        "legalPersonName": "周法人",
        "regCapital": "8000万",
        "estiblishTime": "2001-09-09 12:00:00",
        "regStatus": "存续",
        "industry": "制造",
        "regLocation": "上海",
        "phoneNumber": "021-1",
        "email": "ic@x.com",
        "businessScope": "生产销售",
        "id": 1,
        "staffList": [
            {"name": "周法人", "staffTypeName": "董事长", "typeJoin": ["董事长", "总经理"]},
            {"name": "钱高管", "staffTypeName": "经理", "typeJoin": ["经理"]},
            {"staffTypeName": "无名"},   # 无名 → 跳过
        ],
        "shareHolderList": [
            {"name": "母公司", "capital": [{"percent": "60%", "amomon": "4800万"}]},
            {"name": "无出资股东"},       # capital 为空 → percent/amount 空串
        ],
        "investList": [
            {"name": "子公司", "percent": "100%", "amount": 1000},
        ],
    }
    c = _tyc_client_with([_FakeResp(json_data={"error_code": 0, "result": item})])
    company, persons = c.get_company_ic("工商公司")
    assert isinstance(company, CompanyInfo)
    assert company.credit_code == "91110000IC"
    assert company.name == "工商公司"
    assert company.established == "2001-09-09"

    by_name = {p.name: p for p in persons}
    assert set(by_name) == {"周法人", "钱高管", "母公司", "无出资股东", "子公司"}
    # typeJoin 含董事长 → legal_rep；title 用 typeJoin 拼接
    assert by_name["周法人"].role == "legal_rep"
    assert by_name["周法人"].title == "董事长, 总经理"
    assert by_name["钱高管"].role == "executive"
    # 股东持股
    assert by_name["母公司"].role == "shareholder"
    assert by_name["母公司"].share_ratio == "60%"
    assert by_name["母公司"].invest_amount == "4800万"
    assert by_name["无出资股东"].share_ratio == ""
    # 对外投资金额转字符串
    assert by_name["子公司"].role == "investor"
    assert by_name["子公司"].invest_amount == "1000"


def test_get_company_ic_role_detection_ignores_typejoin():
    """POSSIBLE BUG 记录：get_company_ic 的 role 仅看 staffTypeName(title)，
    不看 typeJoin。即使 typeJoin 含「董事长」，只要 staffTypeName 不含关键词，
    role 仍判为 executive。展示用 title 却优先取 typeJoin，二者口径不一致。
    本测试断言「当前实际行为」以保持绿色，并在报告中标记为可疑点。"""
    item = {
        "name": "工商公司",
        "creditCode": "CC",
        "staffList": [
            # staffTypeName 不含董事长，typeJoin 含董事长 → 仍被判为 executive
            {"name": "实为董事长", "staffTypeName": "高管", "typeJoin": ["董事长"]},
        ],
    }
    c = _tyc_client_with([_FakeResp(json_data={"error_code": 0, "result": item})])
    _, persons = c.get_company_ic("工商公司")
    assert persons[0].role == "executive"      # 当前行为（疑似应为 legal_rep）
    assert persons[0].title == "董事长"          # 展示 title 却用了 typeJoin


def test_get_company_ic_empty_result():
    c = _tyc_client_with([_FakeResp(json_data={"error_code": 0, "result": {}})])
    company, persons = c.get_company_ic("无")
    assert company is None
    assert persons == []


def test_get_company_ic_credit_code_fallbacks():
    # creditCode/taxNumber 都缺 → tyc_<id>
    item = {"name": "兜底公司", "id": 42}
    c = _tyc_client_with([_FakeResp(json_data={"error_code": 0, "result": item})])
    company, _ = c.get_company_ic("兜底公司")
    assert company.credit_code == "tyc_42"


def test_fetch_by_name_and_id_delegate_to_ic():
    payload = {"error_code": 0, "result": {"name": "委托公司", "creditCode": "CC"}}
    c = _tyc_client_with([_FakeResp(json_data=payload), _FakeResp(json_data=payload)])
    company1, _ = c.fetch_by_name("委托公司")
    company2, _ = c.fetch_by_id("123")
    assert company1.credit_code == "CC"
    assert company2.credit_code == "CC"
    # 两次都打到 1001 工商接口
    assert all("/cb/ic/2.0" in call[1] for call in c.session.calls)


# ── 8. build_qcc_client ──────────────────────────────────────────────────────

def test_build_qcc_client_from_cfg():
    c = build_qcc_client({"qcc": {"token": "cfg-token"}})
    assert c.session.headers.get("Authorization") == "token cfg-token"


def test_build_qcc_client_from_env(monkeypatch):
    monkeypatch.setenv("TYC_TOKEN", "env-token")
    c = build_qcc_client({})
    assert c.session.headers.get("Authorization") == "token env-token"


def test_build_qcc_client_missing_token_raises(monkeypatch):
    monkeypatch.delenv("TYC_TOKEN", raising=False)
    with pytest.raises(TycClientError):
        build_qcc_client({})


# ── 9. ListedCompanyClient ───────────────────────────────────────────────────

def test_listed_shareholders_and_controller_parses():
    data = {
        "sdltgd": [
            {"HOLDER_NAME": "流通股东A", "FREE_HOLDNUM_RATIO": 12.3456789, "HOLDER_TYPE": "境内法人"},
            {"HOLDER_NAME": "流通股东B", "FREE_HOLDNUM_RATIO": "5.5%"},   # 非 float → str 原样
            {"HOLDER_NAME": "", "FREE_HOLDNUM_RATIO": 1.0},               # 无名 → 跳过
        ],
        "sjkzr": [
            {"HOLDER_NAME": "实控人甲", "HOLD_RATIO": 40},
            {"HOLDER_NAME": "实控人乙"},   # 无 HOLD_RATIO → share_ratio 空
            {"HOLDER_NAME": ""},           # 无名 → 跳过
        ],
    }
    c = _listed_client_with([_FakeResp(json_data=data)])
    shareholders, controllers = c.get_shareholders_and_controller("600519")
    assert [s.name for s in shareholders] == ["流通股东A", "流通股东B"]
    # float → 保留 4 位小数 + %
    assert shareholders[0].share_ratio == "12.3457%"
    assert shareholders[0].title == "境内法人"
    assert shareholders[1].share_ratio == "5.5%"

    assert [c0.name for c0 in controllers] == ["实控人甲", "实控人乙"]
    assert controllers[0].role == "legal_rep"
    assert controllers[0].share_ratio == "40%"
    assert controllers[1].share_ratio == ""
    # 校验请求用 SH 前缀
    assert c.session.calls[0][2]["params"]["code"] == "SH600519"


def test_listed_company_scope_finds_keyword():
    data = {"hxtc": [
        {"KEYWORD": "主营业务", "MAINPOINT_CONTENT": "造车"},
        {"KEYWORD": "经营范围", "MAINPOINT_CONTENT": "生产销售白酒" * 100},
    ]}
    c = _listed_client_with([_FakeResp(json_data=data)])
    scope = c.get_company_scope("000858")
    assert scope.startswith("生产销售白酒")
    assert len(scope) == 500   # 截断 500


def test_listed_company_scope_missing_returns_empty():
    c = _listed_client_with([_FakeResp(json_data={"hxtc": []})])
    assert c.get_company_scope("000858") == ""


def test_listed_company_info_legal_rep_from_sjkzr():
    sh_data = {"sjkzr": [{"HOLDER_NAME": "实控人X"}], "sdltgd": [{"HOLDER_NAME": "股东Y"}]}
    scope_data = {"hxtc": [{"KEYWORD": "经营范围", "MAINPOINT_CONTENT": "范围"}]}
    c = _listed_client_with([_FakeResp(json_data=sh_data), _FakeResp(json_data=scope_data)])
    info = c.get_company_info("600519", "贵州茅台")
    assert info.credit_code == "listed_600519"
    assert info.name == "贵州茅台"
    assert info.legal_rep == "实控人X"
    assert info.status == "上市"
    assert info.scope == "范围"


def test_listed_company_info_legal_rep_fallback_to_sdltgd():
    # sjkzr 为空 → 回退第一大流通股东
    sh_data = {"sjkzr": [], "sdltgd": [{"HOLDER_NAME": "第一大股东"}]}
    scope_data = {"hxtc": []}
    c = _listed_client_with([_FakeResp(json_data=sh_data), _FakeResp(json_data=scope_data)])
    info = c.get_company_info("000858")
    assert info.legal_rep == "第一大股东"
    assert info.name == "000858"   # 未传 name → 用 stock_code


def test_listed_fetch_all_merges_controllers_then_shareholders():
    sh_data = {
        "sjkzr": [{"HOLDER_NAME": "实控人"}],
        "sdltgd": [{"HOLDER_NAME": "股东"}],
    }
    scope_data = {"hxtc": []}
    # fetch_all: get_company_info(ShareholderResearch + CoreConception)
    #            + get_shareholders_and_controller(ShareholderResearch)
    c = _listed_client_with([
        _FakeResp(json_data=sh_data),     # get_company_info → ShareholderResearch
        _FakeResp(json_data=scope_data),  # get_company_info → CoreConception
        _FakeResp(json_data=sh_data),     # get_shareholders_and_controller
    ])
    company, persons = c.fetch_all("600519", "茅台")
    assert company is not None
    # controllers 在前，shareholders 在后
    assert persons[0].role == "legal_rep"
    assert persons[0].name == "实控人"
    assert persons[1].role == "shareholder"
    assert persons[1].name == "股东"


# ── 独立运行入口（无 pytest 时） ──────────────────────────────────────────────

def _run_standalone() -> int:
    import inspect

    class _DummyMP:
        def __init__(self):
            self._saved = {}

        def setenv(self, k, v):
            self._saved.setdefault(k, os.environ.get(k))
            os.environ[k] = v

        def delenv(self, k, raising=True):
            self._saved.setdefault(k, os.environ.get(k))
            os.environ.pop(k, None)

        def undo(self):
            for k, v in self._saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        mp = _DummyMP()
        try:
            if "monkeypatch" in inspect.signature(fn).parameters:
                fn(mp)
            else:
                fn()
            print(f"PASS  {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {fn.__name__}: {e}")
            failed += 1
        except Exception as e:  # noqa: BLE001
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
            failed += 1
        finally:
            mp.undo()
    print(f"\n{passed} passed, {failed} failed (total {passed + failed})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_standalone())
