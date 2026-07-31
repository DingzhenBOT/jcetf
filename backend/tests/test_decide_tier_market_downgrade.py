"""C23 回归：decide_tier 市场弱/高波动为降档修正，不再 blanket MARKET_RISK_HIGH（P3）。

方法论来源：持仓监控告警 / A股每日复盘 / 短线交易 —— 市场弱应降档减权，
仅「大盘 BEAR 且数据缺失」(veto) 才硬 NO_PARTICIPATE。
"""
from app.risk_engine.engine import RiskEngine
from app.strategy_engine.engine import POSITION_RANGE, decide_tier

TH = {"opportunity_enhance": 85, "small_position": 75, "join_observe": 60}
STRONG_FUND = {"score": 80, "consecutive_positive_days": 3}
STRONG_RS = {"score": 70}
WEAK_FUND = {"score": 50, "consecutive_positive_days": 1}
WEAK_RS = {"score": 40}


def _risk(**kw):
    base = {"veto": False, "downgrade": False, "high_vol": False, "chase_high": False,
            "reasons": [], "flags": {}}
    base.update(kw)
    return base


def test_weak_does_not_double_penalize_strong_etf():
    # 市场分已计入 composite；regime 仅作解释，不在档位层再次扣分。
    t = decide_tier(90, "WEAK", _risk(), STRONG_FUND, STRONG_RS, TH)
    assert t == "OPPORTUNITY_ENHANCE"
    assert t in POSITION_RANGE


def test_weak_keeps_same_tier_as_trend_for_same_composite():
    base = decide_tier(78, "TREND_UP", _risk(), WEAK_FUND, WEAK_RS, TH)
    weak = decide_tier(78, "WEAK", _risk(), WEAK_FUND, WEAK_RS, TH)
    assert base == "SMALL_POSITION"
    assert weak == base


def test_bear_does_not_double_penalize_strong_etf():
    # 相同 composite 与个体证据应得到相同档位，BEAR 已体现在 market_score 中。
    t = decide_tier(80, "BEAR", _risk(), STRONG_FUND, STRONG_RS, TH)
    assert t != "MARKET_RISK_HIGH"
    assert t == "SMALL_POSITION"


def test_high_vol_downgrades_small_to_observe():
    # 真实 ATR 高波动是独立风险，采用可解释的离散一档降级。
    base = decide_tier(76, "TREND_UP", _risk(), WEAK_FUND, WEAK_RS, TH)
    hv = decide_tier(
        76, "TREND_UP", _risk(high_vol=True, downgrade=True), WEAK_FUND, WEAK_RS, TH
    )
    assert base == "SMALL_POSITION"
    assert hv == "OBSERVE"


def test_no_blanket_market_risk_high_ever_produced():
    # 强回归：遍历 regime × high_vol × 若干综合分，decide_tier 永不再产出 MARKET_RISK_HIGH。
    regimes = ["STRONG_UP", "TREND_UP", "VOLATILE", "WEAK", "BEAR"]
    for regime in regimes:
        for hv in (True, False):
            for c in (40, 60, 75, 85, 95):
                t = decide_tier(c, regime, _risk(high_vol=hv), STRONG_FUND, STRONG_RS, TH)
                assert t != "MARKET_RISK_HIGH", (regime, hv, c, t)
                assert t in POSITION_RANGE


def test_veto_bear_with_missing_still_no_participate():
    # 仅「大盘 BEAR 且数据缺失」(veto) 才硬 NO_PARTICIPATE（保留现状）。
    risk = RiskEngine({"deny_market_bear_with_missing_data": True}).evaluate(
        {"market_regime": "BEAR", "missing_data": True}
    )
    assert risk["veto"] is True
    t = decide_tier(95, "BEAR", risk, STRONG_FUND, STRONG_RS, TH)
    assert t == "NO_PARTICIPATE"
