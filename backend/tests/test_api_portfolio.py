"""P6 持仓分析端点测试（无状态、不落库）。

复用 conftest 的 api_client（510300=MARKET_RISK_HIGH 且较前一日 OBSERVE score 70 下降、
510500=NO_PARTICIPATE、510050 无信号）+ api_client_quote（额外带 510300 ETF 行情）。
"""
from __future__ import annotations

from typing import Any, Dict, List


def _post(client, positions: List[Dict[str, Any]]):
    return client.post("/api/portfolio/analyze", json={"positions": positions})


def _find(items: List[Dict[str, Any]], code: str) -> Dict[str, Any]:
    for it in items:
        if it["etf_code"] == code:
            return it
    raise AssertionError(f"{code} not in {items}")


def test_analyze_score_drop_returns_reduce(api_client):
    r = _post(api_client, [{"etf_code": "510300", "cost_price": 3.82, "position_percent": 30, "quantity": 10000}])
    assert r.status_code == 200, r.text
    item = _find(r.json()["items"], "510300")
    # 510300 综合分较前一日下降（70->35）-> REDUCE（§9.5）
    assert item["action"] == "REDUCE"
    assert item["suggested_position_text"] is not None
    assert item["review_time"] is not None


def test_analyze_no_participate_returns_reconfirm(api_client):
    r = _post(api_client, [{"etf_code": "510500", "cost_price": 1.0, "position_percent": 10}])
    assert r.status_code == 200, r.text
    item = _find(r.json()["items"], "510500")
    assert item["action"] == "RECONFIRM"


def test_analyze_no_signal_returns_reconfirm(api_client):
    r = _post(api_client, [{"etf_code": "510050", "cost_price": 2.0, "position_percent": 10}])
    assert r.status_code == 200, r.text
    item = _find(r.json()["items"], "510050")
    # 无最新信号 -> 等待重新确认
    assert item["action"] == "RECONFIRM"
    assert item["suggested_position_text"] is None


def test_analyze_etf_not_in_whitelist(api_client):
    r = _post(api_client, [{"etf_code": "999999", "cost_price": 1.0, "position_percent": 10}])
    assert r.status_code == 422, r.text
    assert "999999" in str(r.json()["error"]["details"]["not_allowed"])


def test_analyze_duplicate_etf_rejected(api_client):
    r = _post(api_client, [
        {"etf_code": "510300", "cost_price": 1.0, "position_percent": 10},
        {"etf_code": "510300", "cost_price": 1.0, "position_percent": 10},
    ])
    assert r.status_code == 422, r.text
    assert "510300" in str(r.json()["error"]["details"]["duplicates"])


def test_analyze_position_sum_exceeds_100(api_client):
    r = _post(api_client, [
        {"etf_code": "510300", "cost_price": 1.0, "position_percent": 60},
        {"etf_code": "510500", "cost_price": 1.0, "position_percent": 60},
    ])
    assert r.status_code == 422, r.text


def test_analyze_cost_nonpositive_rejected(api_client):
    r = _post(api_client, [{"etf_code": "510300", "cost_price": 0, "position_percent": 10}])
    assert r.status_code == 422, r.text


def test_analyze_empty_positions_rejected(api_client):
    r = _post(api_client, [])
    assert r.status_code == 422, r.text


def test_analyze_too_many_positions_rejected(api_client):
    positions = [{"etf_code": "510300", "cost_price": 1.0, "position_percent": 1} for _ in range(21)]
    r = _post(api_client, positions)
    assert r.status_code == 422, r.text


def test_analyze_computes_pnl_with_quote(api_client_quote):
    # 510300 ETF 行情 close=4.00；cost 3.82, qty 10000
    r = _post(api_client_quote, [{"etf_code": "510300", "cost_price": 3.82, "position_percent": 30, "quantity": 10000}])
    assert r.status_code == 200, r.text
    item = _find(r.json()["items"], "510300")
    # return% = (4.00-3.82)/3.82*100 ≈ 4.71；pnl = 10000*(4.00-3.82)=1800
    assert item["return_percent"] is not None
    assert abs(item["return_percent"] - 4.71) < 0.05
    assert item["pnl_amount"] is not None
    assert abs(item["pnl_amount"] - 1800.0) < 0.01


def test_analyze_no_quantity_pnl_is_none(api_client_quote):
    r = _post(api_client_quote, [{"etf_code": "510300", "cost_price": 3.82, "position_percent": 30}])
    assert r.status_code == 200, r.text
    item = _find(r.json()["items"], "510300")
    assert item["return_percent"] is not None  # 有行情仍算收益率
    assert item["pnl_amount"] is None          # 无数量不算金额
