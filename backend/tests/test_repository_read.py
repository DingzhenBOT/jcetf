"""repository 读函数单测（P3）：get_latest_quote / get_bar_history /
get_max_bar_timestamp / get_breadth_on_date / get_active_mappings。

用临时 SQLite + 手工注入测试数据（复用已有唯一索引），验证读路径。
"""
from datetime import date, datetime

from app.config import get_settings
from app.db import init_db, make_engine, session_scope
from app.db.models.market import MarketBreadth, MarketQuote
from app.repository import mapping_repo, quote_repo


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
