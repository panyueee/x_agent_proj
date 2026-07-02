"""TradingView 告警 webhook 接收器（默认不自启，需要时手动拉起）。

功能：接收 TradingView 付费版告警的 POST 推送，校验共享密钥后
落库 output/x_agent.db 的 tv_alerts 表（按原始报文 sha256 去重），并打日志。

== 启动 ==

    export TV_WEBHOOK_SECRET=你的共享密钥        # 必须设置，否则拒绝服务(503)
    .venv/bin/python scripts/tv_webhook_server.py --port 8787 --db output/x_agent.db

== TradingView 侧设置 ==

告警对话框 → 通知 → Webhook URL 填（TradingView 需要公网可达地址，
本地需配合 frp / cloudflared / ngrok 等内网穿透）：

    http://<你的公网地址>:8787/tv_alert?secret=你的共享密钥

TradingView 无法自定义 HTTP header，密钥放 URL query 参数或消息体 JSON
字段 `secret` 均可（二选一，都给也行）。消息体建议写成 JSON，例如：

    {"secret": "你的共享密钥",
     "symbol": "{{ticker}}", "exchange": "{{exchange}}",
     "price": {{close}}, "action": "buy",
     "time": "{{timenow}}", "message": "MA金叉 {{interval}}"}

非 JSON 纯文本消息也能收，整段存入 message 字段（此时密钥只能走 URL）。

== 响应约定 ==

    200 {"ok": true, "dup": false}   # 收到并入库；dup=true 表示重复告警已忽略
    401                              # 密钥错误/缺失
    503                              # 服务端未配置 TV_WEBHOOK_SECRET
    404 / 405                        # 路径或方法不对

== 落库 ==

表 tv_alerts：id(报文sha256, 主键去重) / received_at / symbol / exchange /
action / price / alert_time / message / raw。
下游可从该表读取告警接入 signals 流程。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger("tv_webhook")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tv_alerts(
  id          TEXT PRIMARY KEY,   -- 原始报文 sha256，天然去重
  received_at TEXT,               -- 服务端收到时间(UTC ISO8601)
  symbol      TEXT DEFAULT '',
  exchange    TEXT DEFAULT '',
  action      TEXT DEFAULT '',    -- buy / sell / alert 等，取自 payload.action
  price       REAL,
  alert_time  TEXT DEFAULT '',    -- TradingView 侧时间，取自 payload.time
  message     TEXT DEFAULT '',
  raw         TEXT                -- 原始报文全文
);
CREATE INDEX IF NOT EXISTS idx_tv_alerts_symbol ON tv_alerts(symbol);
"""


class TvAlertStore:
    """tv_alerts 表的读写封装，风格对齐 x_agent/storage.py 的 Store。"""

    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def save_alert(self, raw_body: str, payload: dict) -> bool:
        """入库一条告警。返回 True=新记录，False=重复（已忽略）。"""
        alert_id = hashlib.sha256(raw_body.encode("utf-8")).hexdigest()
        price = payload.get("price")
        try:
            price = float(price) if price is not None else None
        except (TypeError, ValueError):
            price = None
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO tv_alerts"
            "(id, received_at, symbol, exchange, action, price, alert_time, message, raw)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (
                alert_id,
                dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                str(payload.get("symbol", "")),
                str(payload.get("exchange", "")),
                str(payload.get("action", "")),
                price,
                str(payload.get("time", "")),
                str(payload.get("message", "")),
                raw_body,
            ),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def close(self):
        self.conn.close()


class TvWebhookHandler(BaseHTTPRequestHandler):
    """只认 POST /tv_alert，其他一律 404/405。store 由 server 实例持有。"""

    def _reply(self, code: int, body: dict | None = None):
        data = json.dumps(body or {}).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        self._reply(405, {"error": "POST only"})

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/tv_alert":
            self._reply(404, {"error": "not found"})
            return

        # 服务端密钥未配置 → 拒绝服务，避免裸奔
        secret = os.environ.get("TV_WEBHOOK_SECRET", "")
        if not secret:
            self._reply(503, {"error": "TV_WEBHOOK_SECRET not configured"})
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw_body = self.rfile.read(length).decode("utf-8", errors="replace")
        except Exception:
            self._reply(400, {"error": "bad body"})
            return

        # 兼容 JSON 与纯文本两种消息体
        try:
            payload = json.loads(raw_body) if raw_body.strip() else {}
            if not isinstance(payload, dict):
                payload = {"message": raw_body}
        except json.JSONDecodeError:
            payload = {"message": raw_body}

        # 密钥可放 URL query（?secret=xxx）或 JSON 字段 secret，任一匹配即可
        given = parse_qs(parsed.query).get("secret", [None])[0] \
            or str(payload.get("secret") or "")
        if not given or not hmac.compare_digest(str(given), secret):
            logger.warning("拒绝告警：密钥不匹配，来自 %s", self.client_address[0])
            self._reply(401, {"error": "bad secret"})
            return

        payload.pop("secret", None)  # 密钥不落库
        try:
            is_new = self.server.store.save_alert(raw_body, payload)  # type: ignore[attr-defined]
        except Exception as e:
            logger.error("落库失败: %s", e)
            self._reply(500, {"error": "db error"})
            return

        logger.info("收到告警 %s:%s action=%s dup=%s",
                    payload.get("exchange", ""), payload.get("symbol", ""),
                    payload.get("action", ""), not is_new)
        self._reply(200, {"ok": True, "dup": not is_new})

    def log_message(self, fmt, *args):
        logger.debug("%s - %s", self.client_address[0], fmt % args)


def make_server(port: int, db_path: str, host: str = "0.0.0.0") -> ThreadingHTTPServer:
    """构造 HTTP 服务实例（不启动），store 挂在 server 上供 handler 使用。"""
    server = ThreadingHTTPServer((host, port), TvWebhookHandler)
    server.store = TvAlertStore(db_path)  # type: ignore[attr-defined]
    return server


def main(argv=None):
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(description="TradingView 告警 webhook 接收器")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--db", default="output/x_agent.db")
    args = ap.parse_args(argv)

    if not os.environ.get("TV_WEBHOOK_SECRET"):
        logger.error("未设置 TV_WEBHOOK_SECRET 环境变量，拒绝启动（防止无鉴权裸奔）")
        return 1

    server = make_server(args.port, args.db, args.host)
    logger.info("tv_webhook 已启动: http://%s:%d/tv_alert  db=%s",
                args.host, args.port, args.db)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("收到中断，退出")
    finally:
        server.store.close()  # type: ignore[attr-defined]
    return 0


if __name__ == "__main__":
    sys.exit(main())
