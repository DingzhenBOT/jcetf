"""系统端点冒烟测试（P0）。

用 TestClient 触发 lifespan（配置加载 + 日志初始化 + 目录创建），
验证 /health 与 /ready。P1 会在 /ready 增加 DB ping。
"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "etf-api"
    assert "timestamp" in body


def test_ready(client):
    r = client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["checks"]["config"] == "ok"
    assert body["checks"]["data_dir_writable"] == "ok"


def test_security_headers_present(client):
    r = client.get("/health")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Frame-Options") == "DENY"
    assert "Content-Security-Policy" in r.headers


def test_request_id_echoed(client):
    r = client.get("/health", headers={"x-request-id": "test-req-123"})
    assert r.headers.get("x-request-id") == "test-req-123"
