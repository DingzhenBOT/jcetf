"""repository 读函数单测（P3）：get_latest_quote / get_bar_history /
get_max_bar_timestamp / get_breadth_on_date / get_active_mappings。

用临时 SQLite + 手工注入测试数据（复用已有唯一索引），验证读路径。
"""
from datetime import date, datetime

from app.config import get_settings
from app.db import init_db, make_engine, session_scope
from app.db.models.market import MarketBreadth, MarketQuote
from app.db.models.signal_opinion import Signal
from app.repository import mapping_repo, quote_repo, signal_repo


def _setup(tmp_path):
    s = get_settings(force_reload=True)
    s.paths.sqlite_path_abs = tmp_path / "etf_monitor.db"
    s.paths.backup_dir_abs = tmp_path / "backups"
    s.paths.log_dir_abs = tmp_path / "logs"
    eng = make_engine(s)
    init_db(eng, s)
    return s, eng


def _bar_dict(symbol_type, symbol, d, close, source="em"):
    return {
        "data_source": source, "symbol_type": symbol_type, "symbol": symbol,
        "data_kind": "BAR", "timeframe": "1d", "trading_date": d,
        "timestamp": datetime(d.year, d.month, d.day),
        "open": close - 1, "high": close + 1, "low": close - 1, "close": close,
        "previous_close": None, "volume": 1000, "amount": 1e6, "change_percent": 1.0,
        "turnover_rate": None, "main_net_inflow": None, "large_order_inflow": None,
        "rise_count": None, "fall_count": None, "limit_up_count": None, "limit_down_count": None,
        "collected_at": datetime(2024, 1, 10), "source_timestamp": None,
        "metric_source": source, "metric_definition_version": "v1",
        "source_switched": 0, "data_quality_status": "OK",
    }


def test_get_bar_history_and_max_timestamp(tmp_path):
    s, eng = _setup(tmp_path)
    rows = [
        _bar_dict("ETF", "510300", date(2024, 1, 2), 3.8),
        _bar_dict("ETF", "510300", date(2024, 1, 3), 3.9),
        _bar_dict("ETF", "510300", date(2024, 1, 4), 4.0),
    ]
    with session_scope(eng) as session:
        quote_repo.upsert_market_quotes(session, rows)
        bars = quote_repo.get_bar_history(session, "ETF", "510300", date(2024, 1, 1), date(2024, 1, 31))
        assert len(bars) == 3
        # 升序
        assert [b.trading_date.isoformat() for b in bars] == [
            "2024-01-02", "2024-01-03", "2024-01-04"
        ]
        mx = quote_repo.get_max_bar_timestamp(session, "ETF", "510300")
        assert mx.date() == date(2024, 1, 4)
        latest = quote_repo.get_latest_quote(session, "ETF", "510300", data_kind="BAR", timeframe="1d")
        assert latest.close == 4.0


def test_latest_daily_bar_coverage_rejects_partial_newer_date(tmp_path):
    _, eng = _setup(tmp_path)
    codes = ["510300", "510500", "510050"]
    complete_day = date(2024, 1, 4)
    partial_day = date(2024, 1, 5)
    rows = [_bar_dict("ETF", code, complete_day, 3.0 + i) for i, code in enumerate(codes)]
    rows.extend([
        _bar_dict("ETF", "510300", partial_day, 4.0, source="sina"),
        _bar_dict("ETF", "510300", partial_day, 4.0, source="em"),
    ])
    with session_scope(eng) as session:
        quote_repo.upsert_market_quotes(session, rows)
        covered = quote_repo.get_latest_daily_bar_coverage(
            session, "ETF", codes, on_or_before=partial_day, min_coverage_ratio=0.8
        )
        assert covered == (complete_day, 3, 3)

        quote_repo.upsert_market_quotes(session, [
            _bar_dict("ETF", "510500", partial_day, 5.0),
            _bar_dict("ETF", "510050", partial_day, 6.0),
        ])
        covered = quote_repo.get_latest_daily_bar_coverage(
            session, "ETF", codes, on_or_before=partial_day, min_coverage_ratio=0.8
        )
        assert covered == (partial_day, 3, 3)


def test_latest_snapshot_readers_skip_unusable_newer_rows(tmp_path):
    _, eng = _setup(tmp_path)
    d = date(2024, 1, 2)
    valid = _bar_dict("ETF", "510300", d, 3.8, source="em")
    valid.update(data_kind="SNAPSHOT", timeframe="snapshot", change_percent=1.2)
    bad = dict(valid)
    bad.update(
        data_source="sina",
        timestamp=datetime(2024, 1, 2, 0, 1),
        change_percent=99.0,
        data_quality_status="ANOMALY",
    )
    with session_scope(eng) as session:
        quote_repo.upsert_market_quotes(session, [valid, bad])
        changes = quote_repo.get_latest_snapshot_change_map(session, "ETF", ["510300"])
        rows = quote_repo.get_latest_snapshots_batch(session, [("ETF", "510300")])
        assert changes["510300"] == 1.2
        assert rows[("ETF", "510300")].data_source == "em"


def test_previous_comparable_signal_excludes_same_day_other_version(tmp_path):
    _, eng = _setup(tmp_path)
    common = {
        "target_etf": "510300", "signal_type": "OBSERVE", "score": 70.0,
        "confidence": 80.0, "market_regime": "VOLATILE",
    }
    previous = Signal(
        signal_id="prev", strategy_version="v2", generated_at=datetime(2024, 1, 2, 15),
        trading_date=date(2024, 1, 2), **common,
    )
    same_day_old_version = Signal(
        signal_id="same-day-old", strategy_version="v1", generated_at=datetime(2024, 1, 3, 16),
        trading_date=date(2024, 1, 3), **common,
    )
    current = Signal(
        signal_id="current", strategy_version="v2", generated_at=datetime(2024, 1, 3, 15),
        trading_date=date(2024, 1, 3), **common,
    )
    with session_scope(eng) as session:
        session.add_all([previous, same_day_old_version, current])
        session.flush()
        found = signal_repo.get_previous_comparable_signal(session, current)
        assert found is not None and found.signal_id == "prev"


def test_get_bar_history_dedupes_by_configured_source_priority(tmp_path):
    """多源同日 BAR 应服从运行时 preferred 配置，而不是仓储层硬编码。

    get_bar_history / get_max_bar_timestamp / get_latest_quote 都应按
    preferred > fallback 优先级去重，避免 K 线重影 / 最新价跳源。
    """
    s, eng = _setup(tmp_path)
    s.data_source.preferred = "sina"
    s.data_source.fallback = ["ths", "tx", "em"]
    d = date(2024, 1, 2)
    rows = [
        dict(_bar_dict("ETF", "510300", d, 3.8, source="em"), close=3.8),
        dict(_bar_dict("ETF", "510300", d, 3.9, source="sina"), close=3.9),
    ]
    with session_scope(eng) as session:
        quote_repo.upsert_market_quotes(session, rows)

        bars = quote_repo.get_bar_history(session, "ETF", "510300", date(2024, 1, 1), date(2024, 1, 31))
        assert len(bars) == 1, "同一交易日多源应只返回一条"
        assert bars[0].data_source == "sina"
        assert bars[0].close == 3.9

        mx = quote_repo.get_max_bar_timestamp(session, "ETF", "510300")
        assert mx.date() == d

        latest = quote_repo.get_latest_quote(session, "ETF", "510300", data_kind="BAR", timeframe="1d")
        assert latest is not None
        assert latest.data_source == "sina"
        assert latest.close == 3.9


def test_get_bar_history_dedupes_ths_over_em(tmp_path):
    """验证 ths 优先级高于 em（sina 缺失时的 fallback 场景）。"""
    s, eng = _setup(tmp_path)
    s.data_source.preferred = "sina"
    s.data_source.fallback = ["ths", "em"]
    d = date(2024, 1, 2)
    rows = [
        dict(_bar_dict("ETF", "510300", d, 3.8, source="em"), close=3.8),
        dict(_bar_dict("ETF", "510300", d, 3.85, source="ths"), close=3.85),
    ]
    with session_scope(eng) as session:
        quote_repo.upsert_market_quotes(session, rows)
        bars = quote_repo.get_bar_history(session, "ETF", "510300", date(2024, 1, 1), date(2024, 1, 31))
        assert len(bars) == 1
        assert bars[0].data_source == "ths"


def test_get_bar_history_date_filter(tmp_path):
    s, eng = _setup(tmp_path)
    rows = [
        _bar_dict("INDEX", "000300", date(2024, 1, 2), 3200),
        _bar_dict("INDEX", "000300", date(2024, 2, 2), 3300),
    ]
    with session_scope(eng) as session:
        quote_repo.upsert_market_quotes(session, rows)
        bars = quote_repo.get_bar_history(session, "INDEX", "000300", date(2024, 1, 1), date(2024, 1, 31))
        assert len(bars) == 1
        assert bars[0].trading_date == date(2024, 1, 2)


def test_bar_history_filters_anomaly(tmp_path):
    """#67：get_bar_history / get_max_bar_timestamp / get_latest_quote 应过滤 ANOMALY 脏数据。

    复现 512000：最新一行 high<low 被标 ANOMALY，读路径不可见，回退到上一交易日有效数据。
    """
    s, eng = _setup(tmp_path)
    rows = [
        dict(_bar_dict("ETF", "512000", date(2024, 1, 2), 0.535), open=0.5, high=0.55, low=0.50),
        # 最新交易日：开 346 / 收 0.535 / 高 0.525 / 低 0.526（高低颠倒）-> ANOMALY
        dict(_bar_dict("ETF", "512000", date(2024, 1, 3), 0.535),
             open=346.0, high=0.525, low=0.526, data_quality_status="ANOMALY"),
    ]
    with session_scope(eng) as session:
        quote_repo.upsert_market_quotes(session, rows)
        bars = quote_repo.get_bar_history(session, "ETF", "512000", date(2024, 1, 1), date(2024, 1, 31))
        assert len(bars) == 1, "ANOMALY 行应被过滤"
        assert bars[0].trading_date == date(2024, 1, 2)
        # 最新有效 BAR 回退到 01-02
        latest = quote_repo.get_latest_quote(session, "ETF", "512000", data_kind="BAR", timeframe="1d")
        assert latest is not None and latest.trading_date == date(2024, 1, 2)
        # 最大有效时间戳也排除 ANOMALY
        mx = quote_repo.get_max_bar_timestamp(session, "ETF", "512000")
        assert mx.date() == date(2024, 1, 2)


def test_get_breadth_on_date(tmp_path):
    s, eng = _setup(tmp_path)
    with session_scope(eng) as session:
        quote_repo.upsert_breadth(session, {
            "trading_date": date(2024, 1, 3), "timestamp": datetime(2024, 1, 3, 7, 0),
            "total_rise": 2000, "total_fall": 2500, "total_flat": 300,
            "limit_up": 40, "limit_down": 10, "total_amount": 8e10,
            "data_source": "sina", "collected_at": datetime(2024, 1, 3, 7, 0),
            "data_quality_status": "OK",
        })
        b = quote_repo.get_breadth_on_date(session, date(2024, 1, 3))
        assert b is not None and b.total_rise == 2000
        assert quote_repo.get_breadth_on_date(session, date(2024, 1, 9)) is None


def test_get_active_mappings(tmp_path):
    s, eng = _setup(tmp_path)
    with session_scope(eng) as session:
        mapping_repo.upsert_mapping(
            session, etf_code="510300", etf_name="沪深300ETF",
            related_sector_codes=["BK0465"], related_index_code="000300",
            category="宽基", mapping_version="v1",
            valid_from=date(2000, 1, 1), valid_to=None, notes="t",
        )
        maps = mapping_repo.get_active_mappings(session, date(2024, 1, 3))
        assert len(maps) == 1 and maps[0].etf_code == "510300"
        # 过期映射不返回
        mapping_repo.upsert_mapping(
            session, etf_code="510500", etf_name="中证500ETF",
            related_sector_codes=[], related_index_code="000905",
            category="宽基", mapping_version="v1",
            valid_from=date(2000, 1, 1), valid_to=date(2024, 1, 1), notes="expired",
        )
        active = mapping_repo.get_active_mappings(session, date(2024, 6, 1))
        codes = {m.etf_code for m in active}
        assert "510300" in codes and "510500" not in codes


def test_get_sector_quotes_empty_codes(tmp_path):
    s, eng = _setup(tmp_path)
    with session_scope(eng) as session:
        assert quote_repo.get_sector_quotes(session, "INDUSTRY", []) == []
