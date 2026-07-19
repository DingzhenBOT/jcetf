"""collector 历史采集 + 回填单测（P3）。

- collect_etf_history / collect_index_history：归一化 -> 幂等入库。
- em-only 板块历史失败（模拟 em 不可达）被捕获、记 FAILED、不抛出、继续回填其他标的（D4）。
- backfill_history 增量：第二次运行起点 = max(timestamp)+1，行数不重复。
"""
from datetime import date

import pandas as pd

from app.collector.collector import Collector
from app.config import get_settings
from app.data_provider.base import BaseDataProvider
from app.db import init_db, make_engine, session_scope
from app.repository import mapping_repo, quote_repo
from app.strategy_versioning import mint_strategy_version
from app.strategy_engine.rules import RULES_V1


class FakeProvider(BaseDataProvider):
    """历史方法返回合成 BAR；板块历史模拟 em 不可达（抛异常）。"""

    def get_trade_calendar(self):
        return ["20240102", "20240103"]

    def get_index_snapshot(self):
        return pd.DataFrame()

    def get_etf_snapshot(self):
        return pd.DataFrame()

    def get_sector_ranking(self, sector_type):
        return pd.DataFrame()

    def get_market_breadth_raw(self):
        return pd.DataFrame()

    def _etf_df(self):
        df = pd.DataFrame([
            {"日期": "2024-01-02", "开盘": 3.7, "收盘": 3.8, "最高": 3.81, "最低": 3.77,
             "成交量": 2, "成交额": 1e8, "涨跌幅": 0.5, "换手率": 1.2},
            {"日期": "2024-01-03", "开盘": 3.79, "收盘": 3.9, "最高": 3.91, "最低": 3.78,
             "成交量": 2, "成交额": 1e8, "涨跌幅": 2.6, "换手率": 1.3},
            {"日期": "2024-01-04", "开盘": 3.89, "收盘": 4.0, "最高": 4.01, "最低": 3.88,
             "成交量": 2, "成交额": 1e8, "涨跌幅": 2.5, "换手率": 1.1},
        ])
        df.attrs["__source"] = "em"
        return df

    def _index_df(self):
        df = pd.DataFrame([
            {"date": "2024-01-02", "open": 3190, "high": 3210, "low": 3188, "close": 3200, "volume": 1},
            {"date": "2024-01-03", "open": 3200, "high": 3220, "low": 3198, "close": 3215, "volume": 1},
            {"date": "2024-01-04", "open": 3215, "high": 3230, "low": 3210, "close": 3225, "volume": 1},
        ])
        df.attrs["__source"] = "em"
        return df

    def get_etf_history(self, symbol, start, end):
        return self._etf_df()

    def get_index_history(self, symbol, start, end):
        return self._index_df()

    def get_sector_history(self, symbol, start, end):
        # 模拟 em-only 板块历史不可达（沙箱/用户服务器均如此）
        raise RuntimeError("em sector history unreachable (simulated)")

    def get_sector_fund_flow_history(self, symbol, start, end):
        raise RuntimeError("em sector fund flow unreachable (simulated)")


def _setup(tmp_path):
    s = get_settings(force_reload=True)
    s.paths.sqlite_path_abs = tmp_path / "etf_monitor.db"
    s.paths.backup_dir_abs = tmp_path / "backups"
    s.paths.log_dir_abs = tmp_path / "logs"
    eng = make_engine(s)
    init_db(eng, s)
    return s, eng


def test_collect_etf_history_upserts_bars(tmp_path):
    s, eng = _setup(tmp_path)
    c = Collector(FakeProvider(), s)
    with session_scope(eng) as session:
        res = c.collect_etf_history(session, "510300", "20240101", "20240105")
        assert res["status"] == "OK" and res["count"] == 3
        bars = quote_repo.get_bar_history(session, "ETF", "510300", date(2024, 1, 1), date(2024, 1, 31))
        assert len(bars) == 3
        assert bars[0].data_kind == "BAR" and bars[0].timeframe == "1d"


def test_collect_index_history_upserts_bars(tmp_path):
    s, eng = _setup(tmp_path)
    c = Collector(FakeProvider(), s)
    with session_scope(eng) as session:
        res = c.collect_index_history(session, "000300", "20240101", "20240105")
        assert res["status"] == "OK" and res["count"] == 3


def test_sector_history_failure_is_graceful(tmp_path):
    s, eng = _setup(tmp_path)
    c = Collector(FakeProvider(), s)
    with session_scope(eng) as session:
        res = c.collect_sector_history(session, "BK0465", "20240101", "20240105")
        assert res["status"] == "FAILED"
        # 不抛，且无 BAR 入库
        bars = quote_repo.get_bar_history(session, "SECTOR", "BK0465", date(2024, 1, 1), date(2024, 1, 31))
        assert bars == []


def test_backfill_history_incremental_and_resilient(tmp_path):
    s, eng = _setup(tmp_path)
    with session_scope(eng) as session:
        mapping_repo.upsert_mapping(
            session, etf_code="510300", etf_name="沪深300ETF",
            related_sector_codes=["BK0465"], related_index_code="000300",
            category="宽基", mapping_version="v1",
            valid_from=date(2000, 1, 1), valid_to=None, notes="t",
        )
        # 先把 P3 规则版本注入，避免每次运行重新 mint 干扰计数
        mint_strategy_version(session, s, RULES_V1)

    c = Collector(FakeProvider(), s)
    with session_scope(eng) as session:
        r1 = c.backfill_history(session, as_of=date(2024, 1, 10))
        # ETF 成功；板块历史/资金流失败（em 不可达）属预期
        assert r1["etf"]["ok"] == 1
        assert r1["sector"]["failed"] >= 1
        assert r1["sector_flow"]["failed"] >= 1

    with session_scope(eng) as session:
        n_after_first = len(quote_repo.get_bar_history(session, "ETF", "510300",
                                                        date(2024, 1, 1), date(2024, 1, 31)))
        mx = quote_repo.get_max_bar_timestamp(session, "ETF", "510300")
        assert n_after_first == 3
        assert mx.date() == date(2024, 1, 4)

        # 第二次回填：起点应推进到 max+1；FakeProvider 返回同样 3 行（均 <= max），upsert 幂等
        r2 = c.backfill_history(session, as_of=date(2024, 1, 10))
        assert r2["etf"]["ok"] == 1
        n_after_second = len(quote_repo.get_bar_history(session, "ETF", "510300",
                                                         date(2024, 1, 1), date(2024, 1, 31)))
        assert n_after_second == 3  # 不重复
        assert quote_repo.get_max_bar_timestamp(session, "ETF", "510300").date() == date(2024, 1, 4)
