"""tv_webhook_server.py 测试：鉴权 / 落库去重 / 报文兼容。

真实起一个本地 HTTP 服务（随机端口），用 urllib 打请求，DB 落在临时目录。
不涉及任何外网请求。

    .venv/bin/python -m pytest tests/test_tv_webhook.py -v
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import urllib.error
import urllib.request

import pytest

# 让 `import scripts.tv_webhook_server` 在任意 cwd 下都能工作
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scripts.tv_webhook_server import TvAlertStore, make_server

SECRET = "test-secret-123"


# ---- TvAlertStore 单测 ----

def test_store_save_and_dedupe(tmp_path):
    db = str(tmp_path / "t.db")
    store = TvAlertStore(db)
    raw = '{"symbol":"AAPL","price":294.38}'
    payload = {"symbol": "AAPL", "price": 294.38, "action": "buy"}
    assert store.save_alert(raw, payload) is True     # 首次入库
    assert store.save_alert(raw, payload) is False    # 相同报文去重
    assert store.save_alert(raw + " ", payload) is True  # 报文不同则是新记录
    rows = store.conn.execute(
        "SELECT symbol, action, price FROM tv_alerts").fetchall()
    assert len(rows) == 2
    assert rows[0] == ("AAPL", "buy", 294.38)
    store.close()


def test_store_tolerates_bad_price(tmp_path):
    store = TvAlertStore(str(tmp_path / "t.db"))
    assert store.save_alert("x", {"symbol": "T", "price": "N/A"}) is True
    (price,) = store.conn.execute("SELECT price FROM tv_alerts").fetchone()
    assert price is None
    store.close()


# ---- HTTP 服务集成测试 ----

@pytest.fixture()
def server(tmp_path, monkeypatch):
    """随机端口起一个真实服务，测试结束关闭。"""
    monkeypatch.setenv("TV_WEBHOOK_SECRET", SECRET)
    srv = make_server(port=0, db_path=str(tmp_path / "hook.db"), host="127.0.0.1")
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    srv.db_path = str(tmp_path / "hook.db")
    yield srv
    srv.shutdown()
    srv.store.close()


# 本机可能配了 http_proxy（如 127.0.0.1:1089），必须绕过代理直连测试服务
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _post(srv, path, body: bytes):
    url = f"http://127.0.0.1:{srv.server_address[1]}{path}"
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with _OPENER.open(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def _alert_body(**extra) -> bytes:
    payload = {"symbol": "600519", "exchange": "SSE",
               "price": 1198.02, "action": "buy", "time": "2026-07-02T10:00:00Z",
               "message": "MA金叉"}
    payload.update(extra)
    return json.dumps(payload).encode()


def test_secret_in_json_field_accepted(server):
    code, body = _post(server, "/tv_alert", _alert_body(secret=SECRET))
    assert code == 200 and body == {"ok": True, "dup": False}
    rows = server.store.conn.execute(
        "SELECT symbol, exchange, action, price, raw FROM tv_alerts").fetchall()
    assert len(rows) == 1
    assert rows[0][:4] == ("600519", "SSE", "buy", 1198.02)
    assert SECRET in rows[0][4]  # raw 保留原文（含密钥）但结构化列不含


def test_secret_in_query_param_accepted(server):
    code, body = _post(server, f"/tv_alert?secret={SECRET}", _alert_body())
    assert code == 200 and body["ok"] is True


def test_wrong_or_missing_secret_rejected(server):
    code, _ = _post(server, "/tv_alert", _alert_body(secret="wrong"))
    assert code == 401
    code, _ = _post(server, "/tv_alert", _alert_body())  # 完全没带
    assert code == 401
    # 未通过鉴权的告警不落库
    n = server.store.conn.execute("SELECT COUNT(*) FROM tv_alerts").fetchone()[0]
    assert n == 0


def test_duplicate_alert_flagged_and_not_reinserted(server):
    body = _alert_body(secret=SECRET)
    code1, r1 = _post(server, "/tv_alert", body)
    code2, r2 = _post(server, "/tv_alert", body)
    assert (code1, r1["dup"]) == (200, False)
    assert (code2, r2["dup"]) == (200, True)
    n = server.store.conn.execute("SELECT COUNT(*) FROM tv_alerts").fetchone()[0]
    assert n == 1


def test_plain_text_body_stored_as_message(server):
    code, body = _post(server, f"/tv_alert?secret={SECRET}",
                       "BTCUSDT crossing 60000".encode())
    assert code == 200 and body["ok"] is True
    (msg,) = server.store.conn.execute("SELECT message FROM tv_alerts").fetchone()
    assert msg == "BTCUSDT crossing 60000"


def test_unknown_path_and_get_rejected(server):
    code, _ = _post(server, "/other", _alert_body(secret=SECRET))
    assert code == 404
    url = f"http://127.0.0.1:{server.server_address[1]}/tv_alert"
    try:
        _OPENER.open(url, timeout=5)
        assert False, "GET 应当被拒绝"
    except urllib.error.HTTPError as e:
        assert e.code == 405


def test_missing_server_secret_returns_503(server, monkeypatch):
    monkeypatch.delenv("TV_WEBHOOK_SECRET")
    code, _ = _post(server, "/tv_alert", _alert_body(secret=SECRET))
    assert code == 503
