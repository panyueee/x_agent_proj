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
