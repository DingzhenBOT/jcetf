"""美股指数采集单测（C14：首页「美股大盘」面板数据源）。

- normalize_us_index_snapshot：解析腾讯财经 usDJI/usIXIC/usINX，存为 symbol_type=US_INDEX。
- collect_us_indices：经注入的 us_index_fetcher 取数 -> 幂等入库（与 A股 regime 隔离）。
- us_index_fetcher=None -> 跳过（零网络，不抛）。
"""
from datetime import datetime, timezone

import pandas as pd

from app.collector.collector import Collector
from app.collector.normalize import normalize_us_index_snapshot
from app.config import get_settings
from app.data_provider.base import BaseDataProvider
from app.db import init_db, make_engine, session_scope
from app.repository import quote_repo


def _us_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"代码": "usDJI", "名称": "道琼斯", "今开": 51791.37, "最高": 52118.19,
             "最低": 51682.36, "最新价": 51947.25, "昨收": 51711.65, "涨跌幅": 0.46},
            {"代码": "usIXIC", "名称": "纳斯达克", "今开": 25107.38, "最高": 25222.14,
             "最低": 24918.09, "最新价": 24975.82, "昨收": 25137.69, "涨跌幅": -0.64},
            {"代码": "usINX", "名称": "标普500", "今开": 7406.30, "最高": 7439.83,
             "最低": 7406.30, "最新价": 7411.98, "昨收": 7408.30, "涨跌幅": 0.05},
        ]
    )


def _naive_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_normalize_us_index_snapshot_marks_us_index():
    rows = normalize_us_index_snapshot(_us_df(), "gtimg_us", _naive_now())
    assert len(rows) == 3
    r0 = rows[0]
    assert r0["symbol_type"] == "US_INDEX"
    assert r0["symbol"] == "usDJI"
    assert r0["close"] == 51947.25
    assert r0["change_percent"] == 0.46
    assert r0["previous_close"] == 51711.65
    # 涨跌幅可反算兜底：纳斯达克显式 -0.64
    r1 = rows[1]
    assert r1["symbol"] == "usIXIC"
    assert r1["change_percent"] == -0.64


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


def _setup(tmp_path):
    s = get_settings(force_reload=True)
    s.paths.sqlite_path_abs = tmp_path / "etf_monitor.db"
    s.paths.backup_dir_abs = tmp_path / "backups"
    s.paths.log_dir_abs = tmp_path / "logs"
    eng = make_engine(s)
    init_db(eng, s)
    return s, eng


def test_collect_us_indices_writes_us_index_snapshots(tmp_path):
    s, eng = _setup(tmp_path)
    c = Collector(_FakeProvider(), s, us_index_fetcher=lambda codes: _us_df())
    with session_scope(eng) as session:
        res = c.collect_us_indices(session)
        assert res["status"] == "OK"
        assert res["count"] == 3
        q = quote_repo.get_latest_quote(
            session, "US_INDEX", "usDJI", data_kind="SNAPSHOT", timeframe="snapshot"
        )
        assert q is not None and q.close == 51947.25
        # A股 INDEX 类型不应被污染
        a = quote_repo.get_latest_quote(
            session, "INDEX", "usDJI", data_kind="SNAPSHOT", timeframe="snapshot"
        )
        assert a is None


def test_collect_us_indices_skips_when_fetcher_none(tmp_path):
    s, eng = _setup(tmp_path)
    c = Collector(_FakeProvider(), s, us_index_fetcher=None)
    with session_scope(eng) as session:
        res = c.collect_us_indices(session)
        assert res["status"] == "skipped"
