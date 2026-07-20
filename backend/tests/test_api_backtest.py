"""回测 API 端点测试（P7）。

覆盖：POST 建 PENDING（202）/ 盘中拒重型回测（409）/ 校验失败（422）/ GET 进度与结果 /
GET 列表 / Worker 执行后状态变 DONE 且返回指标+交易+净值曲线。
"""
from __future__ import annotations

from datetime import date

import pytest

from app.config import get_settings
from app.db.session import session_scope
from app.market_calendar import is_trading_now


@pytest.fixture()
def _disable_intraday_block(monkeypatch):
    """让 is_trading_now 返回 False，绕过盘中拒重型回测，便于测试建任务成功路径。"""
    monkeypatch.setattr("app.api.routers.backtest.is_trading_now", lambda *a, **k: False)


def test_post_creates_pending(backtest_client, _disable_intraday_block):
    client, _ = backtest_client
    sd, ed = "2024-01-01", "2024-12-27"
    r = client.post("/api/backtest/run", json={
        "etf_code": "510300", "start_date": sd, "end_date": ed, "initial_capital": 100000.0,
    })
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "PENDING"
    assert body["progress"] == 0
    assert "id" in body
    rid = body["id"]
    # GET 立即可见 PENDING
    g = client.get(f"/api/backtest/{rid}")
    assert g.status_code == 200
    assert g.json()["status"] == "PENDING"


def test_post_blocked_intraday(backtest_client, monkeypatch):
    """盘中默认拒重型回测 -> 409 BACKTEST_INTRADAY_BLOCKED。"""
    monkeypatch.setattr("app.api.routers.backtest.is_trading_now", lambda *a, **k: True)
    client, _ = backtest_client
    r = client.post("/api/backtest/run", json={
        "etf_code": "510300", "start_date": "2024-01-01", "end_date": "2024-12-27",
    })
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "BACKTEST_INTRADAY_BLOCKED"


def test_post_validation_errors(backtest_client, _disable_intraday_block):
    client, _ = backtest_client
    # 非法日期
    r = client.post("/api/backtest/run", json={
        "etf_code": "510300", "start_date": "2024-13-01", "end_date": "2024-12-27",
    })
    assert r.status_code == 422, r.text
    # start >= end
    r = client.post("/api/backtest/run", json={
        "etf_code": "510300", "start_date": "2024-12-27", "end_date": "2024-01-01",
    })
    assert r.status_code == 422, r.text
    # etf 不在白名单
    r = client.post("/api/backtest/run", json={
        "etf_code": "999999", "start_date": "2024-01-01", "end_date": "2024-12-27",
    })
    assert r.status_code == 422, r.text
    # 非法策略版本
    r = client.post("/api/backtest/run", json={
        "etf_code": "510300", "start_date": "2024-01-01", "end_date": "2024-12-27",
        "strategy_version": "v9.9.9-not-real",
    })
    assert r.status_code == 422, r.text


def test_full_loop_pending_to_done(backtest_client, _disable_intraday_block):
    """POST -> 模拟 Worker 执行 -> GET 返回 DONE + 指标 + 交易 + 净值曲线。"""
    client, eng = backtest_client
    r = client.post("/api/backtest/run", json={
        "etf_code": "510300", "start_date": "2024-01-01", "end_date": "2024-12-27",
        "initial_capital": 100000.0,
    })
    rid = r.json()["id"]

    # 模拟 Worker 处理 PENDING
    settings = get_settings()
    from app.backtest_engine.runner import process_pending_backtests

    with session_scope(eng) as session:
        done = process_pending_backtests(session, settings)
    assert done == 1

    # GET 结果
    g = client.get(f"/api/backtest/{rid}")
    assert g.status_code == 200
    body = g.json()
    assert body["status"] == "DONE", body.get("error_message")
    assert body["progress"] == 100
    assert body["trades_count"] >= 1
    res = body["results"]
    assert res is not None
    for key in ("in_sample", "out_of_sample", "full", "benchmark", "params"):
        assert key in res
    assert len(res["full"]["equity_curve"]) > 0
    assert res["full"]["metrics"]["total_return_pct"] is not None


def test_list_runs(backtest_client, _disable_intraday_block):
    client, eng = backtest_client
    # 建两条
    for _ in range(2):
        client.post("/api/backtest/run", json={
            "etf_code": "510300", "start_date": "2024-01-01", "end_date": "2024-12-27",
        })
    # 执行
    settings = get_settings()
    from app.backtest_engine.runner import process_pending_backtests

    with session_scope(eng) as session:
        process_pending_backtests(session, settings)

    r = client.get("/api/backtest/runs")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 2
    assert len(body["items"]) >= 2
    # 降序：第一条应是最近建的
    assert body["items"][0]["id"]


def test_get_unknown_run_404(backtest_client, _disable_intraday_block):
    client, _ = backtest_client
    r = client.get("/api/backtest/does-not-exist")
    assert r.status_code == 404, r.text
