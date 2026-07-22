"""指数快照补齐测试：em 批次缺失深市指数（399001）时，从 sina 兜底补齐 SNAPSHOT。

生产根因：东方财富 stock_zh_index_spot_em 只返回沪市类指数（000300/000001），
深证成指 399001 不在其批次里 -> 永远采不到 em 快照，overview 回退陈旧 sina BAR。
本测试验证 collector 能自动用 sina 补齐 399001，且新浪代码前缀 sz 正确归一到 399001。
"""
from pathlib import Path

import pandas as pd
import pytest

from app.collector.collector import Collector
from app.config import get_settings
from app.data_provider.base import BaseDataProvider
from app.db import init_db, make_engine, session_scope
from app.db.models.market import MarketQuote
from app.repository import quote_repo


class GapFillProvider(BaseDataProvider):
    """主批次只含 000300/000001（模拟 em）；sina 含 399001（带 sz 前缀 + 涨跌幅）。"""

    def get_trade_calendar(self):
        return ["20240102"]

    def _df(self, rows, source):
        df = pd.DataFrame(rows)
        df.attrs["__source"] = source
        return df

    def get_index_snapshot(self):
        # 主源（em）批次：只有沪市指数，不含 399001
        return self._df([
            {"代码": "000300", "名称": "沪深300", "最新价": 4717.24, "涨跌幅": -0.46, "昨收": 4739.23,
             "今开": 4710.66, "最高": 4772.08, "最低": 4692.83, "成交量": 1, "成交额": 1},
            {"代码": "000001", "名称": "上证指数", "最新价": 3867.03, "涨跌幅": 0.07, "昨收": 3864.37,
             "今开": 3839.66, "最高": 3884.43, "最低": 3839.66, "成交量": 1, "成交额": 1},
        ], "em")

    def index_spot_sources(self):
        return ["em", "sina"]

    def get_index_snapshot_from(self, src: str):
        if src == "em":
            return self.get_index_snapshot()
        # sina 含 399001，代码带 sz 前缀 + 涨跌幅列
        return self._df([
            {"代码": "sh000300", "名称": "沪深300", "最新价": 4717.24, "涨跌幅": -0.46, "昨收": 4739.23,
             "今开": 4710.66, "最高": 4772.08, "最低": 4692.83, "成交量": 1, "成交额": 1},
            {"代码": "sz399001", "名称": "深证成指", "最新价": 14061.44, "涨跌幅": -1.422, "昨收": 14264.29,
             "今开": 14169.97, "最高": 14360.37, "最低": 13988.51, "成交量": 1, "成交额": 1},
        ], "sina")

    def get_etf_snapshot(self):
        return self._df([], "em")

    def get_sector_ranking(self, sector_type):
        return self._df([], "em")

    def get_etf_history(self, *a, **k):
        return pd.DataFrame()

    def get_index_history(self, *a, **k):
        return pd.DataFrame()

    def get_sector_history(self, *a, **k):
        return pd.DataFrame()

    def get_sector_fund_flow_history(self, *a, **k):
        return pd.DataFrame()

    def get_market_breadth_raw(self):
        return self._df([], "em")


def _setup(tmp_path):
    s = get_settings(force_reload=True)
    s.paths.sqlite_path_abs = tmp_path / "etf_monitor.db"
    s.paths.backup_dir_abs = tmp_path / "backups"
    s.paths.log_dir_abs = tmp_path / "logs"
    eng = make_engine(s)
    init_db(eng, s)
    return s, eng


def test_index_snapshot_gapfills_missing_sz_index(tmp_path):
    s, eng = _setup(tmp_path)
    c = Collector(GapFillProvider(), s)
    with session_scope(eng) as session:
        res = c.collect_index_snapshot(session)

    assert res["status"] == "OK"
    with session_scope(eng) as session:
        q = quote_repo.get_latest_quote(session, "INDEX", "399001", "SNAPSHOT", "snapshot")
        assert q is not None, "399001 应从 sina 补齐 SNAPSHOT"
        assert q.data_source == "sina"
        # 新浪带 涨跌幅 列，直接用源值（不被反算覆盖）
        assert abs(q.change_percent - (-1.422)) < 1e-3
        assert abs(q.close - 14061.44) < 1e-3
        # em 主源指数仍正常
        q300 = quote_repo.get_latest_quote(session, "INDEX", "000300", "SNAPSHOT", "snapshot")
        assert q300 is not None and q300.data_source == "em"


def test_index_snapshot_gapfill_skips_when_em_has_code(tmp_path):
    """em 已含某指数时，不应再用 sina 覆盖（补齐只在缺失时触发）。"""
    s, eng = _setup(tmp_path)
    # 仅验证 000300/000001 不触发补齐：用只返回此两者的 provider，index_spot_sources 仅 em
    class EmOnlyProvider(GapFillProvider):
        def index_spot_sources(self):
            return ["em"]

        def get_index_snapshot_from(self, src):
            raise AssertionError("不应调用兜底源")

    c = Collector(EmOnlyProvider(), s)
    with session_scope(eng) as session:
        c.collect_index_snapshot(session)
    with session_scope(eng) as session:
        q = quote_repo.get_latest_quote(session, "INDEX", "000300", "SNAPSHOT", "snapshot")
        assert q is not None and q.data_source == "em"
