"""P1 数据库测试：建表 / 唯一约束 / 索引 / strategy_version 幂等 / prune / hash。"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app import retention
from app.config import Settings, get_settings
from app.db import init_db, make_engine, ping_db, session_scope
from app.db.base import utcnow
from app.db.models.market import MarketQuote
from app.strategy_versioning import compute_strategy_hash


def _tmp_settings(tmp_path: Path) -> Settings:
    s = get_settings(force_reload=True)
    s.paths.sqlite_path_abs = tmp_path / "etf_monitor.db"
    s.paths.backup_dir_abs = tmp_path / "backups"
    s.paths.log_dir_abs = tmp_path / "logs"
    return s


EXPECTED_TABLES = {
    "market_quote", "market_breadth", "etf_mapping", "strategy_version",
    "signal", "opinion", "task_run_log", "data_source_status",
}


def test_init_db_creates_8_tables(tmp_path):
    s = _tmp_settings(tmp_path)
    eng = make_engine(s)
    init_db(eng, s)
    names = set(inspect(eng).get_table_names())
    assert EXPECTED_TABLES <= names
    eng.dispose()


def test_indexes_exist(tmp_path):
    s = _tmp_settings(tmp_path)
    eng = make_engine(s)
    init_db(eng, s)
    with eng.connect() as conn:
        idx = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='index'")).fetchall()}
    for required in ("idx_quote_symbol_time", "idx_quote_trade_type", "idx_signal_etf_time", "idx_task_name_time", "uq_market_quote"):
        assert required in idx
    eng.dispose()


def test_unique_constraint_market_quote(tmp_path):
    s = _tmp_settings(tmp_path)
    eng = make_engine(s)
    init_db(eng, s)
    base = dict(
        data_source="sina", symbol_type="ETF", symbol="510300", data_kind="SNAPSHOT",
        timeframe="snapshot", trading_date=utcnow().date(), timestamp=utcnow(),
        collected_at=utcnow(),
    )
    with session_scope(eng) as session:
        session.add(MarketQuote(**base))
    with pytest.raises(IntegrityError):
        with session_scope(eng) as session:
            session.add(MarketQuote(**base))  # 同唯一键 -> 冲突
    eng.dispose()


def test_strategy_version_seed_idempotent(tmp_path):
    s = _tmp_settings(tmp_path)
    eng = make_engine(s)
    init_db(eng, s)
    init_db(eng, s)  # 再跑一次应不重复插入
    from app.db.models.mapping import StrategyVersion

    with session_scope(eng) as session:
        rows = session.query(StrategyVersion).all()
        assert len(rows) == 1
        assert rows[0].version.startswith("v1.0.0-")
        assert rows[0].strategy_hash == compute_strategy_hash(
            {
                "composite_weights": s.strategy.composite_weights,
                "thresholds": s.strategy.thresholds,
                "risk_filter": s.strategy.risk_filter,
            },
            {},
        )
    eng.dispose()


def test_prune_deletes_old_snapshot_keeps_bar(tmp_path):
    s = _tmp_settings(tmp_path)
    eng = make_engine(s)
    init_db(eng, s)
    old = utcnow() - timedelta(days=100)
    with session_scope(eng) as session:
        session.add(MarketQuote(
            data_source="sina", symbol_type="ETF", symbol="510300", data_kind="SNAPSHOT",
            timeframe="snapshot", trading_date=old.date(), timestamp=old, collected_at=old,
        ))
        session.add(MarketQuote(
            data_source="sina", symbol_type="ETF", symbol="510300", data_kind="BAR",
            timeframe="1d", trading_date=old.date(), timestamp=old, collected_at=old,
        ))
    out = retention.run_retention(s)
    assert out["deleted_snapshot"] == 1
    assert out["deleted_bar"] == 0
    eng.dispose()


def test_compute_strategy_hash_deterministic():
    p = {"a": 1, "b": 2}
    assert compute_strategy_hash(p, {}) == compute_strategy_hash(p, {})
    assert compute_strategy_hash({"a": 1, "b": 2}, {}) != compute_strategy_hash({"a": 2, "b": 1}, {})


def test_ping_db_lifecycle(tmp_path):
    s = _tmp_settings(tmp_path)
    assert ping_db(s) is False  # 文件不存在
    eng = make_engine(s)
    init_db(eng, s)
    eng.dispose()
    assert ping_db(s) is True
