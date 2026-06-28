"""企查查数据抓取层：通过官方开放平台 API 拉取企业工商信息与人员数据。

环境变量：
    QCC_API_KEY    企查查开放平台 AppKey
    QCC_SECRET_KEY 企查查开放平台 SecretKey（用于生成签名）

API 文档：https://openapi.qcc.com/
"""
from __future__ import annotations

import hashlib
import os
import time
import datetime as dt
from dataclasses import dataclass, field
from typing import List, Optional

import requests

QCC_BASE = "https://api.qichacha.com"


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


class QccClientError(Exception):
    pass


class QccClient:
    """企查查开放平台客户端。"""

    def __init__(self, api_key: str, secret_key: str):
        if not api_key or not secret_key:
            raise QccClientError("企查查 API 需要 QCC_API_KEY 和 QCC_SECRET_KEY，请检查环境变量")
        self.api_key = api_key
        self.secret_key = secret_key
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    def _sign(self, timespan: str) -> str:
        """MD5(AppKey + timespan + SecretKey)，全小写。"""
        raw = self.api_key + timespan + self.secret_key
        return hashlib.md5(raw.encode("utf-8")).hexdigest().lower()

    def _get(self, path: str, params: dict) -> dict:
        timespan = str(int(time.time()))
        headers = {
            "Token": self._sign(timespan),
            "Timespan": timespan,
        }
        url = QCC_BASE + path
        resp = self.session.get(url, params={"key": self.api_key, **params},
                                headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if str(data.get("Status", "")) not in ("200", "true", "True"):
            msg = data.get("Message") or data.get("Reason") or str(data)
            raise QccClientError(f"企查查 API 错误: {msg}")
        return data

    # ── 企业搜索 ──────────────────────────────────────────────────────────

    def search_company(self, keyword: str, page: int = 1) -> list[dict]:
        """按关键词搜索企业，返回简要列表（name, credit_code, legal_rep 等）。"""
        data = self._get("/ECIV4/GetBasicDetailsByName", {
            "searchKey": keyword,
            "pageIndex": page,
            "pageSize": 10,
        })
        items = data.get("Data", {}).get("Result", []) or []
        return [
            {
                "credit_code": item.get("CreditCode", ""),
                "name": item.get("Name", ""),
                "legal_rep": item.get("OperName", ""),
                "status": item.get("Status", ""),
                "established": item.get("StartDate", ""),
                "reg_capital": item.get("RegistCapi", ""),
            }
            for item in items
        ]

    # ── 企业详情 ──────────────────────────────────────────────────────────

    def get_company_detail(self, credit_code: str) -> Optional[CompanyInfo]:
        """按统一社会信用代码拉取企业详情。"""
        import json
        data = self._get("/ECIV4/GetBasicDetailsByCreditCode", {
            "creditCode": credit_code,
        })
        item = data.get("Data", {})
        if not item:
            return None
        return CompanyInfo(
            credit_code=item.get("CreditCode", credit_code),
            name=item.get("Name", ""),
            legal_rep=item.get("OperName", ""),
            reg_capital=item.get("RegistCapi", ""),
            established=item.get("StartDate", ""),
            status=item.get("Status", ""),
            industry=item.get("Industry", ""),
            address=item.get("Address", ""),
            phone=item.get("ContactInfo", {}).get("Tel", "") if isinstance(item.get("ContactInfo"), dict) else "",
            email=item.get("ContactInfo", {}).get("Email", "") if isinstance(item.get("ContactInfo"), dict) else "",
            scope=item.get("Scope", ""),
            raw_json=json.dumps(item, ensure_ascii=False)[:5000],
        )

    # ── 股东信息 ──────────────────────────────────────────────────────────

    def get_shareholders(self, credit_code: str) -> list[PersonInfo]:
        """拉取股东列表。"""
        data = self._get("/ECIV4/GetShareholderInfo", {
            "creditCode": credit_code,
            "pageIndex": 1,
            "pageSize": 50,
        })
        persons = []
        for item in (data.get("Data", {}).get("Result", []) or []):
            persons.append(PersonInfo(
                name=item.get("StockName", ""),
                role="shareholder",
                title="股东",
                share_ratio=item.get("StockPercent", ""),
                invest_amount=item.get("ShouldCapi", ""),
            ))
        return persons

    # ── 高管信息 ──────────────────────────────────────────────────────────

    def get_executives(self, credit_code: str) -> list[PersonInfo]:
        """拉取高管/主要人员列表。"""
        data = self._get("/ECIV4/GetStaffInfo", {
            "creditCode": credit_code,
            "pageIndex": 1,
            "pageSize": 50,
        })
        persons = []
        for item in (data.get("Data", {}).get("Result", []) or []):
            name = item.get("Name", "")
            title = item.get("Job", "")
            role = "legal_rep" if "法定代表人" in title else "executive"
            persons.append(PersonInfo(
                name=name,
                role=role,
                title=title,
            ))
        return persons

    # ── 对外投资 ──────────────────────────────────────────────────────────

    def get_investments(self, credit_code: str) -> list[PersonInfo]:
        """拉取对外投资（该企业作为股东投资的其他公司）。"""
        data = self._get("/ECIV4/GetInvestInfo", {
            "creditCode": credit_code,
            "pageIndex": 1,
            "pageSize": 50,
        })
        persons = []
        for item in (data.get("Data", {}).get("Result", []) or []):
            persons.append(PersonInfo(
                name=item.get("Name", ""),
                role="investor",
                title=f"被投企业（{item.get('FundedRatio', '')}）",
                invest_amount=item.get("Amount", ""),
            ))
        return persons

    # ── 一键拉全量 ────────────────────────────────────────────────────────

    def fetch_all(self, credit_code: str) -> tuple[Optional[CompanyInfo], list[PersonInfo]]:
        """拉取企业详情 + 股东 + 高管，合并返回。"""
        company = self.get_company_detail(credit_code)
        persons: list[PersonInfo] = []

        try:
            persons += self.get_shareholders(credit_code)
        except QccClientError as e:
            print(f"[qcc] 股东信息获取失败 {credit_code}: {e}")

        try:
            persons += self.get_executives(credit_code)
        except QccClientError as e:
            print(f"[qcc] 高管信息获取失败 {credit_code}: {e}")

        # 对外投资可选，失败不阻断
        try:
            persons += self.get_investments(credit_code)
        except Exception:
            pass

        return company, persons


def build_qcc_client(cfg: dict) -> QccClient:
    """从配置中读取 API key 构建客户端。"""
    qcc_cfg = cfg.get("qcc", {})
    api_key = qcc_cfg.get("api_key", "") or os.environ.get("QCC_API_KEY", "")
    secret_key = qcc_cfg.get("secret_key", "") or os.environ.get("QCC_SECRET_KEY", "")
    return QccClient(api_key, secret_key)


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
