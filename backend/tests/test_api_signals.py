"""信号端点测试（P4）：latest / history。

用 api_client fixture（临时库播种 510300 两条信号 + 510500 一条）。
"""
from datetime import date


def test_signals_latest_per_etf_one_row(api_client):
    r = api_client.get("/api/signals/latest")
    assert r.status_code == 200
    body = r.json()
    # 仅 510300 / 510500 有信号（510050 无）
    codes = {s["target_etf"] for s in body}
    assert codes == {"510300", "510500"}
    # 510300 应取 MAX(generated_at) 那条 = MARKET_RISK_HIGH（非更旧的 OBSERVE）
    s300 = next(s for s in body if s["target_etf"] == "510300")
    assert s300["signal_type"] == "MARKET_RISK_HIGH"
    assert s300["signal_type_text"] == "市场风险较高"
    assert s300["position_text"]  # 中文仓位文字非空
    assert s300["failed_rules"] == ["broad_index_missing", "breadth_missing"]


def test_signals_latest_empty_library_no_500(api_client):
    # latest 永远返回列表（即便某些 etf 无信号）。此处整体不抛 500。
    r = api_client.get("/api/signals/latest")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_signals_history_pagination_and_filter(api_client):
    # 510300 有 2 条（不同 trading? 同 trading_date 不同 generated_at）
    r = api_client.get("/api/signals/history?etf_code=510300&limit=10&offset=0")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2
    assert body["limit"] == 10 and body["offset"] == 0
    # 降序：第一条应为 15:10 的 MARKET_RISK_HIGH
    assert body["items"][0]["signal_type"] == "MARKET_RISK_HIGH"

    # etf 过滤：510500 仅 1 条
    r2 = api_client.get("/api/signals/history?etf_code=510500")
    assert r2.json()["total"] == 1

    # trading_date 过滤
    r3 = api_client.get("/api/signals/history?trading_date=2025-07-18")
    assert r3.json()["total"] == 3
    # 非法日期 -> 422
    r4 = api_client.get("/api/signals/history?trading_date=2025-13-99")
    assert r4.status_code == 422

    # 分页越界由 FastAPI Query 校验拦截（limit>200 / offset<0 -> 422）
    assert api_client.get("/api/signals/history?limit=9999").status_code == 422
    assert api_client.get("/api/signals/history?offset=-5").status_code == 422
    # 边界内生效：limit=200 仍成功
    r5 = api_client.get("/api/signals/history?limit=200")
    assert r5.status_code == 200 and r5.json()["limit"] == 200


def test_signals_history_degraded_data_ok(api_client):
    # 降级数据（failed_rules 非空、confidence=55）正常返回，不 500
    r = api_client.get("/api/signals/history?etf_code=510300")
    item = r.json()["items"][0]
    assert item["confidence"] == 55
    assert "broad_index_missing" in item["failed_rules"]
