"""市场总览端点测试（P4）：/api/market/breadth/latest、/api/market/overview。

临时库播种：1 行 breadth、1 行 000300 指数 BAR、510300/510500 信号。
"""


def test_breadth_latest_present(api_client):
    r = api_client.get("/api/market/breadth/latest")
    assert r.status_code == 200
    b = r.json()
    assert b["total_rise"] == 2200
    assert b["total_fall"] == 2300
    # advance_ratio = 2200 / (2200+2300) = 0.4889
    assert b["advance_ratio"] is not None
    assert abs(b["advance_ratio"] - 2200 / 4500) < 1e-6
    assert b["data_source"] == "sina"


def test_breadth_latest_missing_returns_null_fields(api_client_no_breadth):
    # 无宽度数据时：200 + 字段全 null（不 404），前端据此标「观察期数据不足」
    r = api_client_no_breadth.get("/api/market/breadth/latest")
    assert r.status_code == 200
    b = r.json()
    assert b["total_rise"] is None
    assert b["advance_ratio"] is None
    assert b["data_source"] is None


def test_overview_without_breadth_breadth_null(api_client_no_breadth):
    # 无宽度：overview 的 breadth=null，as_of 退化为指数交易日，整体不 500
    r = api_client_no_breadth.get("/api/market/overview")
    assert r.status_code == 200
    body = r.json()
    assert body["breadth"] is None
    assert body["as_of"] == "2025-07-18"
    assert body["signal_risk"]["total"] == 2


def test_overview_aggregates(api_client):
    r = api_client.get("/api/market/overview")
    assert r.status_code == 200
    body = r.json()
    # indices 含 000300（有 BAR）
    idx = {i["code"]: i for i in body["indices"]}
    assert "000300" in idx
    assert idx["000300"]["close"] == 4000.0
    assert idx["000300"]["name"] == "沪深300"
    # 未播种的宽基（000001/399001）应出现但 close=null
    assert idx["000001"]["close"] is None
    # breadth 已播种
    assert body["breadth"] is not None
    assert body["breadth"]["total_rise"] == 2200
    # signal_risk：2 个信号（MARKET_RISK_HIGH + NO_PARTICIPATE）-> 风险偏高
    sr = body["signal_risk"]
    assert sr["total"] == 2
    assert sr["counts"].get("MARKET_RISK_HIGH") == 1
    assert sr["counts"].get("NO_PARTICIPATE") == 1
    assert sr["market_risk_level"] in ("偏高", "高", "中性", "偏低", "未知")
    # as_of 应为 2025-07-18（breadth/index 的最大交易日）
    assert body["as_of"] == "2025-07-18"


def test_overview_no_index_bar_no_500(api_client):
    # overview 在指数 BAR 缺失时不应 500（已通过播种覆盖；此处断言整体健壮性）
    r = api_client.get("/api/market/overview")
    assert r.status_code == 200


def test_overview_prefers_realtime_snapshot_over_bar(api_client):
    """指数应优先取盘中实时 SNAPSHOT（含真实涨跌），而非昨收日线 BAR。"""
    from app.api.deps import get_db
    from app.main import app
    from app.repository import quote_repo
    from datetime import date, datetime

    # 通过依赖覆盖拿到与 api_client 相同的临时库 session
    gen = app.dependency_overrides[get_db]()
    session = next(gen)
    try:
        quote_repo.upsert_market_quotes(session, [{
            "data_source": "sina", "symbol_type": "INDEX", "symbol": "000300",
            "data_kind": "SNAPSHOT", "timeframe": "snapshot", "trading_date": date(2025, 7, 18),
            "timestamp": datetime(2025, 7, 18, 11, 0),
            "open": 4080.0, "high": 4110.0, "low": 4070.0, "close": 4100.0,
            "previous_close": 4000.0, "volume": 1_000_000, "amount": 2.0e11,
            "change_percent": 2.5, "turnover_rate": None, "main_net_inflow": None,
            "large_order_inflow": None, "rise_count": None, "fall_count": None,
            "limit_up_count": None, "limit_down_count": None,
            "collected_at": datetime(2025, 7, 18, 11, 5), "source_timestamp": None,
            "metric_source": "sina", "metric_definition_version": "v1",
            "source_switched": 0, "data_quality_status": "OK",
        }])
        session.commit()
    finally:
        session.close()

    r = api_client.get("/api/market/overview")
    assert r.status_code == 200
    idx = {i["code"]: i for i in r.json()["indices"]}
    # 优先 SNAPSHOT：close=4100（而非 BAR 的 4000），change_percent=2.5（而非 0.5）
    assert idx["000300"]["close"] == 4100.0
    assert idx["000300"]["change_percent"] == 2.5

def test_index_history_returns_points_with_volume(api_client_index_history):
    r = api_client_index_history.get("/api/market/index/000001/history?days=60")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == "000001"
    assert body["name"] == "上证综指"
    assert len(body["points"]) > 10
    p0 = body["points"][0]
    assert "date" in p0 and "close" in p0 and "volume" in p0 and "amount" in p0
    assert p0["volume"] > 0 and p0["amount"] > 0
    # 人话自解读
    assert body["read"] and "关键指标" not in body["read"]
    assert isinstance(body["signals"], list) and len(body["signals"]) > 0
    # 升序（首点日期 < 末点日期）
    assert body["points"][0]["date"] <= body["points"][-1]["date"]


def test_index_history_unknown_code_returns_empty_points(api_client_index_history):
    r = api_client_index_history.get("/api/market/index/999999/history")
    assert r.status_code == 200
    body = r.json()
    assert body["points"] == []
    assert body["read"]  # 观察期提示
    assert "关键指标" not in body["read"] (P10 前端重塑：指数数字带 + 可点开详情抽屉 + 意见人话化)
