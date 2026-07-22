"""strategy_engine 单测（P3）。

- compute_composite：全可用 -> 加权；缺失 -> 重归一化 + 降置信（D4）。
- decide_tier：强制分数验证档位映射与降级/否决优先级。
- evaluate_etf：空库集成，返回合法 Signal 形态字典（不抛）。
"""
from datetime import date

from app.risk_engine.engine import RiskEngine
from app.strategy_engine.engine import (
    POSITION_RANGE,
    StrategyEngine,
    compute_composite,
    decide_tier,
)

W = {"market": 0.25, "sector_trend": 0.25, "fund_flow": 0.25, "etf_rs": 0.25}
TH = {"opportunity_enhance": 85, "small_position": 75, "join_observe": 60}
TIERS = set(POSITION_RANGE.keys())


def test_composite_all_available_weighted():
    r = compute_composite(
        {"market": 80, "sector_trend": 80, "fund_flow": 80, "etf_rs": 80}, W
    )
    assert r["composite"] == 80.0
    assert r["missing"] == []
    assert r["confidence"] == 100


def test_composite_missing_renormalized_and_lower_confidence():
    r = compute_composite(
        {"market": 80, "sector_trend": 80, "fund_flow": None, "etf_rs": 80}, W
    )
    # 仅 3 项可用，权重重归一化 -> 仍 80；缺失 1 项 -> 置信 -15
    assert abs(r["composite"] - 80.0) < 1e-9
    assert r["missing"] == ["fund_flow"]
    assert r["confidence"] == 85


def test_composite_none_when_all_missing():
    r = compute_composite(
        {"market": None, "sector_trend": None, "fund_flow": None, "etf_rs": None}, W
    )
    assert r["composite"] is None
    assert r["confidence"] == 100 - 4 * 15


def _risk(**kw):
    base = {"veto": False, "downgrade": False, "high_vol": False, "chase_high": False,
            "reasons": [], "flags": {}}
    base.update(kw)
    return base


def test_tier_opportunity_enhance():
    t = decide_tier(90, "TREND_UP", _risk(),
                    {"score": 80, "consecutive_positive_days": 3}, {"score": 70}, TH)
    assert t == "OPPORTUNITY_ENHANCE"


def test_tier_small_position():
    t = decide_tier(80, "TREND_UP", _risk(),
                    {"score": 50, "consecutive_positive_days": 1}, {"score": 40}, TH)
    assert t == "SMALL_POSITION"


def test_tier_observe():
    t = decide_tier(65, "VOLATILE", _risk(), None, None, TH)
    assert t == "OBSERVE"


def test_tier_no_participate_low():
    t = decide_tier(40, "VOLATILE", _risk(), None, None, TH)
    assert t == "NO_PARTICIPATE"


def test_tier_no_chase_high_priority():
    t = decide_tier(95, "TREND_UP", _risk(chase_high=True), None, {"score": 90}, TH)
    assert t == "NO_CHASE_HIGH"


def test_tier_market_risk_high():
    t = decide_tier(80, "BEAR", _risk(high_vol=True), None, None, TH)
    assert t == "MARKET_RISK_HIGH"


def test_tier_veto_priority():
    risk = RiskEngine({"deny_market_bear_with_missing_data": True,
                        "downgrade_on_chase_high": True}).evaluate(
        {"market_regime": "BEAR", "missing_data": True}
    )
    assert risk["veto"] is True
    t = decide_tier(95, "BEAR", risk, None, {"score": 90}, TH)
    assert t == "NO_PARTICIPATE"  # veto -> 暂不参与


def test_tier_downgrade_lowers_one_tier():
    # 90 本可 OPPORTUNITY_ENHANCE，但 downgrade 下调一档 -> SMALL_POSITION（资金/RS 不强）
    t = decide_tier(90, "TREND_UP", _risk(downgrade=True),
                    {"score": 50, "consecutive_positive_days": 1}, {"score": 40}, TH)
    assert t == "SMALL_POSITION"


# ---- 方案B：量价形态增强（additive，不改变原权重） ----
def test_tier_vp_enhance_small_to_opportunity():
    # SMALL_POSITION + 放量突破 + 相对强弱确认 -> 上调至 OPPORTUNITY_ENHANCE
    vp = {"vp_patterns": ["breakout_volume"]}
    t = decide_tier(80, "TREND_UP", _risk(),
                    {"score": 50, "consecutive_positive_days": 1}, {"score": 70}, TH, vp)
    assert t == "OPPORTUNITY_ENHANCE"


def test_tier_vp_enhance_observe_to_small():
    # OBSERVE + 分段量涨阳线 + 相对强弱确认 -> 上调至 SMALL_POSITION
    vp = {"vp_patterns": ["segment_up"]}
    t = decide_tier(65, "VOLATILE", _risk(), None, {"score": 65}, TH, vp)
    assert t == "SMALL_POSITION"


def test_tier_vp_no_enhance_without_rs():
    # 量价强势但相对强弱不足 -> 不上调
    vp = {"vp_patterns": ["breakout_volume"]}
    t = decide_tier(80, "TREND_UP", _risk(),
                    {"score": 50, "consecutive_positive_days": 1}, {"score": 40}, TH, vp)
    assert t == "SMALL_POSITION"


def test_tier_vp_no_enhance_when_downgrade():
    # 降级命中时量价增强被抑制
    vp = {"vp_patterns": ["breakout_volume"]}
    t = decide_tier(90, "TREND_UP", _risk(downgrade=True),
                    {"score": 50, "consecutive_positive_days": 1}, {"score": 70}, TH, vp)
    assert t == "SMALL_POSITION"


def test_tier_vp_none_preserves_legacy():
    # vp=None 退化为原逻辑，保证历史测试不变
    t = decide_tier(80, "TREND_UP", _risk(),
                    {"score": 50, "consecutive_positive_days": 1}, {"score": 40}, TH)
    assert t == "SMALL_POSITION"


# ---- 方案B+：量价看空形态驱动降档（与上调互斥，看空优先） ----
def test_tier_vp_downgrade_divergence_observe_to_no_participate():
    # OBSERVE + 量价背离 -> 下调至 NO_PARTICIPATE
    vp = {"vp_patterns": ["divergence"], "vp_state": "VOL_LOW_FLAT"}
    t = decide_tier(65, "VOLATILE", _risk(), None, None, TH, vp)
    assert t == "NO_PARTICIPATE"


def test_tier_vp_downgrade_vol_up_fall_small_to_observe():
    # SMALL_POSITION + 放量下跌(出货) -> 下调至 OBSERVE
    vp = {"vp_patterns": [], "vp_state": "VOL_UP_FALL"}
    t = decide_tier(80, "TREND_UP", _risk(),
                    {"score": 50, "consecutive_positive_days": 1}, {"score": 40}, TH, vp)
    assert t == "OBSERVE"


def test_tier_vp_downgrade_anomaly_down_observe_to_no_participate():
    # OBSERVE + 异动放量(下跌方向) -> 下调至 NO_PARTICIPATE
    vp = {"vp_patterns": ["anomaly"], "vp_state": "VOL_UP_FALL"}
    t = decide_tier(65, "VOLATILE", _risk(), None, None, TH, vp)
    assert t == "NO_PARTICIPATE"


def test_tier_vp_anomaly_up_no_downgrade():
    # 异动放量但上涨方向 -> 非看空，不降档（保持原档位）
    vp = {"vp_patterns": ["anomaly"], "vp_state": "VOL_UP_RISE"}
    t = decide_tier(65, "VOLATILE", _risk(), None, None, TH, vp)
    assert t == "OBSERVE"


def test_tier_vp_downgrade_priority_over_enhance():
    # 同含背离与突破：看空优先，下调而非上调
    vp = {"vp_patterns": ["divergence", "breakout_volume"], "vp_state": "VOL_LOW_FLAT"}
    t = decide_tier(65, "VOLATILE", _risk(), None, None, TH, vp)
    assert t == "NO_PARTICIPATE"


def test_tier_vp_downgrade_floor_no_participate():
    # 已在 NO_PARTICIPATE + 看空 -> 不越界，保持 NO_PARTICIPATE
    vp = {"vp_patterns": ["divergence"], "vp_state": "VOL_LOW_FLAT"}
    t = decide_tier(40, "VOLATILE", _risk(), None, None, TH, vp)
    assert t == "NO_PARTICIPATE"


def test_vp_bearish_helper():
    from app.strategy_engine.engine import _vp_bearish

    assert _vp_bearish({"vp_patterns": ["divergence"]}) is True
    assert _vp_bearish({"vp_patterns": [], "vp_state": "VOL_UP_FALL"}) is True
    assert _vp_bearish({"vp_patterns": ["anomaly"], "vp_state": "VOL_DOWN_FALL"}) is True
    # 异动上涨不看成空
    assert _vp_bearish({"vp_patterns": ["anomaly"], "vp_state": "VOL_UP_RISE"}) is False
    # 中性/看多形态不看空
    assert _vp_bearish({"vp_patterns": ["breakout_volume", "segment_up"]}) is False
    assert _vp_bearish({}) is False
    assert _vp_bearish(None) is False


def test_evaluate_etf_marks_vp_downgrade(monkeypatch, tmp_path):
    """方案B+：量价背离(看空)时 evaluate_etf 应下调档位并标记 vp_downgrade。

    用 monkeypatch 把市场环境/板块/资金/风险固定为「OBSERVE 基准」，仅量价分析返回看空背离，
    验证引擎把 OBSERVE 降为 NO_PARTICIPATE 且 triggered_rules 含 vp_downgrade / vp_divergence。
    """
    from datetime import datetime as _dt

    from app.config import get_settings
    from app.db import init_db, make_engine, session_scope
    from app.db.models.market import MarketQuote

    s = get_settings(force_reload=True)
    s.paths.sqlite_path_abs = tmp_path / "etf_monitor.db"
    s.paths.backup_dir_abs = tmp_path / "backups"
    s.paths.log_dir_abs = tmp_path / "logs"
    eng = make_engine(s)
    init_db(eng, s)
    # 至少 1 行 ETF BAR -> etf_df 非空 -> 触发 analyze_volume_price（被 monkeypatch 覆盖）
    with session_scope(eng) as session:
        session.add(MarketQuote(
            data_source="em", symbol_type="ETF", symbol="510300", data_kind="BAR",
            timeframe="1d", trading_date=date(2025, 7, 18), timestamp=_dt(2025, 7, 18, 7, 0, 0),
            open=4.0, high=4.1, low=3.9, close=4.0, previous_close=4.0,
            volume=1e6, amount=4e6, collected_at=_dt(2025, 7, 18, 7, 0, 0),
        ))

    class FakeMapping:
        etf_code = "510300"
        related_index_code = "000300"
        related_sector_codes = ["BK0465"]

    engine = StrategyEngine(s)
    monkeypatch.setattr(engine, "_evaluate_market", lambda *a, **k: (65, "VOLATILE", 0.5, True, True))
    monkeypatch.setattr(engine.sector, "evaluate_sector_trend",
                        lambda *a, **k: {"available": True, "score": 65, "risk_overheat": False})
    monkeypatch.setattr(engine.sector, "evaluate_fund_flow",
                        lambda *a, **k: {"available": True, "score": 65, "consecutive_positive_days": 3})
    monkeypatch.setattr(engine.risk, "evaluate",
                        lambda *a, **k: {"veto": False, "downgrade": False, "high_vol": False,
                                         "chase_high": False, "reasons": [], "flags": {}})
    monkeypatch.setattr("app.strategy_engine.engine.analyze_volume_price",
                        lambda *a, **k: {"vp_patterns": ["divergence"], "vp_state": "VOL_LOW_FLAT",
                                         "vp_state_text": "量价背离", "vp_strength": 35, "vp_anomaly": False})

    with session_scope(eng) as session:
        res = engine.evaluate_etf(session, FakeMapping(), "v2.1.0-test", date(2025, 7, 18))

    assert res["signal_type"] == "NO_PARTICIPATE"  # OBSERVE 被看空降档
    assert "vp_downgrade" in res["triggered_rules"]
    assert "vp_divergence" in res["triggered_rules"]
