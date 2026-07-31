from datetime import date, datetime

from app.config import PortfolioConfig
from app.db.models.signal_opinion import Signal
from app.portfolio.analyzer import _decide_action, _rs_negative


def _signal(*, rs=1.0, tier="SMALL_POSITION", position_range=None, regime="TREND_UP"):
    return Signal(
        signal_id="s",
        strategy_version="v-test",
        generated_at=datetime.utcnow(),
        trading_date=date.today(),
        target_etf="510300",
        signal_type=tier,
        score=80.0,
        market_regime=regime,
        suggested_position_range=position_range or [10, 25],
        supporting_metrics={"etf_rs_20d": rs},
        risk_flags={},
        failed_rules=[],
        invalidation_conditions={},
    )


def test_position_percent_drives_reduce_above_suggested_cap():
    cfg = PortfolioConfig()
    sig = _signal()
    assert _decide_action(sig, None, False, 25.0, cfg) == "HOLD"
    assert _decide_action(sig, None, False, 30.0, cfg) == "REDUCE"


def test_rs_exit_threshold_ignores_near_one_noise():
    cfg = PortfolioConfig(rs_exit_threshold=0.95)
    assert _rs_negative(_signal(rs=0.999), cfg.rs_exit_threshold) is False
    assert _rs_negative(_signal(rs=0.94), cfg.rs_exit_threshold) is True


def test_zero_position_tier_exits_existing_position():
    cfg = PortfolioConfig()
    sig = _signal(tier="NO_PARTICIPATE", position_range=[0, 0])
    assert _decide_action(sig, None, False, 10.0, cfg) == "EXIT"


def test_bear_regime_does_not_blanket_exit_strong_etf_signal():
    cfg = PortfolioConfig()
    sig = _signal(regime="BEAR")
    assert _decide_action(sig, None, False, 20.0, cfg) == "HOLD"


def test_weak_regime_does_not_blanket_reconfirm_strong_etf_signal():
    cfg = PortfolioConfig()
    sig = _signal(regime="WEAK")
    assert _decide_action(sig, None, False, 20.0, cfg) == "HOLD"
