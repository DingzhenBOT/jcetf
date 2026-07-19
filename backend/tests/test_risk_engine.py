"""risk_engine 单测（P3）：veto / downgrade / high_vol / chase_high，受 risk_filter 约束。"""
from app.risk_engine.engine import RiskEngine


def _engine(deny=True, downgrade=True):
    return RiskEngine(
        {
            "deny_market_bear_with_missing_data": deny,
            "downgrade_on_chase_high": downgrade,
        }
    )


def test_veto_on_bear_with_missing_data():
    r = _engine().evaluate({"market_regime": "BEAR", "missing_data": True})
    assert r["veto"] is True
    assert r["flags"]["deny_market_bear_with_missing_data"] is True


def test_no_veto_when_bear_but_data_present():
    r = _engine().evaluate({"market_regime": "BEAR", "missing_data": False})
    assert r["veto"] is False


def test_no_veto_when_switch_disabled():
    r = _engine(deny=False).evaluate({"market_regime": "BEAR", "missing_data": True})
    assert r["veto"] is False


def test_downgrade_on_rsi_overheat():
    r = _engine().evaluate({"rsi14": 85})
    assert r["chase_high"] is True
    assert r["downgrade"] is True


def test_downgrade_on_sector_surge():
    r = _engine().evaluate({"sector_surge": True})
    assert r["chase_high"] is True
    assert r["downgrade"] is True


def test_downgrade_disabled_keeps_no_downgrade():
    r = _engine(downgrade=False).evaluate({"rsi14": 85})
    assert r["chase_high"] is True  # 追高标志仍记录
    assert r["downgrade"] is False  # 但降级被开关关闭


def test_high_vol_from_atr():
    r = _engine().evaluate({"atr_pct": 6.0})
    assert r["high_vol"] is True


def test_high_vol_from_weak_regime():
    r = _engine().evaluate({"market_regime": "WEAK"})
    assert r["high_vol"] is True


def test_clean_metrics_no_flags():
    r = _engine().evaluate({"market_regime": "TREND_UP", "rsi14": 55, "atr_pct": 1.0})
    assert r["veto"] is False
    assert r["downgrade"] is False
    assert r["high_vol"] is False
    assert r["chase_high"] is False
