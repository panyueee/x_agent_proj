# -*- coding: utf-8 -*-
"""独立政策事件库 —— 冻结契约（schema + 受控词表 + 预测契约）。

服务于 persona 方法D：给分析师（罗志恒/明明/张瑜…）的政策预测打分，需要一把
客观真值尺。本模块是 **所有下游脚本/子模块共享的单一契约**，冻结后不得随意改动：
  - 货币灌入脚本   scripts/load_policy_monetary.py     写 policy_events
  - 会议日历脚本   scripts/load_policy_calendar.py     写 policy_events
  - 公告适配器     scripts/load_policy_announcements.py 写 policy_events
  - 预测抽取(sonnet) 产出 Prediction JSON（见下 SCHEMA）
  - 匹配打分       x_agent/persona/policy_score.py     读 policy_events + Prediction

设计要点（point-in-time / 防泄漏）：
  - announce_date（公布日）与 effective_date（生效日）**必须分开**：打分只看
    announce_date 之后的事件，降准常宣布后隔几天生效。
  - event_type：scheduled（日历型，时点已知，预测内容）/ discretionary（相机型，
    时点本身即预测）。
  - region：CN / US / INTL —— 顶层按支柱筛（中国政府 / 美国政府 / 国际重大事件）。

并发安全：x_agent.db 是 WAL，且有 tgb 抓取在并发写 tweets 表。本模块所有 **写**
连接都设 busy_timeout（默认 30s），只加新表 policy_events，绝不动现有表。
所有脚本一律写 output/x_agent.db，**绝不写 0 字节的根目录 x_agent.db**。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import datetime as dt
from dataclasses import dataclass, field, asdict
from typing import Optional

# ── 受控词表（load-bearing：匹配层按 region+category+action+direction 比对，
#    三方 loader/extractor/matcher 必须共用同一套码，否则零匹配而命中率变噪声）──

REGIONS = {"CN", "US", "INTL"}

# 顶层功能 taxonomy（与 region 正交）
CATEGORIES = {
    "货币",   # 央行利率/准备金/公开市场（PBOC / FOMC / ECB / BOJ …）
    "财政",   # 赤字率/专项债/特别国债/减税/预算/债务上限/支出法案
    "监管",   # 资本市场改革/地产/产业/反垄断/出口管制/实体清单
    "会议",   # 两会/政治局/中央经济工作会议/国常会
    "贸易",   # 关税/贸易协定/制裁（美方为主）
    "政治",   # 选举/政府停摆
    "地缘",   # 战争/冲突/地缘制裁
    "大宗",   # OPEC+ 产量/能源冲击
    "主权",   # 主权信用评级/G7/G20/IMF
}

EVENT_TYPES = {"scheduled", "discretionary"}

# 方向：cut=降息/降准, hike=加息/升准, hold=按兵不动,
#       expand=扩表/QE/增发, contract=缩表/QT/收紧, na=不适用
DIRECTIONS = {"cut", "hike", "hold", "expand", "contract", "na"}

# 货币类 action 码 —— **严格**：extractor 与 loader 必须逐字一致（匹配靠它）
MONETARY_ACTIONS = {
    "LPR_1Y",    # 1 年期 LPR
    "LPR_5Y",    # 5 年期以上 LPR
    "RRR",       # 存款准备金率（降准/升准）
    "MLF_RATE",  # 中期借贷便利利率（暂无结构化源，公告补）
    "OMO_7D",    # 7 天逆回购操作利率（暂无结构化源，公告补）
    "FED_FUNDS", # 美联储联邦基金目标利率
    "FED_QT",    # 缩表
    "FED_QE",    # 扩表
    "ECB_RATE",  # 欧洲央行主要再融资/存款便利利率
    "BOJ_RATE",  # 日本央行政策利率
    "BOE_RATE",  # 英格兰央行 Bank Rate
}
# 其余 action（财政/监管/贸易…）为自由文本，但建议用简短英文码，如
# DEFICIT_RATIO / SPECIAL_BOND / SPECIAL_TREASURY / TAX_CUT /
# TARIFF / EXPORT_CONTROL / ENTITY_LIST。

SOURCE_TIERS = {
    "official_primary",    # 一手官方原文（PBOC/MOF/gov.cn/Fed 公告）
    "official_secondary",  # 官方转述/官媒
    "structured_feed",     # akshare 等结构化真值序列
    "media",               # 一般媒体
    "manual",              # 人工录入
}

VERIFICATION_STATUSES = {"verified", "auto", "unverified"}

# ── DDL（storage.py 会 import 追加进 _SCHEMA；脚本亦可 ensure_schema）──

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS policy_events(
  id                  TEXT PRIMARY KEY,   -- 身份哈希（不含数值，数值修订 → 覆盖同一行）
  region              TEXT,               -- CN / US / INTL
  category            TEXT,               -- 货币/财政/监管/会议/贸易/政治/地缘/大宗/主权
  event_type          TEXT,               -- scheduled / discretionary
  issuer              TEXT,               -- PBOC / MOF / FOMC / ECB / BOJ / 国务院 …
  action              TEXT,               -- action 码（货币类严格，见 MONETARY_ACTIONS）
  direction           TEXT,               -- cut / hike / hold / expand / contract / na
  announce_date       TEXT,               -- 公布日 YYYY-MM-DD（打分只看此日之后）
  effective_date      TEXT DEFAULT '',    -- 生效日 YYYY-MM-DD（可空）
  title               TEXT DEFAULT '',    -- 人读短标题
  params_json         TEXT DEFAULT '{}',  -- 数值 JSON，含 delta_pp（百分点变动，供误差计算）
  surprise_flag       INTEGER DEFAULT -1, -- 1 超预期 / 0 符合预期 / -1 未知
  source_url          TEXT DEFAULT '',
  source_tier         TEXT DEFAULT '',    -- 见 SOURCE_TIERS
  verification_status TEXT DEFAULT 'unverified',
  fetched_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_policy_region_cat   ON policy_events(region, category);
CREATE INDEX IF NOT EXISTS idx_policy_announce      ON policy_events(announce_date);
CREATE INDEX IF NOT EXISTS idx_policy_action        ON policy_events(action);
"""

_COLUMNS = [
    "id", "region", "category", "event_type", "issuer", "action", "direction",
    "announce_date", "effective_date", "title", "params_json", "surprise_flag",
    "source_url", "source_tier", "verification_status", "fetched_at",
]


@dataclass
class PolicyEvent:
    """一条离散、带日期、可独立核验的政策动作。"""
    region: str
    category: str
    event_type: str          # scheduled / discretionary
    issuer: str
    action: str              # action 码
    direction: str           # cut / hike / hold / expand / contract / na
    announce_date: str       # YYYY-MM-DD
    effective_date: str = ""
    title: str = ""
    params: dict = field(default_factory=dict)   # 数值；建议含 delta_pp
    surprise_flag: int = -1
    source_url: str = ""
    source_tier: str = ""
    verification_status: str = "unverified"

    def event_id(self) -> str:
        """身份哈希：region|category|action|issuer|announce|effective —— **不含数值**，
        使 akshare 数值修订走 INSERT OR REPLACE 覆盖，而非重复插入（真幂等）。"""
        key = "|".join([
            self.region, self.category, self.action, self.issuer,
            self.announce_date, self.effective_date,
        ])
        return hashlib.md5(key.encode("utf-8")).hexdigest()

    def validate(self) -> list[str]:
        """返回问题列表（空 = 合法）。货币类 action 走严格码校验。"""
        problems = []
        if self.region not in REGIONS:
            problems.append(f"region 非法: {self.region!r}")
        if self.category not in CATEGORIES:
            problems.append(f"category 非法: {self.category!r}")
        if self.event_type not in EVENT_TYPES:
            problems.append(f"event_type 非法: {self.event_type!r}")
        if self.direction not in DIRECTIONS:
            problems.append(f"direction 非法: {self.direction!r}")
        if self.category == "货币" and self.action not in MONETARY_ACTIONS:
            problems.append(f"货币类 action 未在受控码表: {self.action!r}")
        if not _is_iso_date(self.announce_date):
            problems.append(f"announce_date 非 YYYY-MM-DD: {self.announce_date!r}")
        if self.effective_date and not _is_iso_date(self.effective_date):
            problems.append(f"effective_date 非 YYYY-MM-DD: {self.effective_date!r}")
        return problems

    def to_row(self) -> tuple:
        return (
            self.event_id(), self.region, self.category, self.event_type,
            self.issuer, self.action, self.direction, self.announce_date,
            self.effective_date, self.title,
            json.dumps(self.params, ensure_ascii=False),
            int(self.surprise_flag), self.source_url, self.source_tier,
            self.verification_status, dt.datetime.utcnow().isoformat(),
        )


def _is_iso_date(s: str) -> bool:
    try:
        dt.date.fromisoformat(str(s))
        return True
    except (ValueError, TypeError):
        return False


# ── 连接 / 读写工具（脚本与 Store 共用；写连接一律带 busy_timeout）──

def connect_write(path: str, busy_timeout_ms: int = 30_000) -> sqlite3.Connection:
    """打开可写连接并设 busy_timeout（tgb 并发写 + WAL，必须容忍锁等待）。
    不改 journal_mode（沿用库现有 WAL）。"""
    conn = sqlite3.connect(path, timeout=busy_timeout_ms / 1000)
    conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def upsert_event(conn: sqlite3.Connection, event: PolicyEvent,
                 strict: bool = True) -> bool:
    """幂等写入一条政策事件（身份哈希冲突则覆盖）。
    strict=True 时校验不过直接抛错；返回 True=新插入，False=覆盖已有。"""
    problems = event.validate()
    if problems and strict:
        raise ValueError(f"政策事件校验失败 {event.action}@{event.announce_date}: {problems}")
    eid = event.event_id()
    existed = conn.execute(
        "SELECT 1 FROM policy_events WHERE id=?", (eid,)
    ).fetchone() is not None
    placeholders = ",".join("?" * len(_COLUMNS))
    conn.execute(
        f"INSERT OR REPLACE INTO policy_events ({','.join(_COLUMNS)}) "
        f"VALUES ({placeholders})",
        event.to_row(),
    )
    conn.commit()
    return not existed


def query_events(
    conn: sqlite3.Connection,
    region: Optional[str] = None,
    category: Optional[str] = None,
    action: Optional[str] = None,
    after: Optional[str] = None,     # announce_date > after（严格大于，防泄漏）
    before: Optional[str] = None,    # announce_date <= before（数据快照边界）
) -> list[dict]:
    """查询政策事件（dict 列表，params 已解析），按 announce_date 升序。"""
    clauses, args = [], []
    if region:
        clauses.append("region=?"); args.append(region)
    if category:
        clauses.append("category=?"); args.append(category)
    if action:
        clauses.append("action=?"); args.append(action)
    if after:
        clauses.append("announce_date>?"); args.append(after)
    if before:
        clauses.append("announce_date<=?"); args.append(before)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"SELECT {','.join(_COLUMNS)} FROM policy_events {where} "
        f"ORDER BY announce_date ASC",
        args,
    ).fetchall()
    out = []
    for r in rows:
        d = dict(zip(_COLUMNS, r))
        try:
            d["params"] = json.loads(d.pop("params_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            d["params"] = {}
        out.append(d)
    return out


# ── 预测契约（sonnet 抽取产出 / 匹配层消费）────────────────────────────────
#
# 预测 JSON 文件（output/policy/predictions_<person>.json）为 Prediction 列表。
# 严格纪律：
#   - kind="prediction"（"会这样"，可打分）vs "recommendation"（"应该这样"，不打分）
#     必须分开；只有 prediction 才进匹配层。
#   - pred_date 必须是**文章真实发表日**（防泄漏边界，只匹配其后的事件）。
#   - 货币类 action 用 MONETARY_ACTIONS 码；direction 用 DIRECTIONS 码。
#   - value = 预测的**百分点变动**（与事件 params.delta_pp 同口径：LPR 降 10bp = -0.10，
#     降准 50bp = -0.50），无数值预测则 null。
#   - horizon_days = 预测覆盖的时间跨度（如"下季度"≈90），无则 null → 匹配层取快照内最近事件。

PREDICTION_FIELDS = [
    "pred_id", "person", "pred_date", "region", "category", "action",
    "direction", "value", "horizon_days", "kind", "quote", "source_title",
    "source_url", "rationale",
]


@dataclass
class Prediction:
    person: str
    pred_date: str            # YYYY-MM-DD，真实发表日
    region: str
    category: str
    action: str
    direction: str
    kind: str = "prediction"  # prediction | recommendation
    value: Optional[float] = None
    horizon_days: Optional[int] = None
    quote: str = ""
    source_title: str = ""
    source_url: str = ""
    rationale: str = ""
    pred_id: str = ""

    def __post_init__(self):
        if not self.pred_id:
            key = "|".join([self.person, self.pred_date, self.category,
                            self.action, self.direction, self.quote[:40]])
            self.pred_id = hashlib.md5(key.encode("utf-8")).hexdigest()[:16]

    def is_scorable(self) -> bool:
        return self.kind == "prediction"


def save_predictions(path: str, preds: list[Prediction]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([asdict(p) for p in preds], f, ensure_ascii=False, indent=2)


def load_predictions(path: str) -> list[Prediction]:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    out = []
    for d in raw:
        d = {k: d.get(k) for k in PREDICTION_FIELDS if k in d}
        out.append(Prediction(**d))
    return out
