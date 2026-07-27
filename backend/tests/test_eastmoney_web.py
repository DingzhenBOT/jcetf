"""C19 东方财富 push2 直连源测试：板块资金流(clist) + 板块日K(kline) 解析，及 collector web 方法入库。

不依赖真实网络：mock urllib.request.urlopen 返回样本 JSON。验证：
- fetch_sector_fund_flow_snapshot：clist(行业t:2+概念t:3) -> DataFrame(bk_code/主力净流入-净额/涨跌幅...)
- fetch_sector_kline：push2 kline 主机 secid=90.BKxxxx -> DataFrame(日期/开盘/收盘/最高/最低/成交量/成交额/涨跌幅)
- collector.collect_sector_fund_flow_web / collect_sector_history_web：mock eastmoney_web 模块函数，验证 SECTOR BAR 入库(source='em_web')
"""
from datetime import date

import pandas as pd
import pytest

from app.config import get_settings
from app.collector.collector import Collector
from app.data_provider.base import BaseDataProvider
from app.db import init_db, make_engine, session_scope
from app.repository import mapping_repo


# ---- 样本 JSON ----
_CLIST_JSON = {
    "rc": 0,
    "data": {"total": 2, "diff": {
        "0": {"f12": "BK1036", "f14": "半导体", "f2": 4700.12, "f3": 1.23, "f62": 123456789, "f66": 50000000, "f184": 3.5, "f6": 987654321},
        "1": {"f12": "BK0438", "f14": "消费", "f2": 1200.5, "f3": -0.5, "f62": -50000000, "f66": -20000000, "f184": -2.1, "f6": 123456789},
    }},
}
_KLINE_JSON = {
    "data": {"code": "BK1036", "name": "半导体", "klines": [
        "2026-07-25,4600.0,4650.0,4700.0,4580.0,1000000,5000000000,1.2,1.1,50.0,2.0",
        "2026-07-27,4650.0,4700.0,4720.0,4640.0,1100000,5200000000,1.5,1.08,55.0,2.1",
    ]},
}


class _Resp:
    def __init__(self, obj):
        self._b = __import__("json").dumps(obj).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._b


def _fake_urlopen(url, timeout=12):
    # 真实代码传的是 urllib.request.Request 对象；兼容字符串与 Request。
    url_str = url.get_full_url() if hasattr(url, "get_full_url") else url
    if "clist/get" in url_str:
        return _Resp(_CLIST_JSON)
    if "stock/kline/get" in url_str:
        return _Resp(_KLINE_JSON)
    raise RuntimeError("unexpected url: " + url_str)


def _setup(tmp_path):
    s = get_settings(force_reload=True)
    s.paths.sqlite_path_abs = tmp_path / "etf_monitor.db"
    s.paths.backup_dir_abs = tmp_path / "backups"
    s.paths.log_dir_abs = tmp_path / "logs"
    s.strategy.broad_index_codes = ["000300"]
    # 关注半导体(BK1036)+消费(BK0438)
    s.backfill.major_sector_codes = ["BK1036", "BK0438"]
    eng = make_engine(s)
    init_db(eng, s)
    with session_scope(eng) as session:
        mapping_repo.upsert_mapping(
            session,
            etf_code="510300", etf_name="沪深300ETF", related_sector_codes=["BK1036", "BK0438"],
            related_index_code="000300", category="宽基", mapping_version="v1",
            valid_from=date(2024, 1, 1),
        )
    return s, eng


# ---- 解析单测（mock HTTP）----
def test_fetch_sector_fund_flow_snapshot(monkeypatch):
    import urllib.request

    import app.data_provider.eastmoney_web as em
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    df = em.fetch_sector_fund_flow_snapshot(trade_date=date(2026, 7, 27))
    assert df.attrs.get("__source") == "em_web"
    assert set(df["bk_code"]) >= {"BK1036", "BK0438"}
    row = df[df["bk_code"] == "BK1036"].iloc[0]
    assert row["主力净流入-净额"] == 123456789.0
    assert row["涨跌幅"] == 1.23
    assert row["日期"] == "2026-07-27"


def test_fetch_sector_kline(monkeypatch):
    import urllib.request

    import app.data_provider.eastmoney_web as em
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    df = em.fetch_sector_kline("BK1036", "19900101", "20260727")
    assert df.attrs.get("__source") == "em_web"
    assert len(df) == 2
    assert list(df.columns)[:4] == ["日期", "开盘", "收盘", "最高"]
    assert df.iloc[-1]["收盘"] == 4700.0
    assert df.iloc[-1]["涨跌幅"] == 1.08


# ---- collector web 方法（mock eastmoney_web 模块）----
class _Provider(BaseDataProvider):
    def get_trade_calendar(self): return ["20260727"]
    def get_index_snapshot(self): return pd.DataFrame()
    def get_sector_ranking(self, sector_type): return pd.DataFrame()
    def get_etf_snapshot(self): return pd.DataFrame()
    def get_etf_history(self, *a, **k): return pd.DataFrame()
    def get_index_history(self, *a, **k): return pd.DataFrame()
    def get_sector_history(self, *a, **k): return pd.DataFrame()
    def get_sector_fund_flow_history(self, *a, **k): return pd.DataFrame()
    def get_market_breadth_raw(self): return pd.DataFrame()


def test_collector_sector_fund_flow_web(monkeypatch, tmp_path):
    import urllib.request

    import app.data_provider.eastmoney_web as em
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    s, eng = _setup(tmp_path)
    snap = em.fetch_sector_fund_flow_snapshot(trade_date=date(2026, 7, 27))
    monkeypatch.setattr(em, "fetch_sector_fund_flow_snapshot", lambda trade_date=None, timeout=12: snap)

    c = Collector(_Provider(), s)
    with session_scope(eng) as session:
        res = c.collect_sector_fund_flow_web(session, {"BK1036", "BK0438"}, date(2026, 7, 27))
    assert res["ok"] == 2 and res["failed"] == 0
    with session_scope(eng) as session:
        from app.db.models.market import MarketQuote
        rows = session.query(MarketQuote).filter_by(
            symbol_type="SECTOR", data_source="em_web", data_kind="BAR"
        ).all()
        assert len(rows) == 2
        by_code = {r.symbol: r for r in rows}
        assert by_code["BK1036"].main_net_inflow == 123456789.0
        assert by_code["BK1036"].change_percent == 1.23


def test_collector_sector_history_web(monkeypatch, tmp_path):
    import urllib.request

    import app.data_provider.eastmoney_web as em
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    s, eng = _setup(tmp_path)
    kl = em.fetch_sector_kline("BK1036", "19900101", "20260727")
    # BK0438 返回空 DataFrame -> normalize 抛空 -> failed 路径；BK1036 返回 kl -> ok
    monkeypatch.setattr(em, "fetch_sector_kline",
                        lambda bk, start, end, timeout=12: kl if bk == "BK1036" else pd.DataFrame())

    c = Collector(_Provider(), s)
    with session_scope(eng) as session:
        res = c.collect_sector_history_web(session, {"BK1036", "BK0438"}, date(2026, 7, 27))
    # BK0438 的 kline mock 只覆盖 BK1036 -> 它返回空 -> failed=1；BK1036 ok=1
    assert res["ok"] == 1 and res["failed"] == 1
    with session_scope(eng) as session:
        from app.db.models.market import MarketQuote
        rows = session.query(MarketQuote).filter_by(
            symbol_type="SECTOR", symbol="BK1036", data_source="em_web", data_kind="BAR"
        ).all()
        assert len(rows) == 2
        assert rows[0].close == 4650.0
