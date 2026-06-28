"""企业工商数据抓取层：天眼查开放平台 API + 东方财富上市公司接口。

环境变量：
    TYC_TOKEN      天眼查开放平台 Token（在开放平台控制台获取）

天眼查 API 文档：https://open.tianyancha.com/
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import requests

TYC_BASE = "https://open.tianyancha.com/services/open"


@dataclass
class CompanyInfo:
    credit_code: str          # 统一社会信用代码
    name: str                 # 企业名称
    legal_rep: str = ""       # 法定代表人
    reg_capital: str = ""     # 注册资本
    established: str = ""     # 成立日期
    status: str = ""          # 经营状态
    industry: str = ""        # 所属行业
    address: str = ""
    phone: str = ""
    email: str = ""
    scope: str = ""           # 经营范围
    raw_json: str = ""


@dataclass
class PersonInfo:
    name: str
    role: str                 # legal_rep / shareholder / executive / investor
    title: str = ""           # 职位
    share_ratio: str = ""     # 持股比例
    invest_amount: str = ""   # 投资金额


class TycClientError(Exception):
    pass

# 向后兼容旧名称
QccClientError = TycClientError


class TyanchaClient:
    """天眼查开放平台客户端。

    认证：Header 中传 Authorization: token <TYC_TOKEN>
    API 版本：2.0
    """

    def __init__(self, token: str):
        if not token:
            raise TycClientError("天眼查 API 需要 TYC_TOKEN，请检查环境变量")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"token {token}",
            "Content-Type": "application/json",
        })

    def _get(self, path: str, params: dict) -> dict:
        resp = self.session.get(f"{TYC_BASE}{path}", params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("state") != "ok":
            msg = data.get("message") or data.get("reason") or str(data)
            raise TycClientError(f"天眼查 API 错误: {msg}")
        return data

    # ── 企业搜索 ──────────────────────────────────────────────────────────

    def search_company(self, keyword: str, page: int = 1) -> list[dict]:
        """按关键词搜索企业，返回简要列表。"""
        data = self._get("/search/2.0", {"word": keyword, "pageSize": 10, "pageNum": page})
        items = data.get("data", {}).get("items", []) or []
        return [
            {
                "id": str(item.get("id", "")),
                "credit_code": item.get("creditCode", ""),
                "name": item.get("name", ""),
                "legal_rep": item.get("legalPersonName", ""),
                "status": item.get("regStatus", ""),
                "established": item.get("estiblishTime", "")[:10] if item.get("estiblishTime") else "",
                "reg_capital": item.get("regCapital", ""),
            }
            for item in items
        ]

    # ── 企业详情 ──────────────────────────────────────────────────────────

    def get_company_detail(self, company_id: str) -> Optional[CompanyInfo]:
        """按天眼查内部 ID 拉取企业详情。"""
        import json as _json
        data = self._get("/companyinfo/base/2.0", {"id": company_id})
        item = data.get("data") or {}
        if not item:
            return None
        return CompanyInfo(
            credit_code=item.get("creditCode", "") or f"tyc_{company_id}",
            name=item.get("name", ""),
            legal_rep=item.get("legalPersonName", ""),
            reg_capital=item.get("regCapital", ""),
            established=(item.get("estiblishTime", "") or "")[:10],
            status=item.get("regStatus", ""),
            industry=item.get("industry", ""),
            address=item.get("regLocation", ""),
            phone=item.get("phoneNumber", ""),
            email=item.get("email", ""),
            scope=item.get("businessScope", "")[:500],
            raw_json=_json.dumps(item, ensure_ascii=False)[:5000],
        )

    # ── 股东信息 ──────────────────────────────────────────────────────────

    def get_shareholders(self, company_id: str) -> list[PersonInfo]:
        """拉取股东列表（含持股比例、认缴金额）。"""
        data = self._get("/companyinfo/holder/2.0",
                         {"id": company_id, "pageNum": 1, "pageSize": 50})
        persons = []
        for item in (data.get("data", {}).get("result", []) or []):
            name = item.get("name", "") or item.get("holderName", "")
            if not name:
                continue
            persons.append(PersonInfo(
                name=name,
                role="shareholder",
                title=item.get("holderType", "股东"),
                share_ratio=item.get("stockPercent", ""),
                invest_amount=item.get("shouldCapi", ""),
            ))
        return persons

    # ── 高管信息 ──────────────────────────────────────────────────────────

    def get_executives(self, company_id: str) -> list[PersonInfo]:
        """拉取高管/董监高列表（含职位、任职时间）。"""
        data = self._get("/companyinfo/staff/2.0",
                         {"id": company_id, "pageNum": 1, "pageSize": 50})
        persons = []
        for item in (data.get("data", {}).get("result", []) or []):
            name = item.get("name", "") or item.get("staffName", "")
            title = item.get("staffTypeName", "") or item.get("typeJoin", "")
            if not name:
                continue
            role = "legal_rep" if "法定代表人" in title or "董事长" in title else "executive"
            persons.append(PersonInfo(name=name, role=role, title=title))
        return persons

    # ── 对外投资 ──────────────────────────────────────────────────────────

    def get_investments(self, company_id: str) -> list[PersonInfo]:
        """拉取对外投资企业列表。"""
        data = self._get("/companyinfo/invest/2.0",
                         {"id": company_id, "pageNum": 1, "pageSize": 50})
        persons = []
        for item in (data.get("data", {}).get("result", []) or []):
            name = item.get("name", "") or item.get("companyName", "")
            if not name:
                continue
            persons.append(PersonInfo(
                name=name,
                role="investor",
                title=f"被投企业（{item.get('percent', '')}）",
                invest_amount=item.get("amount", ""),
            ))
        return persons

    # ── 一键拉全量（按名称搜索 → 取第一个匹配 → 拉详情）────────────────

    def fetch_by_name(self, company_name: str) -> tuple[Optional[CompanyInfo], list[PersonInfo]]:
        """按公司名搜索后拉取完整信息。"""
        results = self.search_company(company_name)
        if not results:
            raise TycClientError(f"未找到企业: {company_name}")
        company_id = results[0]["id"]
        return self.fetch_by_id(company_id)

    def fetch_by_id(self, company_id: str) -> tuple[Optional[CompanyInfo], list[PersonInfo]]:
        """按天眼查 ID 拉取企业详情 + 股东 + 高管。"""
        company = self.get_company_detail(company_id)
        persons: list[PersonInfo] = []
        for fn, label in [(self.get_executives, "高管"),
                          (self.get_shareholders, "股东"),
                          (self.get_investments, "对外投资")]:
            try:
                persons += fn(company_id)
            except TycClientError as e:
                print(f"[tyc] {label}获取失败 {company_id}: {e}")
        return company, persons


def build_qcc_client(cfg: dict) -> TyanchaClient:
    """从配置中读取天眼查 Token 构建客户端。"""
    qcc_cfg = cfg.get("qcc", {})
    token = qcc_cfg.get("token", "") or os.environ.get("TYC_TOKEN", "")
    return TyanchaClient(token)


# ══════════════════════════════════════════════════════════════════════════════
# 东方财富上市公司高管/股东接口（无需 API key，免费）
# ══════════════════════════════════════════════════════════════════════════════

def _em_secucode(stock_code: str) -> str:
    """把 6 位股票代码转为东方财富 SECUCODE 格式，如 600519 → 600519.SH。"""
    code = stock_code.strip()
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    return f"{code}.SZ"


def _em_prefix_code(stock_code: str) -> str:
    """把 6 位股票代码转为 SH/SZ 前缀格式，如 600519 → SH600519。"""
    code = stock_code.strip()
    prefix = "SH" if code.startswith(("6", "9")) else "SZ"
    return f"{prefix}{code}"


class ListedCompanyClient:
    """东方财富 F10 数据客户端，抓取 A 股上市公司工商信息、十大股东、实际控制人。

    使用两个稳定可用的接口：
    - ShareholderResearch/PageAjax  → 十大流通股东 + 实际控制人
    - CoreConception/PageAjax       → 经营范围/主营业务摘要
    """

    _BASE = "https://emweb.securities.eastmoney.com/PC_HSF10"
    _HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Referer": "https://emweb.securities.eastmoney.com/",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
    }

    def _get(self, path: str, stock_code: str) -> dict:
        prefix_code = _em_prefix_code(stock_code)
        resp = requests.get(f"{self._BASE}/{path}/PageAjax",
                            params={"code": prefix_code},
                            headers=self._HEADERS, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def get_shareholders_and_controller(self, stock_code: str
                                        ) -> tuple[list[PersonInfo], list[PersonInfo]]:
        """返回 (十大流通股东列表, 实际控制人列表)。"""
        data = self._get("ShareholderResearch", stock_code)

        # 十大流通股东
        shareholders: list[PersonInfo] = []
        for row in data.get("sdltgd") or []:
            name = row.get("HOLDER_NAME", "")
            if not name:
                continue
            ratio = row.get("FREE_HOLDNUM_RATIO", "")
            shareholders.append(PersonInfo(
                name=name,
                role="shareholder",
                title=row.get("HOLDER_TYPE", "股东"),
                share_ratio=f"{ratio:.4f}%" if isinstance(ratio, float) else str(ratio),
            ))

        # 实际控制人
        controllers: list[PersonInfo] = []
        for row in data.get("sjkzr") or []:
            name = row.get("HOLDER_NAME", "")
            if not name:
                continue
            ratio = row.get("HOLD_RATIO") or ""
            controllers.append(PersonInfo(
                name=name,
                role="legal_rep",
                title="实际控制人",
                share_ratio=f"{ratio}%" if ratio else "",
            ))

        return shareholders, controllers

    def get_company_scope(self, stock_code: str) -> str:
        """从 CoreConception 接口提取经营范围。"""
        data = self._get("CoreConception", stock_code)
        hxtc = data.get("hxtc") or []
        for item in hxtc:
            if item.get("KEYWORD") == "经营范围":
                return item.get("MAINPOINT_CONTENT", "")[:500]
        return ""

    def get_company_info(self, stock_code: str, name: str = "") -> Optional[CompanyInfo]:
        """组装 CompanyInfo（从 ShareholderResearch 和 CoreConception 拼合）。"""
        import json as _json
        try:
            sh_data = self._get("ShareholderResearch", stock_code)
        except Exception:
            sh_data = {}
        try:
            scope = self.get_company_scope(stock_code)
        except Exception:
            scope = ""

        # sjkzr 实际控制人作为"法人"展示；为空时回退到第一大股东
        sjkzr = sh_data.get("sjkzr") or []
        legal_rep = (sjkzr[0].get("HOLDER_NAME") or "") if sjkzr else ""
        if not legal_rep:
            sdltgd = sh_data.get("sdltgd") or []
            legal_rep = sdltgd[0].get("HOLDER_NAME", "") if sdltgd else ""

        return CompanyInfo(
            credit_code=f"listed_{stock_code}",
            name=name or stock_code,
            legal_rep=legal_rep,
            status="上市",
            scope=scope,
            raw_json=_json.dumps({"sjkzr": sjkzr}, ensure_ascii=False)[:2000],
        )

    def fetch_all(self, stock_code: str, name: str = ""
                  ) -> tuple[Optional[CompanyInfo], list[PersonInfo]]:
        """拉取公司信息 + 十大股东 + 实际控制人，合并返回。"""
        company = None
        persons: list[PersonInfo] = []

        try:
            company = self.get_company_info(stock_code, name)
        except Exception as e:
            print(f"[listed] 公司信息获取失败 {stock_code}: {e}")

        try:
            shareholders, controllers = self.get_shareholders_and_controller(stock_code)
            persons += controllers + shareholders
        except Exception as e:
            print(f"[listed] 股东/控制人获取失败 {stock_code}: {e}")

        return company, persons
