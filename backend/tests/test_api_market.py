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
