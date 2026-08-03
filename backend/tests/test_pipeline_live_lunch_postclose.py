"""C23：评估流水线三相位（live/lunch/post_close）端到端 + 幂等（P3）。

验证：
- live：Signal.supporting_metrics 含盘中强度/倾向；Opinion 无 trade_plan。
- lunch：产出午盘意见，无 trade_plan。
- post_close：Opinion.trade_plan 含三档价位（止损<加仓<突破）。
- 幂等：重复跑 post_close 不新建重复 Opinion。
"""
from datetime import date, datetime, timedelta

from sqlalchemy import select

from app.config import get_settings
from app.db import init_db, make_engine, session_scope
from app.db.models.market import MarketQuote
from app.db.models.signal_opinion import Opinion, Signal
from app.evaluation.pipeline import post_collection_evaluate
from app.market_calendar import beijing_to_utc
from app.repository import mapping_repo, quote_repo


def _weekdays_ending(end, n):
    out = []
    d = end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return list(reversed(out))


def _seed(tmp_path):
    s = get_settings(force_reload=True)
    s.paths.sqlite_path_abs = tmp_path / "db.sqlite3"
    s.paths.backup_dir_abs = tmp_path / "backups"
    s.paths.log_dir_abs = tmp_path / "logs"
    s.strategy.broad_index_codes = ["000300"]
    eng = make_engine(s)
    init_db(eng, s)

    T = date.today()
    days = _weekdays_ending(T, 25)
    with session_scope(eng) as session:
        mapping_repo.upsert_mapping(
            session, etf_code="510300", etf_name="沪深300ETF",
            related_sector_codes=[], related_index_code="000300", category="宽基",
            mapping_version="v1", valid_from=date(2000, 1, 1), valid_to=None, notes="t",
        )
        rows = []
        for i, d in enumerate(days):
            c = round(3.5 + 0.02 * i, 4)
            rows.append({
                "data_source": "em", "symbol_type": "ETF", "symbol": "510300", "data_kind": "BAR",
                "timeframe": "1d", "trading_date": d, "timestamp": datetime(d.year, d.month, d.day, 15, 0),
                "open": c, "high": c + 0.02, "low": c - 0.02, "close": c, "previous_close": c,
                "volume": 1_000_000, "amount": 2e9, "change_percent": 1.0 if i > 0 else 0.0,
                "turnover_rate": None, "main_net_inflow": None, "large_order_inflow": None,
                "rise_count": None, "fall_count": None, "limit_up_count": None, "limit_down_count": None,
                "collected_at": datetime(d.year, d.month, d.day, 15, 5), "source_timestamp": None,
                "metric_source": "em", "metric_definition_version": "v1", "source_switched": 0,
                "data_quality_status": "OK",
            })
        for i, d in enumerate(days):
            c = round(3900 + 8 * i, 2)
            rows.append({
                "data_source": "em", "symbol_type": "INDEX", "symbol": "000300", "data_kind": "BAR",
                "timeframe": "1d", "trading_date": d, "timestamp": datetime(d.year, d.month, d.day, 15, 0),
                "open": c, "high": c + 2, "low": c - 2, "close": c, "previous_close": c,
                "volume": 1_000_000, "amount": 2e11, "change_percent": 0.2 if i > 0 else 0.0,
                "turnover_rate": None, "main_net_inflow": None, "large_order_inflow": None,
                "rise_count": None, "fall_count": None, "limit_up_count": None, "limit_down_count": None,
                "collected_at": datetime(d.year, d.month, d.day, 15, 5), "source_timestamp": None,
                "metric_source": "em", "metric_definition_version": "v1", "source_switched": 0,
                "data_quality_status": "OK",
            })
        quote_repo.upsert_market_quotes(session, rows)

        # 当日实时 1m（ETF 上行 + 指数微涨）供 live/lunch 算盘中强度
        intraday = []
        base = 4.0
        for i in range(30):
            bj = datetime(T.year, T.month, T.day, 9, 30) + timedelta(minutes=i)
            c = round(base + 0.001 * i, 4)
            intraday.append({
                "data_source": "gtimg", "symbol_type": "ETF", "symbol": "510300", "data_kind": "BAR",
                "timeframe": "1m", "trading_date": T, "timestamp": beijing_to_utc(bj),
                "open": c, "high": c + 0.002, "low": c - 0.002, "close": c, "previous_close": 3.98,
                "volume": 100_000 + i * 1_000, "amount": None, "change_percent": None,
                "turnover_rate": None, "main_net_inflow": None, "large_order_inflow": None,
                "rise_count": None, "fall_count": None, "limit_up_count": None, "limit_down_count": None,
                "collected_at": beijing_to_utc(bj), "source_timestamp": None,
                "metric_source": "gtimg", "metric_definition_version": "v1", "source_switched": 0,
                "data_quality_status": "OK",
            })
            base += 0.001
        for i in range(30):
            bj = datetime(T.year, T.month, T.day, 9, 30) + timedelta(minutes=i)
            c = round(3950 + 0.2 * i, 2)
            intraday.append({
                "data_source": "gtimg", "symbol_type": "INDEX", "symbol": "000300", "data_kind": "BAR",
                "timeframe": "1m", "trading_date": T, "timestamp": beijing_to_utc(bj),
                "open": c, "high": c, "low": c, "close": c, "previous_close": 3950,
                "volume": 1_000_000, "amount": 1e9, "change_percent": None,
                "turnover_rate": None, "main_net_inflow": None, "large_order_inflow": None,
                "rise_count": None, "fall_count": None, "limit_up_count": None, "limit_down_count": None,
                "collected_at": beijing_to_utc(bj), "source_timestamp": None,
                "metric_source": "gtimg", "metric_definition_version": "v1", "source_switched": 0,
                "data_quality_status": "OK",
            })
        quote_repo.upsert_market_quotes(session, intraday)

        # 当日 SNAPSHOT（含昨收）供盘中动量修正
        quote_repo.upsert_market_quotes(session, [{
            "data_source": "gtimg", "symbol_type": "ETF", "symbol": "510300", "data_kind": "SNAPSHOT",
            "timeframe": "snapshot", "trading_date": T, "timestamp": beijing_to_utc(datetime(T.year, T.month, T.day, 11, 0)),
            "open": 3.98, "high": 4.02, "low": 3.97, "close": 4.01, "previous_close": 3.98,
            "volume": 8_000_000, "amount": 3.2e10, "change_percent": 0.75,
            "turnover_rate": None, "main_net_inflow": None, "large_order_inflow": None,
            "rise_count": None, "fall_count": None, "limit_up_count": None, "limit_down_count": None,
            "collected_at": beijing_to_utc(datetime(T.year, T.month, T.day, 11, 5)), "source_timestamp": None,
            "metric_source": "gtimg", "metric_definition_version": "v1", "source_switched": 0,
            "data_quality_status": "OK",
        }])

    return s, eng, T


def test_pipeline_live_produces_intraday_opinion(tmp_path):
    s, eng, T = _seed(tmp_path)
    with session_scope(eng) as session:
        res = post_collection_evaluate(session, s, phase="live", as_of=T)
    assert res["signals_written"] >= 1
    assert res["opinions_written"] >= 1
    with session_scope(eng) as session:
        o = session.execute(select(Opinion).where(Opinion.phase == "live")).scalars().first()
        assert o is not None
        assert o.trade_plan is None  # live 无 trade_plan
        assert "盘中实时" in o.content
        sig = session.execute(
            select(Signal).where(Signal.target_etf == "510300", Signal.phase == "live")
        ).scalars().first()
        assert sig is not None
        sm = sig.supporting_metrics or {}
        assert sm.get("intraday_strength") is not None
        assert sm.get("intraday_lean") in ("看多", "看空", "中性")


def test_pipeline_live_keeps_only_current_opinion_per_etf(tmp_path):
    s, eng, T = _seed(tmp_path)
    with session_scope(eng) as session:
        post_collection_evaluate(session, s, phase="live", as_of=T - timedelta(days=1))
    with session_scope(eng) as session:
        result = post_collection_evaluate(session, s, phase="live", as_of=T)
        live = session.execute(select(Opinion).where(Opinion.phase == "live")).scalars().all()
        assert len(live) == 1
        assert live[0].trading_date == T
        assert result["live_opinions_pruned"] == 1


def test_pipeline_lunch_produces_opinion(tmp_path):
    s, eng, T = _seed(tmp_path)
    with session_scope(eng) as session:
        res = post_collection_evaluate(session, s, phase="lunch", as_of=T)
    assert res["opinions_written"] >= 1
    with session_scope(eng) as session:
        o = session.execute(select(Opinion).where(Opinion.phase == "lunch")).scalars().first()
        assert o is not None
        assert o.trade_plan is None
        assert "午盘" in o.content


def test_pipeline_post_close_has_trade_plan(tmp_path):
    s, eng, T = _seed(tmp_path)
    with session_scope(eng) as session:
        res = post_collection_evaluate(session, s, phase="post_close", as_of=T)
    assert res["opinions_written"] >= 1
    with session_scope(eng) as session:
        o = session.execute(select(Opinion).where(Opinion.phase == "post_close")).scalars().first()
        assert o is not None
        tp = o.trade_plan
        assert tp is not None
        assert tp["breakout_price"] > 0 and tp["add_price"] > 0 and tp["stop_price"] > 0
        assert tp["breakout_price"] > tp["add_price"] > tp["stop_price"]
        assert tp["expectation_low"] < tp["expectation_high"]


def test_post_close_removes_ephemeral_intraday_opinions(tmp_path):
    s, eng, T = _seed(tmp_path)
    with session_scope(eng) as session:
        post_collection_evaluate(session, s, phase="live", as_of=T)
    with session_scope(eng) as session:
        result = post_collection_evaluate(session, s, phase="post_close", as_of=T)
        ephemeral = session.execute(
            select(Opinion).where(Opinion.phase.in_(("live", "pre_market", "midday", "pre_close")))
        ).scalars().all()
        assert ephemeral == []
        assert result["live_opinions_pruned"] == 1


def test_pipeline_post_close_idempotent(tmp_path):
    s, eng, T = _seed(tmp_path)
    with session_scope(eng) as session:
        post_collection_evaluate(session, s, phase="post_close", as_of=T)
    with session_scope(eng) as session:
        n1 = len(session.execute(select(Opinion).where(Opinion.phase == "post_close")).scalars().all())
        res2 = post_collection_evaluate(session, s, phase="post_close", as_of=T)
    assert res2["opinions_updated"] >= 1
    with session_scope(eng) as session:
        n2 = len(session.execute(select(Opinion).where(Opinion.phase == "post_close")).scalars().all())
    assert n2 == n1  # 不重复新建
