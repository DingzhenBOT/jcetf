"""Task A（gtimg 实时快照源）测试：腾讯财经 qt.gtimg.cn 作为 CVM 不封 IP 的可靠盘中源。

不依赖真实网络：用 fake gtimg_fetcher 注入确定性 DataFrame；验证：
- 注入后 collect_market 末尾写入 gtimg 来源的 ETF+指数 SNAPSHOT；
- gtimg 未注入 -> collect_market 跳过 gtimg（零网络），其余四类采集不受影响；
- gtimg 拉取失败 -> 记 FAILED 状态 + 返回，不影响其余采集（优雅降级）；
- ETF/指数代码正确分流到对应 symbol_type，change_percent 正确解析；
- get_latest_snapshot_change_map 跨源取 max(timestamp) -> 命中 gtimg 最新值（P1 生效前提）。
"""
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from app.config import get_settings
from app.collector.collector import Collector
from app.data_provider.base import BaseDataProvider
from app.db import init_db, make_engine, session_scope
from app.db.models.market import MarketQuote
from app.repository import mapping_repo
from app.repository.quote_repo import get_latest_snapshot_change_map


class _SilentProvider(BaseDataProvider):
    """仅用于 gtimg 测试：主采集返回空（不联网），重点验证 gtimg 附加源。"""

    def get_trade_calendar(self):
        return ["20240102", "20240103"]

    def get_index_snapshot(self):
        return pd.DataFrame()

    def get_etf_snapshot(self):
        return pd.DataFrame()

    def get_sector_ranking(self, sector_type):
        return pd.DataFrame()

    def get_etf_history(self, *a, **k):
        return pd.DataFrame()

    def get_index_history(self, *a, **k):
        return pd.DataFrame()

    def get_sector_history(self, *a, **k):
        return pd.DataFrame()

    def get_sector_fund_flow_history(self, *a, **k):
        return pd.DataFrame()

    def get_market_breadth_raw(self):
        return pd.DataFrame()


# gtimg 返回的实时 DataFrame（列名对齐 gtimg_client.fetch_realtime 输出）
def _gtimg_df() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "代码": "510300", "名称": "沪深300ETF", "今开": 3.79, "最高": 3.81,
            "最低": 3.77, "最新价": 3.85, "昨收": 3.78, "成交量": 2, "成交额": 1e8, "涨跌幅": 1.85,
        },
        {
            "代码": "000300", "名称": "沪深300", "今开": 3195, "最高": 3210,
            "最低": 3188, "最新价": 3210, "昨收": 3190, "成交量": 1, "成交额": 1e9, "涨跌幅": 0.63,
        },
    ])


def _setup(tmp_path: Path):
    s = get_settings(force_reload=True)
    s.paths.sqlite_path_abs = tmp_path / "etf_monitor.db"
    s.paths.backup_dir_abs = tmp_path / "backups"
    s.paths.log_dir_abs = tmp_path / "logs"
    s.strategy.broad_index_codes = ["000300"]
    eng = make_engine(s)
    init_db(eng, s)
    with session_scope(eng) as session:
        mapping_repo.upsert_mapping(
            session,
            etf_code="510300", etf_name="沪深300ETF", related_sector_codes=[],
            related_index_code="000300", category="宽基", mapping_version="v1",
            valid_from=date(2024, 1, 1),
        )
    return s, eng


def test_gtimg_injected_writes_snapshot(tmp_path):
    s, eng = _setup(tmp_path)
    captured = {}

    def fake_fetcher(codes_with_kind):
        captured["codes"] = [c for c, _ in codes_with_kind]
        return _gtimg_df()

    c = Collector(_SilentProvider(), s, gtimg_fetcher=fake_fetcher)
    with session_scope(eng) as session:
        res = c.collect_market(session)

    assert res["gtimg"]["status"] == "OK"
    assert res["gtimg"]["count"] == 2
    # 拉取的代码包含 ETF(510300)+指数(000300)
    assert "510300" in captured["codes"]
    assert "000300" in captured["codes"]

    with session_scope(eng) as session:
        etf = session.query(MarketQuote).filter_by(
            symbol_type="ETF", symbol="510300", data_source="gtimg"
        ).one()
        idx = session.query(MarketQuote).filter_by(
            symbol_type="INDEX", symbol="000300", data_source="gtimg"
        ).one()
        assert etf.change_percent == 1.85
        assert idx.change_percent == 0.63
        assert etf.close == 3.85


def test_gtimg_is_latest_for_p1(tmp_path):
    """get_latest_snapshot_change_map 应跨源取 max(timestamp)，命中 gtimg（P1 生效前提）。"""
    s, eng = _setup(tmp_path)
    c = Collector(_SilentProvider(), s, gtimg_fetcher=lambda ck: _gtimg_df())
    with session_scope(eng) as session:
        c.collect_market(session)
    with session_scope(eng) as session:
        m = get_latest_snapshot_change_map(session, "ETF", ["510300"])
        assert m["510300"] == 1.85  # gtimg 最新值生效


def test_gtimg_not_injected_skips(tmp_path):
    s, eng = _setup(tmp_path)
    c = Collector(_SilentProvider(), s)  # gtimg_fetcher 默认 None
    with session_scope(eng) as session:
        res = c.collect_market(session)
    assert res["gtimg"]["status"] == "skipped"
    with session_scope(eng) as session:
        cnt = session.query(MarketQuote).filter_by(data_source="gtimg").count()
        assert cnt == 0  # 未注入 -> 零网络、零写入


def test_gtimg_failure_degrades(tmp_path):
    s, eng = _setup(tmp_path)

    def boom(codes_with_kind):
        raise RuntimeError("simulated gtimg outage")

    c = Collector(_SilentProvider(), s, gtimg_fetcher=boom)
    with session_scope(eng) as session:
        res = c.collect_market(session)  # 不应抛异常（优雅降级）
    assert res["gtimg"]["status"] == "FAILED"
    # 其余能力仍正常返回（主采集空 -> FAILED，但不因 gtimg 而崩溃）
    for k in ("index", "etf", "industry", "concept"):
        assert k in res
    with session_scope(eng) as session:
        # gtimg 失败 -> 无任何 gtimg 行入库
        cnt = session.query(MarketQuote).filter_by(data_source="gtimg").count()
        assert cnt == 0
