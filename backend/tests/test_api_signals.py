"""信号端点测试（P4）：latest / history。

用 api_client fixture（临时库播种 510300 两条信号 + 510500 一条）。
"""
from datetime import date

from app.main import app


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
    assert s300["signal_type_text"] == "市场风险大，先观望"
    assert s300["position_text"]  # 中文仓位文字非空
    assert s300["failed_rules"] == ["broad_index_missing", "breadth_missing"]


def test_signals_latest_empty_library_no_500(api_client):
    # latest 永远返回列表（即便某些 etf 无信号）。此处整体不抛 500。
    r = api_client.get("/api/signals/latest")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_signals_latest_sorted_by_score_desc(api_client):
    # 修复 bug⑥：最新信号列表须按综合分降序（NULL 排末，SQLite DESC 行为）。
    r = api_client.get("/api/signals/latest")
    scores = [s["score"] for s in r.json()]
    non_null = [x for x in scores if x is not None]
    assert non_null == sorted(non_null, reverse=True)
    assert scores == [x for x in scores if x is not None] + [x for x in scores if x is None]


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

    # trading_date 过滤（信号播种在近期基准日，过滤该日应命中全部 3 条）
    r3 = api_client.get(f"/api/signals/history?trading_date={date.today().isoformat()}")
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


def test_stale_signal_excluded_from_latest_but_kept_in_history(api_client):
    # 用户诉求：最新信号超过两天应清除（不作为当前信号），但历史记录保留。
    # 给 510300 插一条 >2 天的过期信号，验证：最新信号不含它，历史仍保留。
    from app.api.deps import get_db
    from app.db.models.signal_opinion import Signal
    from datetime import datetime, timedelta

    override = app.dependency_overrides[get_db]
    gen = override()
    session = next(gen)
    try:
        session.add(
            Signal(
                signal_id="sig-stale-510300",
                strategy_version="v1.0.0-test",
                generated_at=datetime.utcnow() - timedelta(days=10),
                trading_date=date.today() - timedelta(days=10),
                target_etf="510300",
                signal_type="OBSERVE",
                score=70,
                confidence=80,
                market_regime="VOLATILE",
                suggested_action="OBSERVE",
                suggested_position_range=[0, 10],
                supporting_metrics={},
                risk_flags={},
                triggered_rules=[],
                failed_rules=[],
                invalidation_conditions={},
                review_time=datetime.utcnow(),
            )
        )
        session.commit()
    finally:
        try:
            next(gen)
        except StopIteration:
            pass

    # 最新信号：510300 仍取今日 MARKET_RISK_HIGH（过期 OBSERVE 被时效过滤排除，不顶替）
    r = api_client.get("/api/signals/latest")
    s300 = next(s for s in r.json() if s["target_etf"] == "510300")
    assert s300["signal_type"] == "MARKET_RISK_HIGH"

    # 历史：过期信号仍保留（共 3 条：今日 MRH + 今日 OBSERVE + 过期 OBSERVE）
    rh = api_client.get("/api/signals/history?etf_code=510300")
    assert rh.json()["total"] == 3
