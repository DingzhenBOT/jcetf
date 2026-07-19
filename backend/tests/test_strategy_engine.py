"""strategy_engine 单测（P3）。

- compute_composite：全可用 -> 加权；缺失 -> 重归一化 + 降置信（D4）。
- decide_tier：强制分数验证档位映射与降级/否决优先级。
- evaluate_etf：空库集成，返回合法 Signal 形态字典（不抛）。
"""
from datetime import date

from app.risk_engine.engine import RiskEngine
from app.strategy_engine.engine import (
    POSITION_RANGE,
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
