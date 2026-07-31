"""strategy_engine 单测（P3）。

- compute_composite：全可用 -> 加权；缺失 -> 重归一化 + 降置信（D4）。
- decide_tier：强制分数验证档位映射与降级/否决优先级。
- evaluate_etf：空库集成，返回合法 Signal 形态字典（不抛）。
"""
from datetime import date, timedelta

from app.risk_engine.engine import RiskEngine
from app.strategy_engine.engine import (
    POSITION_RANGE,
    StrategyEngine,
    compute_composite,
    decide_tier,
    intraday_momentum_adjustment,
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


def test_tier_bear_high_vol_downgraded_not_blanket():
    # C23 修复回归：市场 BEAR + 高波动 不再一票否决 blanket MARKET_RISK_HIGH，
    # 而是对综合分降档（80 -> 80-18-5=57 -> 无资金/RS 支撑 -> NO_PARTICIPATE）。
    # 核心断言：decide_tier 永不再产出 MARKET_RISK_HIGH。
    t = decide_tier(80, "BEAR", _risk(high_vol=True), None, None, TH)
    assert t != "MARKET_RISK_HIGH"
    assert t == "NO_PARTICIPATE"
    assert t in TIERS


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


# ---- P1：盘中动量修正（让综合分随实时行情移动） ----
def test_intraday_momentum_adjustment_none():
    assert intraday_momentum_adjustment(None, 1.5) is None


def test_intraday_momentum_adjustment_scaling():
    # z = change/vol：+1 日波动率 -> +12 分；-1 日波动率 -> -12 分
    assert abs(intraday_momentum_adjustment(1.5, 1.5) - 12.0) < 1e-9
    assert abs(intraday_momentum_adjustment(-1.5, 1.5) - (-12.0)) < 1e-9


def test_intraday_momentum_adjustment_clamp():
    # 远超上限 -> 封顶 ±18
    assert intraday_momentum_adjustment(10.0, 1.5) == 18.0
    assert intraday_momentum_adjustment(-10.0, 1.5) == -18.0


def test_intraday_momentum_adjustment_vol_fallback():
    # 波动率缺失/过小 -> 回退 1.5%
    assert abs(intraday_momentum_adjustment(1.5, None) - 12.0) < 1e-9
    assert abs(intraday_momentum_adjustment(1.5, 0.05) - 12.0) < 1e-9


def _build_patched_engine(s, monkeypatch):
    """复用方案B+ 测试的打桩：市场环境/板块/资金/风险固定为 OBSERVE 基准（65），仅量价被 mock。"""
    engine = StrategyEngine(s)
    monkeypatch.setattr(engine, "_evaluate_market", lambda *a, **k: (65, "VOLATILE", 0.5, True, True))
    monkeypatch.setattr(engine.sector, "evaluate_sector_trend",
                        lambda *a, **k: {"available": True, "score": 65, "risk_overheat": False})
    monkeypatch.setattr(engine.sector, "evaluate_fund_flow",
                        lambda *a, **k: {"available": True, "score": 65, "consecutive_positive_days": 3})
    monkeypatch.setattr(engine.risk, "evaluate",
                        lambda *a, **k: {"veto": False, "downgrade": False, "high_vol": False,
                                         "chase_high": False, "reasons": [], "flags": {}})
    return engine


class _FakeTodayDate(date):
    @staticmethod
    def today():
        return _FakeTodayDate._TODAY


def test_evaluate_etf_applies_intraday_momentum_live(tmp_path, monkeypatch):
    """P1：当日实时路径下，SNAPSHOT.change_percent 应作为盘中动量修正上修综合分。"""
    from datetime import datetime as _dt

    from app.config import get_settings
    from app.db import init_db, make_engine, session_scope
    from app.db.models.market import MarketQuote
    from app.strategy_engine import engine as eng_mod

    D = date(2026, 7, 25)
    _FakeTodayDate._TODAY = D
    monkeypatch.setattr(eng_mod, "date", _FakeTodayDate)

    s = get_settings(force_reload=True)
    s.paths.sqlite_path_abs = tmp_path / "etf_monitor.db"
    s.paths.backup_dir_abs = tmp_path / "backups"
    s.paths.log_dir_abs = tmp_path / "logs"
    eng = make_engine(s)
    init_db(eng, s)
    with session_scope(eng) as session:
        # ETF BAR 历史（≥3 行供日波动率估计）
        for i, c in enumerate([4.0, 4.02, 3.98, 4.05]):
            td = D - timedelta(days=4 - i)
            session.add(MarketQuote(
                data_source="em", symbol_type="ETF", symbol="510300", data_kind="BAR",
                timeframe="1d", trading_date=td, timestamp=_dt(td.year, td.month, td.day, 7, 0, 0),
                open=c, high=c, low=c, close=c, previous_close=c,
                volume=1e6, amount=4e6, collected_at=_dt(td.year, td.month, td.day, 7, 0, 0),
            ))
        # 当日实时快照：涨 5%
        session.add(MarketQuote(
            data_source="em", symbol_type="ETF", symbol="510300", data_kind="SNAPSHOT",
            timeframe="snapshot", trading_date=D, timestamp=_dt(D.year, D.month, D.day, 10, 30, 0),
            open=4.0, high=4.2, low=3.9, close=4.2, previous_close=4.0,
            change_percent=5.0, volume=1e6, amount=4e6, collected_at=_dt(D.year, D.month, D.day, 10, 30, 0),
        ))

    class FakeMapping:
        etf_code = "510300"
        related_index_code = "000300"
        related_sector_codes = ["BK0465"]

    engine = _build_patched_engine(s, monkeypatch)
    with session_scope(eng) as session:
        res = engine.evaluate_etf(session, FakeMapping(), "v2.2.0-test", D)

    # 纯 BAR 合成应为 65（市场/板块/资金=65，etf_rs 缺失重归一化），盘中动量 +5% 应上修
    assert res["supporting_metrics"]["intraday_change_percent"] == 5.0
    assert res["supporting_metrics"]["intraday_adjust"] is not None and res["supporting_metrics"]["intraday_adjust"] > 0
    assert "intraday_momentum_up" in res["triggered_rules"]
    assert res["score"] is not None and res["score"] > 65


def test_evaluate_etf_no_intraday_on_historical_backfill(tmp_path, monkeypatch):
    """P1：历史回填（as_of<今日）不改综合分，避免与 mom/rs 双重计入，且不影响既有测试。"""
    from datetime import datetime as _dt

    from app.config import get_settings
    from app.db import init_db, make_engine, session_scope
    from app.db.models.market import MarketQuote
    from app.strategy_engine import engine as eng_mod

    D = date(2026, 7, 25)
    _FakeTodayDate._TODAY = D
    monkeypatch.setattr(eng_mod, "date", _FakeTodayDate)

    s = get_settings(force_reload=True)
    s.paths.sqlite_path_abs = tmp_path / "etf_monitor.db"
    s.paths.backup_dir_abs = tmp_path / "backups"
    s.paths.log_dir_abs = tmp_path / "logs"
    eng = make_engine(s)
    init_db(eng, s)
    with session_scope(eng) as session:
        for i, c in enumerate([4.0, 4.02, 3.98, 4.05]):
            td = D - timedelta(days=4 - i)
            session.add(MarketQuote(
                data_source="em", symbol_type="ETF", symbol="510300", data_kind="BAR",
                timeframe="1d", trading_date=td, timestamp=_dt(td.year, td.month, td.day, 7, 0, 0),
                open=c, high=c, low=c, close=c, previous_close=c,
                volume=1e6, amount=4e6, collected_at=_dt(td.year, td.month, td.day, 7, 0, 0),
            ))
        # 即使存在当日快照，历史回填也应忽略
        session.add(MarketQuote(
            data_source="em", symbol_type="ETF", symbol="510300", data_kind="SNAPSHOT",
            timeframe="snapshot", trading_date=D, timestamp=_dt(D.year, D.month, D.day, 10, 30, 0),
            open=4.0, high=4.2, low=3.9, close=4.2, previous_close=4.0,
            change_percent=5.0, volume=1e6, amount=4e6, collected_at=_dt(D.year, D.month, D.day, 10, 30, 0),
        ))

    class FakeMapping:
        etf_code = "510300"
        related_index_code = "000300"
        related_sector_codes = ["BK0465"]

    engine = _build_patched_engine(s, monkeypatch)
    with session_scope(eng) as session:
        # as_of 为过去日期（D-10），走历史回填路径
        res = engine.evaluate_etf(session, FakeMapping(), "v2.2.0-test", D - timedelta(days=10))

    assert res["supporting_metrics"]["intraday_change_percent"] is None
    assert res["supporting_metrics"]["intraday_adjust"] is None
    assert "intraday_momentum_up" not in res["triggered_rules"]
    # 综合分应等于纯 BAR 合成的 65（无盘中修正）
    assert res["score"] is not None and abs(res["score"] - 65.0) < 1e-9


def test_intraday_regime_method_up_flat_down(tmp_path, monkeypatch):
    """_intraday_regime：当日指数上涨->TREND_UP，平盘->VOLATILE，下跌->None（保持日线），无数据->None。"""
    from datetime import datetime as _dt

    from app.config import get_settings
    from app.db import init_db, make_engine, session_scope
    from app.db.models.market import MarketQuote
    from app.strategy_engine import engine as eng_mod

    D = date(2026, 7, 29)
    s = get_settings(force_reload=True)
    s.paths.sqlite_path_abs = tmp_path / "etf_monitor.db"
    s.paths.backup_dir_abs = tmp_path / "backups"
    s.paths.log_dir_abs = tmp_path / "logs"
    s.strategy.broad_index_codes = ["000300"]
    eng = make_engine(s)
    init_db(eng, s)
    eng_obj = StrategyEngine(s)

    def _set_minutes(prices):
        with session_scope(eng) as session:
            session.query(MarketQuote).delete()
            session.commit()
        with session_scope(eng) as session:
            for i, p in enumerate(prices):
                ts = _dt(D.year, D.month, D.day, 1, 30 + i, 0)  # 09:30+ 北京 = 01:30+ UTC
                session.add(MarketQuote(
                    data_source="gtimg", symbol_type="INDEX", symbol="000300", data_kind="BAR",
                    timeframe="1m", trading_date=D, timestamp=ts,
                    open=p, high=p, low=p, close=p, previous_close=p,
                    volume=1e6, amount=1e9, collected_at=ts,
                ))

    _set_minutes([3400, 3420])
    with session_scope(eng) as session:
        assert eng_obj._intraday_regime(session, D, ["000300"]) == "TREND_UP"
    _set_minutes([3400, 3401])
    with session_scope(eng) as session:
        assert eng_obj._intraday_regime(session, D, ["000300"]) == "VOLATILE"
    _set_minutes([3420, 3400])
    with session_scope(eng) as session:
        assert eng_obj._intraday_regime(session, D, ["000300"]) is None
    _set_minutes([])
    with session_scope(eng) as session:
        assert eng_obj._intraday_regime(session, D, ["000300"]) is None


def test_intraday_regime_overrides_stale_weak_daily(tmp_path, monkeypatch):
    """盘中(midday)阶段：当日实时指数上涨时，盘中 regime 抬升，不再被陈旧日线 WEAK 强制 MARKET_RISK_HIGH。

    复现用户反馈「盘中建议全偏弱」：盘中 evaluate_etf 原用昨日日线算 regime（今日日线 15:10 才写），
    若日线偏弱则 decide_tier 强制 MARKET_RISK_HIGH，盘中实时动量修正被压制。修复后盘中改用实时指数。
    """
    from datetime import datetime as _dt

    from app.config import get_settings
    from app.db import init_db, make_engine, session_scope
    from app.db.models.market import MarketQuote
    from app.strategy_engine import engine as eng_mod

    D = date(2026, 7, 29)
    _FakeTodayDate._TODAY = D
    monkeypatch.setattr(eng_mod, "date", _FakeTodayDate)

    s = get_settings(force_reload=True)
    s.paths.sqlite_path_abs = tmp_path / "etf_monitor.db"
    s.paths.backup_dir_abs = tmp_path / "backups"
    s.paths.log_dir_abs = tmp_path / "logs"
    s.strategy.broad_index_codes = ["000300"]
    eng = make_engine(s)
    init_db(eng, s)

    with session_scope(eng) as session:
        # 宽基指数 000300 日线：持续下行 -> 日线 regime=WEAK（昨收视角）
        for i in range(25):
            td = D - timedelta(days=25 - i)
            close = 3500 - 5 * i  # 3500 -> 3380
            session.add(MarketQuote(
                data_source="em", symbol_type="INDEX", symbol="000300", data_kind="BAR",
                timeframe="1d", trading_date=td,
                timestamp=_dt(td.year, td.month, td.day, 7, 0, 0),
                open=close, high=close, low=close, close=close, previous_close=close,
                volume=1e8, amount=1e11, collected_at=_dt(td.year, td.month, td.day, 7, 0, 0),
            ))
        # 当日实时 1m：指数低开高走 +0.6% -> 盘中 regime 应抬升
        for i, p in enumerate([3400, 3420]):
            ts = _dt(D.year, D.month, D.day, 1, 30 + i, 0)
            session.add(MarketQuote(
                data_source="gtimg", symbol_type="INDEX", symbol="000300", data_kind="BAR",
                timeframe="1m", trading_date=D, timestamp=ts,
                open=p, high=p, low=p, close=p, previous_close=p,
                volume=1e6, amount=1e9, collected_at=ts,
            ))
        # ETF 510300 日线（供 etf_rs/动量）+ 当日快照
        for i in range(25):
            td = D - timedelta(days=25 - i)
            session.add(MarketQuote(
                data_source="em", symbol_type="ETF", symbol="510300", data_kind="BAR",
                timeframe="1d", trading_date=td,
                timestamp=_dt(td.year, td.month, td.day, 7, 0, 0),
                open=4.0, high=4.0, low=4.0, close=4.0, previous_close=4.0,
                volume=1e8, amount=4e8, collected_at=_dt(td.year, td.month, td.day, 7, 0, 0),
            ))
        session.add(MarketQuote(
            data_source="em", symbol_type="ETF", symbol="510300", data_kind="SNAPSHOT",
            timeframe="snapshot", trading_date=D,
            timestamp=_dt(D.year, D.month, D.day, 2, 0, 0),
            open=4.0, high=4.0, low=4.0, close=4.0, previous_close=4.0,
            change_percent=0.0, volume=1e6, amount=4e6,
            collected_at=_dt(D.year, D.month, D.day, 2, 0, 0),
        ))

    class FakeMapping:
        etf_code = "510300"
        related_index_code = "000300"
        related_sector_codes = []

    engine = StrategyEngine(s)
    with session_scope(eng) as session:
        res_midday = engine.evaluate_etf(session, FakeMapping(), "v2.2.0-test", D, phase="midday")
        res_post = engine.evaluate_etf(session, FakeMapping(), "v2.2.0-test", D, phase="post_close")

    # 日线 regime 确为 WEAK（post_close 阶段沿用日线）
    assert res_post["market_regime"] == "WEAK"
    # C23 修复回归：弱市下不再 blanket MARKET_RISK_HIGH，而是降档到合理档位
    # （个股层面分析不被丢弃）。核心：signal_type 不再等于 MARKET_RISK_HIGH。
    assert res_post["signal_type"] != "MARKET_RISK_HIGH"
    assert res_post["signal_type"] in POSITION_RANGE
    # 盘中阶段：实时指数上涨 -> regime 抬升，不再强制 MARKET_RISK_HIGH
    assert res_midday["market_regime"] != "WEAK"
    assert res_midday["signal_type"] != "MARKET_RISK_HIGH"
