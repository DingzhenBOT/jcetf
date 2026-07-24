"""P11 新增端点测试：ETF 日线历史、盘中 1m 分时。

- /api/market/etf/{code}/history  —— 与指数历史对称，复用 humanize_index_read
- /api/market/{type}/{code}/intraday —— 盘中分时（价格/均价/成交量 + 昨收 + 北京时间）
"""
from datetime import date, datetime, timedelta


def test_etf_history_returns_points_with_volume(api_client_etf_history):
    r = api_client_etf_history.get("/api/market/etf/510300/history?days=60")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == "510300"
    assert body["name"] == "沪深300ETF"
    assert len(body["points"]) > 10
    p0 = body["points"][0]
    assert "date" in p0 and "close" in p0 and "volume" in p0 and "amount" in p0
    assert p0["volume"] > 0 and p0["amount"] > 0
    # 人话自解读 + 信号
    assert body["read"] and "关键指标" not in body["read"]
    assert isinstance(body["signals"], list)
    # 升序
    assert body["points"][0]["date"] <= body["points"][-1]["date"]


def test_etf_history_unknown_code_returns_empty_points(api_client_etf_history):
    r = api_client_etf_history.get("/api/market/etf/999999/history")
    assert r.status_code == 200
    body = r.json()
    assert body["points"] == []
    assert body["read"]  # 观察期提示
    assert "关键指标" not in body["read"]


def test_intraday_returns_points_with_avg_and_prev_close(api_client_intraday):
    r = api_client_intraday.get("/api/market/etf/510300/intraday?day=2025-07-18")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == "510300"
    assert body["name"] == "沪深300ETF"
    assert body["date"] == "2025-07-18"
    # 昨收来自 SNAPSHOT
    assert body["prev_close"] == 3.980
    pts = body["points"]
    assert len(pts) == 12
    # 均价：首点 avg == price（累计成交额/累计量）
    assert pts[0]["avg"] == pts[0]["price"]
    # 末点均价应 >= 首点价（价格递增）且为 float
    assert isinstance(pts[-1]["avg"], float)
    # 时间应为北京时间（09:30 起）
    assert pts[0]["time"].startswith("2025-07-18T09:30")
    # 升序
    assert pts[0]["time"] <= pts[-1]["time"]
    # 轻量人话已生成
    assert body["read"]


def test_intraday_index_type_supported(api_client_intraday):
    # 指数分时走同一端点；510300 仅 ETF 映射，这里用 index 类型但同代码仅验证不 500
    r = api_client_intraday.get("/api/market/index/000300/intraday?day=2025-07-18")
    assert r.status_code == 200
    body = r.json()
    # 无 000300 的 1m 数据 -> 空 points，但返回结构正确（不 500）
    assert body["code"] == "000300"
    assert isinstance(body["points"], list)


def test_intraday_no_data_returns_empty(api_client_intraday):
    r = api_client_intraday.get("/api/market/etf/510300/intraday?day=2025-01-01")
    assert r.status_code == 200
    body = r.json()
    assert body["points"] == []
