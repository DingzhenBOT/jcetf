"""C23：意见接口接受 live/lunch 相位 + trade_plan 透出 + 刷新端点（P4）。

- GET /api/opinions/{etf}?phase=live|lunch 返回 200（不 422）。
- 非法 phase -> 422；未知 ETF -> 404。
- trade_plan 经序列化透出（含三档单调校验）。
- POST /api/signals/{etf}/refresh 触发盘中实时重算（亚秒级，返回本 ETF 最新信号）。
"""
from datetime import date, datetime, time

from app.db.models.signal_opinion import Opinion
from app.main import app


def test_opinions_accepts_live_and_lunch_phase(api_client):
    for ph in ("live", "lunch", "midday", "pre_close", "post_close"):
        r = api_client.get(f"/api/opinions/510300?phase={ph}")
        assert r.status_code == 200, (ph, r.status_code)
        assert isinstance(r.json()["items"], list)


def test_opinions_invalid_phase_422(api_client):
    r = api_client.get("/api/opinions/510300?phase=bogus")
    assert r.status_code == 422


def test_opinions_unknown_etf_404(api_client):
    r = api_client.get("/api/opinions/999999?phase=live")
    assert r.status_code == 404


def test_opinions_trade_plan_serialized(api_client):
    # 直接插一条带 trade_plan 的 post_close 意见，验证序列化透出
    from app.api.deps import get_db

    gen = app.dependency_overrides[get_db]()
    session = next(gen)
    try:
        session.add(Opinion(
            opinion_id="op-tp", signal_id="sig-510300-MARKET_RISK_HIGH",
            generated_at=datetime.combine(date.today(), time(15, 10)),
            trading_date=date.today(), phase="post_close", title="复盘", content="x",
            input_summary={}, template_version="v1", model_version=None,
            trade_plan={
                "breakout_price": 4.05, "breakout_cond": "c",
                "add_price": 3.9, "add_cond": "a",
                "stop_price": 3.7, "stop_cond": "s",
                "expectation_low": 3.95, "expectation_high": 4.1,
                "regime_tomorrow": "偏多", "notes": [],
            },
        ))
        session.commit()
    finally:
        try:
            next(gen)
        except StopIteration:
            pass

    r = api_client.get("/api/opinions/510300?phase=post_close")
    assert r.status_code == 200
    items = r.json()["items"]
    tp_rows = [it for it in items if it["opinion_id"] == "op-tp"]
    assert tp_rows, "插入的 trade_plan 意见应被返回"
    tp = tp_rows[0]["trade_plan"]
    assert tp["breakout_price"] == 4.05
    assert tp["breakout_price"] > tp["add_price"] > tp["stop_price"]


def test_signal_refresh_endpoint_returns_live_signal(api_client):
    # 想立刻查看意见：按需重算该 ETF 盘中实时信号（worker 未持锁时 200）。
    # 若 worker 正写库返回 409，均属正常。
    r = api_client.post("/api/signals/510300/refresh")
    assert r.status_code in (200, 409)
    if r.status_code == 200:
        body = r.json()
        assert body["target_etf"] == "510300"
        assert "signal_id" in body
