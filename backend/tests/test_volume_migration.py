from datetime import date, datetime

from app.config import get_settings
from app.db import init_db, make_engine, session_scope
from app.db.models.market import MarketQuote
from scripts.normalize_etf_volume_units import apply_migration, migration_summary


def _row(source: str, symbol_type: str, symbol: str, volume: float, cum_volume=None):
    return MarketQuote(
        data_source=source,
        symbol_type=symbol_type,
        symbol=symbol,
        data_kind="BAR",
        timeframe="1d",
        trading_date=date(2024, 1, 2),
        timestamp=datetime(2024, 1, 2, 7, 0),
        close=4.0,
        volume=volume,
        cum_volume=cum_volume,
        collected_at=datetime(2024, 1, 2, 7, 1),
        data_quality_status="OK",
    )


def test_volume_migration_is_scoped_and_idempotent(tmp_path):
    settings = get_settings(force_reload=True)
    settings.paths.sqlite_path_abs = tmp_path / "migration.db"
    engine = make_engine(settings)
    init_db(engine, settings)
    with session_scope(engine) as session:
        session.add_all([
            _row("em", "ETF", "510300", 10.0, 20.0),
            _row("sina", "ETF", "510500", 1000.0),
            _row("gtimg", "INDEX", "000300", 30.0, 40.0),
        ])

    with session_scope(engine) as session:
        assert migration_summary(session)["pending_etf_rows"] == 2
        first = apply_migration(session)
        assert first["updated_lot_rows"] == 1
        assert first["updated_share_rows"] == 1
        second = apply_migration(session)
        assert second["updated_lot_rows"] == 0
        assert second["updated_share_rows"] == 0

    with session_scope(engine) as session:
        em = session.query(MarketQuote).filter_by(symbol="510300").one()
        sina = session.query(MarketQuote).filter_by(symbol="510500").one()
        index = session.query(MarketQuote).filter_by(symbol="000300").one()
        assert (em.volume, em.cum_volume, em.volume_unit) == (1000.0, 2000.0, "shares")
        assert (sina.volume, sina.volume_unit) == (1000.0, "shares")
        assert (index.volume, index.cum_volume, index.volume_unit) == (30.0, 40.0, None)
    engine.dispose()
