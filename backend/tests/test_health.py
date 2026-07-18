"""系统端点冒烟测试（P0）。

用 TestClient 触发 lifespan（配置加载 + 日志初始化 + 目录创建），
验证 /health 与 /ready。P1 会在 /ready 增加 DB ping。
"""
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import init_db, make_engine


@pytest.fixture(scope="module", autouse=True)
def _init_real_db():
    # /ready 现在依赖 DB 存在；P1 的交付物之一就是把库建出来（init_db 幂等）。
    s = get_settings(force_reload=True)
    s.ensure_dirs()
    eng = make_engine(s)
    init_db(eng, s)
    eng.dispose()


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
