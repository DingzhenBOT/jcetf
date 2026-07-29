"""美股指数日线采集单测（#109）：normalize + collect_us_index_history + backfill 纳入 US_INDEX。

- normalize_us_index_bar：存为独立 symbol_type=US_INDEX，与 A股 INDEX 隔离。
- collect_us_index_history：经注入的 us_index_history_fetcher 取数 → 幂等入库（US_INDEX BAR）。
- backfill_history：result 含 us_index 桶且对三大美股代码各回填一次。
"""
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from app.collector.collector import Collector
from app.collector.normalize import normalize_us_index_bar
from app.config import get_settings
from app.data_provider.base import BaseDataProvider
from app.db import init_db, make_engine, session_scope
from app.repository import quote_repo


def _fake_us_history(symbol: str, start: date, end: date) -> pd.DataFrame:
    days = [date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)]
    return pd.DataFrame(
        {
            "date": days,
            "open": [100.0, 101.0, 102.0],
            "high": [101.0, 102.0, 103.0],
            "low": [99.0, 100.0, 101.0],
            "close": [100.0, 101.0, 102.0],
            "volume": [1_000_000, 1_000_000, 1_000_000],
        }
    )


def _naive_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _setup(tmp_path):
    s = get_settings(force_reload=True)
    s.paths.sqlite_path_abs = tmp_path / "etf_monitor.db"
    s.paths.backup_dir_abs = tmp_path / "backups"
    s.paths.log_dir_abs = tmp_path / "logs"
    eng = make_engine(s)
    init_db(eng, s)
    return s, eng


class _FakeProvider(BaseDataProvider):
    def get_trade_calendar(self):
        return []

    def get_index_snapshot(self):
        return pd.DataFrame()

    def get_sector_ranking(self, sector_type):
        return pd.DataFrame()

    def get_etf_snapshot(self):
        return pd.DataFrame()

    def get_etf_history(self, symbol, start, end):
        return pd.DataFrame()

    def get_index_history(self, symbol, start, end):
        return pd.DataFrame()

    def get_sector_history(self, symbol, start, end):
        return pd.DataFrame()

    def get_sector_fund_flow_history(self, symbol, start, end):
        return pd.DataFrame()

    def get_market_breadth_raw(self):
        return pd.DataFrame()


def test_normalize_us_index_bar_marks_us_index():
    df = _fake_us_history(".DJI", date(2026, 7, 1), date(2026, 7, 3))
    rows = normalize_us_index_bar(df, "sina_us", "usDJI", _naive_now())
    assert len(rows) == 3
    assert all(r["symbol_type"] == "US_INDEX" for r in rows)
    assert all(r["symbol"] == "usDJI" for r in rows)
    assert all(r["data_kind"] == "BAR" and r["timeframe"] == "1d" for r in rows)
    # 涨跌幅由收盘价反算（第二根 = 101/100-1 = +1%）
    assert abs(rows[1]["change_percent"] - 1.0) < 1e-6


def test_collect_us_index_history_writes_us_index_bar(tmp_path):
    s, eng = _setup(tmp_path)
    c = Collector(_FakeProvider(), s, us_index_history_fetcher=_fake_us_history)
    with session_scope(eng) as session:
        res = c.collect_us_index_history(session, "usDJI", "20260701", "20260703")
        assert res["status"] == "OK"
        bars = quote_repo.get_bar_history(
            session, "US_INDEX", "usDJI", date(2026, 7, 1), date(2026, 7, 3), "1d", "BAR"
        )
        assert len(bars) == 3
        # A股 INDEX 类型不应被污染
        a = quote_repo.get_bar_history(
            session, "INDEX", "usDJI", date(2026, 7, 1), date(2026, 7, 3), "1d", "BAR"
        )
        assert len(a) == 0


def test_backfill_includes_us_index(tmp_path):
    s, eng = _setup(tmp_path)
    c = Collector(_FakeProvider(), s, us_index_history_fetcher=_fake_us_history)
    with session_scope(eng) as session:
        result = c.backfill_history(session, as_of=date(2026, 7, 3), lookback_days=400)
        assert "us_index" in result
        assert result["us_index"]["ok"] == 3  # usDJI / usIXIC / usINX
