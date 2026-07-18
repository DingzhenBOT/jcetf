"""P2 collector 测试：编排 / 幂等 / 切源标记 / 数据源状态 / 失败路径。

用 FakeProvider 注入确定性数据，不依赖网络；验证：
- 采集入库行数正确；
- 同时间戳重跑幂等（ON CONFLICT 不重复）；
- 切源标记 source_switched=1；
- 数据源状态 OK/FAILED + 连续失败计数；
- 单能力失败不影响其他能力，且记 FAILED。
"""
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from app.config import get_settings
from app.collector.collector import Collector
from app.data_provider.base import BaseDataProvider
from app.db import init_db, make_engine, session_scope
from app.db.models.market import MarketBreadth, MarketQuote
from app.db.models.system import DataSourceStatus


class FakeProvider(BaseDataProvider):
    def __init__(self, source="sina", fail_index=False):
        self.source = source
        self.fail_index = fail_index

    def get_trade_calendar(self):
        return ["20240102", "20240103"]

    def _df(self, rows):
        df = pd.DataFrame(rows)
        df.attrs["__source"] = self.source
        return df

    def get_index_snapshot(self):
        if self.fail_index:
            raise RuntimeError("simulated index outage")
        return self._df([{
            "代码": "000001", "最新价": 3200, "涨跌幅": 0.3, "昨收": 3190,
            "今开": 3195, "最高": 3210, "最低": 3188, "成交量": 1, "成交额": 1e9,
        }])

    def get_etf_snapshot(self):
        return self._df([{
            "代码": "510300", "最新价": 3.8, "涨跌幅": 0.5, "昨收": 3.78,
            "今开": 3.79, "最高": 3.81, "最低": 3.77, "成交量": 2, "成交额": 1e8,
        }])

    def get_sector_ranking(self, sector_type):
        return self._df([{
            "板块代码": "BK0001", "最新价": 1000, "涨跌幅": 1.2,
            "上涨家数": 30, "下跌家数": 10,
        }])

    def get_etf_history(self, *a, **k):
        return pd.DataFrame()

    def get_index_history(self, *a, **k):
        return pd.DataFrame()

    def get_sector_history(self, *a, **k):
        return pd.DataFrame()

    def get_sector_fund_flow_history(self, *a, **k):
        return pd.DataFrame()

    def get_market_breadth_raw(self):
        return self._df([
            {"代码": "1", "涨跌幅": 2.0, "成交额": 1e6},
            {"代码": "2", "涨跌幅": -3.0, "成交额": 2e6},
            {"代码": "3", "涨跌幅": 0.0, "成交额": 5e5},
        ])


def _setup(tmp_path: Path):
    s = get_settings(force_reload=True)
    s.paths.sqlite_path_abs = tmp_path / "etf_monitor.db"
    s.paths.backup_dir_abs = tmp_path / "backups"
    s.paths.log_dir_abs = tmp_path / "logs"
    eng = make_engine(s)
    init_db(eng, s)
    return s, eng


def _count(session, symbol_type):
    return session.query(MarketQuote).filter(MarketQuote.symbol_type == symbol_type).count()


def test_collect_market_inserts_expected_rows(tmp_path):
    s, eng = _setup(tmp_path)
    c = Collector(FakeProvider("sina"), s)
    with session_scope(eng) as session:
        res = c.collect_market(session)
    assert res["index"]["status"] == "OK" and res["index"]["count"] == 1
    assert res["etf"]["count"] == 1
    assert res["industry"]["count"] == 1
    assert res["concept"]["count"] == 1
    with session_scope(eng) as session:
        assert _count(session, "INDEX") == 1
        assert _count(session, "ETF") == 1
        assert _count(session, "INDUSTRY") == 1
        assert _count(session, "CONCEPT") == 1
        # 数据源状态 OK
        st = session.query(DataSourceStatus).filter_by(data_source="sina", symbol_type="INDEX").one()
        assert st.status == "OK" and st.consecutive_failures == 0


def test_collect_market_idempotent_same_timestamp(tmp_path):
    s, eng = _setup(tmp_path)
    c = Collector(FakeProvider("sina"), s)
    fixed = datetime(2024, 1, 2, 2, 0, 0)  # 北京 10:00
    c._now = lambda: fixed  # 同时间戳重跑 -> 应幂等
    with session_scope(eng) as session:
        c.collect_market(session)
    with session_scope(eng) as session:
        c.collect_market(session)
    with session_scope(eng) as session:
        assert _count(session, "INDEX") == 1  # 未重复


def test_source_switched_marked_on_switch(tmp_path):
    s, eng = _setup(tmp_path)
    t1 = datetime(2024, 1, 2, 2, 0, 0)
    t2 = datetime(2024, 1, 2, 2, 3, 0)  # 不同时间戳，新快照
    c1 = Collector(FakeProvider("sina"), s)
    c1._now = lambda: t1
    c2 = Collector(FakeProvider("ths"), s)
    c2._now = lambda: t2
    with session_scope(eng) as session:
        c1.collect_market(session)
    with session_scope(eng) as session:
        c2.collect_market(session)
    with session_scope(eng) as session:
        # 第二次（ths）的 INDEX 行应标 source_switched=1
        row = session.query(MarketQuote).filter_by(symbol_type="INDEX", data_source="ths").one()
        assert row.source_switched == 1
        st = session.query(DataSourceStatus).filter_by(data_source="ths", symbol_type="INDEX").one()
        assert st.status == "OK"


def test_failure_path_records_failed_and_skips_row(tmp_path):
    s, eng = _setup(tmp_path)
    c = Collector(FakeProvider("sina", fail_index=True), s)
    with session_scope(eng) as session:
        res = c.collect_market(session)
    assert res["index"]["status"] == "FAILED"
    assert res["etf"]["status"] == "OK"  # 其他能力不受影响
    with session_scope(eng) as session:
        assert _count(session, "INDEX") == 0  # 失败未入库
        assert _count(session, "ETF") == 1
        st = session.query(DataSourceStatus).filter_by(symbol_type="INDEX").one()
        assert st.status == "FAILED" and st.consecutive_failures == 1


def test_breadth_upsert_idempotent_per_day(tmp_path):
    s, eng = _setup(tmp_path)
    c = Collector(FakeProvider("sina"), s)
    with session_scope(eng) as session:
        c.collect_breadth(session)
    with session_scope(eng) as session:
        c.collect_breadth(session)  # 同日重跑
    with session_scope(eng) as session:
        rows = session.query(MarketBreadth).all()
        assert len(rows) == 1
        assert rows[0].total_rise == 1 and rows[0].total_fall == 1 and rows[0].total_flat == 1
