"""C22 主源切换测试：collect_intraday_minute 主源改为 qt.gtimg.cn 实时快照(fetch_realtime)批量转 1m。

不依赖真实网络：注入 fake gtimg_fetcher（批量返回实时快照 DataFrame）验证：
- 主源批量快照 -> 每标的 1 根 1m BAR：close=最新价、volume=增量(当前累计-上一根BAR cum)、cum_volume=当前累计；
- 增量口径以「上一根 1m BAR 的 cum_volume」为基准 -> 快照采集(180s)与 1m 采样(60s)频率错配时
  不漏计、不重复（核心回归点：旧实现用快照差值，快照中途更新会漏计）；
- 主源未覆盖的标的(快照返回空) -> 回退次源 web.ifzq/sina；
- cum_volume 列幂等迁移存在；get_latest_1m_bars 取当日最新 1m BAR。
"""
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from app.collector.collector import Collector
from app.data_provider.base import BaseDataProvider
from app.db import init_db, make_engine, session_scope
from app.db.models.market import MarketQuote
from app.repository import mapping_repo, quote_repo


def _realtime_df(rows):
    """构造 fetch_realtime 风格的批量快照 DataFrame（代码/最新价/昨收/成交量/涨跌幅）。"""
    df = pd.DataFrame(rows)
    df.attrs["__source"] = "gtimg"
    return df


class _Provider(BaseDataProvider):
    """主采集全空（不联网），仅实现分时接口供降级测试。"""

    def get_trade_calendar(self):
        return ["20260727"]

    def get_index_snapshot(self):
        return pd.DataFrame()

    def get_sector_ranking(self, sector_type):
        return pd.DataFrame()

    def get_etf_snapshot(self):
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

    def get_intraday_minute(self, symbol_type, code):
        # 降级路径：返回 sina 格式分时
        td = trading_date_for()
        df = pd.DataFrame([
            {"day": pd.Timestamp(td.year, td.month, td.day, 14, 0, 0), "open": 4.7, "high": 4.7, "low": 4.7, "close": 4.7, "volume": 100.0},
            {"day": pd.Timestamp(td.year, td.month, td.day, 14, 1, 0), "open": 4.7, "high": 4.7, "low": 4.7, "close": 4.7, "volume": 100.0},
        ])
        df.attrs["__source"] = "sina"
        return df


def _setup(tmp_path):
    from app.config import get_settings

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


def test_primary_snapshot_to_1m_basic(tmp_path, monkeypatch):
    """主源快照 -> 1m BAR：close=最新价、cum_volume=当前累计、首根 volume=当前累计。"""
    from app.market_calendar import trading_date_for

    s, eng = _setup(tmp_path)
    monkeypatch.setattr(Collector, "_now", lambda self: datetime(2026, 7, 29, 6, 0, 0))  # UTC=14:00 北京

    def fake_realtime(codes_with_kind):
        return _realtime_df([
            {"代码": "510300", "名称": "沪深300ETF", "今开": 4.65, "最高": 4.66, "最低": 4.64,
             "最新价": 4.657, "昨收": 4.627, "成交量": 15092027.0, "成交额": 6.9e9, "涨跌幅": 0.65},
            {"代码": "000300", "名称": "沪深300", "今开": 4590.0, "最高": 4601.0, "最低": 4560.0,
             "最新价": 4600.26, "昨收": 4569.52, "成交量": 248082357.0, "成交额": 7.5e11, "涨跌幅": 0.67},
        ])

    c = Collector(_Provider(), s, gtimg_fetcher=fake_realtime)
    with session_scope(eng) as session:
        res = c.collect_intraday_minute(session)
    assert res["ok"] == 2 and res["failed"] == 0

    with session_scope(eng) as session:
        etf = session.query(MarketQuote).filter_by(
            symbol_type="ETF", symbol="510300", data_source="gtimg", timeframe="1m"
        ).order_by(MarketQuote.timestamp).all()
        idx = session.query(MarketQuote).filter_by(
            symbol_type="INDEX", symbol="000300", data_source="gtimg", timeframe="1m"
        ).order_by(MarketQuote.timestamp).all()
        assert len(etf) == 1 and len(idx) == 1
        # 首根：prev 无 -> volume == 当前累计
        assert etf[0].close == 4.657
        assert etf[0].cum_volume == 15092027.0
        assert etf[0].volume == 15092027.0
        assert etf[0].previous_close == 4.627
        assert abs(etf[0].change_percent - 0.65) < 1e-9
        # 时间戳 = 北京 14:00 -> UTC 06:00
        assert etf[0].timestamp == datetime(2026, 7, 29, 6, 0, 0)


def test_primary_incremental_no_leak(tmp_path, monkeypatch):
    """频率错配不漏计：模拟快照跨三次 1m 采集推进(100->100->250)，增量累计须等于最终累计。

    旧实现用快照差值：T=180s 快照从 S0 跳到 S1 时，若本次采集把 S1 同时当 cur 与 prev，
    则 S0->S1 的增量被算成 0 -> VWAP 偏低、cum_vol 偏少。新实现以「上一根 1m BAR 的 cum_volume」
    为基准，彻底规避：T0 vol=100, T1(快照未更新) vol=0, T2 vol=150 -> 累计 250 无漏计。
    """
    from app.market_calendar import trading_date_for

    s, eng = _setup(tmp_path)
    state = {"n": 0}

    def fake_now(self):
        t = datetime(2026, 7, 29, 6, 0, 0) + timedelta(minutes=state["n"])
        state["n"] += 1
        return t

    monkeypatch.setattr(Collector, "_now", fake_now)

    # 预置「上一根」1m BAR（cum_volume=0，模拟开盘初），供首根以之为基准
    td = trading_date_for()
    with session_scope(eng) as session:
        session.add(MarketQuote(
            data_source="gtimg", symbol_type="ETF", symbol="510300",
            data_kind="BAR", timeframe="1m", trading_date=td,
            timestamp=datetime(2026, 7, 29, 5, 59, 0),  # 上一分钟(北京13:59)
            close=4.65, volume=0.0, cum_volume=0.0, collected_at=datetime.utcnow(),
        ))
        session.commit()

    snap_values = [100.0, 100.0, 250.0]
    calls = {"i": 0}

    def fake_realtime(codes_with_kind):
        v = snap_values[calls["i"]]
        calls["i"] += 1
        return _realtime_df([
            {"代码": "510300", "名称": "E", "今开": 4.65, "最高": 4.66, "最低": 4.64,
             "最新价": 4.66, "昨收": 4.62, "成交量": v, "成交额": 1.0, "涨跌幅": 0.8},
            {"代码": "000300", "名称": "I", "今开": 4590.0, "最高": 4601.0, "最低": 4560.0,
             "最新价": 4600.0, "昨收": 4569.0, "成交量": v * 10, "成交额": 1.0, "涨跌幅": 0.6},
        ])

    c = Collector(_Provider(), s, gtimg_fetcher=fake_realtime)
    for _ in range(3):
        with session_scope(eng) as session:
            c.collect_intraday_minute(session)

    with session_scope(eng) as session:
        etf = session.query(MarketQuote).filter_by(
            symbol_type="ETF", symbol="510300", data_source="gtimg", timeframe="1m"
        ).order_by(MarketQuote.timestamp).all()
        # 预置(5:59,cum0) + 三新根(6:00/6:01/6:02) = 4 根
        assert len(etf) == 4
        vols = [r.volume for r in etf]
        cums = [r.cum_volume for r in etf]
        # 增量之和 == 当前累计（关键：不漏计、不重复）
        assert abs(sum(vols) - 250.0) < 1e-6
        # 末根 cum_volume == 250（无漏计）
        assert cums[-1] == 250.0
        # 快照未更新的那一根 vol=0（被正确处理，不重复计数）
        assert any(v == 0.0 for v in vols[1:]), "快照未更新时应产生 vol=0 根而非重复计数"


def test_primary_covers_etf_secondary_covers_index(tmp_path, monkeypatch):
    """主源只返回 ETF（不含指数），指数回退次源 web.ifzq；两路各自带 cum_volume。"""
    from app.market_calendar import trading_date_for

    s, eng = _setup(tmp_path)
    monkeypatch.setattr(Collector, "_now", lambda self: datetime(2026, 7, 29, 6, 0, 0))

    # 主源只返回 ETF，不含指数 000300
    def fake_realtime(codes_with_kind):
        return _realtime_df([
            {"代码": "510300", "名称": "E", "今开": 4.65, "最高": 4.66, "最低": 4.64,
             "最新价": 4.657, "昨收": 4.627, "成交量": 15092027.0, "成交额": 6.9e9, "涨跌幅": 0.65},
        ])

    # 次源(web.ifzq)返回指数真分钟
    def fake_intraday(code, kind):
        td = trading_date_for()
        df = pd.DataFrame([
            {"day": pd.Timestamp(td.year, td.month, td.day, 14, 0, 0), "open": 4600.0, "high": 4601.0,
             "low": 4599.0, "close": 4600.26, "volume": 1234.0},
        ])
        df.attrs["__source"] = "gtimg"
        return df

    c = Collector(_Provider(), s, gtimg_fetcher=fake_realtime, gtimg_intraday_fetcher=fake_intraday)
    with session_scope(eng) as session:
        res = c.collect_intraday_minute(session)
    assert res["ok"] == 2

    with session_scope(eng) as session:
        etf = session.query(MarketQuote).filter_by(symbol_type="ETF", symbol="510300", timeframe="1m").all()
        idx = session.query(MarketQuote).filter_by(symbol_type="INDEX", symbol="000300", timeframe="1m").all()
        assert len(etf) == 1 and etf[0].cum_volume == 15092027.0  # 主源
        assert len(idx) == 1 and idx[0].cum_volume == 1234.0      # 次源续算累计量


def test_cum_volume_column_migrated(tmp_path):
    """ensure_schema_columns 幂等补列：market_quote 含 cum_volume。"""
    from sqlalchemy import inspect

    s, eng = _setup(tmp_path)
    cols = {c["name"] for c in inspect(eng).get_columns("market_quote")}
    assert "cum_volume" in cols


def test_get_latest_1m_bars(tmp_path):
    """get_latest_1m_bars 返回每个标的当日最新 1m BAR（含 cum_volume）。"""
    from app.market_calendar import trading_date_for

    s, eng = _setup(tmp_path)
    td = trading_date_for()
    with session_scope(eng) as session:
        for minute, cum in [(30, 100.0), (31, 250.0)]:
            session.add(MarketQuote(
                data_source="gtimg", symbol_type="ETF", symbol="510300",
                data_kind="BAR", timeframe="1m", trading_date=td,
                timestamp=datetime(2026, 7, 29, 1, minute, 0),
                close=4.6, volume=cum, cum_volume=cum, collected_at=datetime.utcnow(),
            ))
        session.commit()
    with session_scope(eng) as session:
        out = quote_repo.get_latest_1m_bars(session, [("ETF", "510300")], td)
        assert out[("ETF", "510300")].cum_volume == 250.0


def test_intraday_read_prefers_gtimg_over_sina(tmp_path):
    """读路径去重：同日 sina 与 gtimg 1m 分时共存时，须优先返回 gtimg（C22 主源正确数据），
    丢弃旧 sina 脏数据（错码/陈旧），否则分时图仍显示错数据。"""
    from app.market_calendar import trading_date_for

    s, eng = _setup(tmp_path)
    td = trading_date_for()
    with session_scope(eng) as session:
        # 旧 sina 脏分时（错码/陈旧，归零）
        session.add(MarketQuote(
            data_source="sina", symbol_type="ETF", symbol="510300",
            data_kind="BAR", timeframe="1m", trading_date=td,
            timestamp=datetime(2026, 7, 29, 1, 30, 0),
            close=0.0, volume=0.0, cum_volume=0.0, collected_at=datetime.utcnow(),
        ))
        # 正确 gtimg 分时
        session.add(MarketQuote(
            data_source="gtimg", symbol_type="ETF", symbol="510300",
            data_kind="BAR", timeframe="1m", trading_date=td,
            timestamp=datetime(2026, 7, 29, 1, 30, 0),
            close=4.657, volume=15092027.0, cum_volume=15092027.0, collected_at=datetime.utcnow(),
        ))
        session.commit()
    with session_scope(eng) as session:
        rows = quote_repo.get_bar_history(session, "ETF", "510300", td, td, timeframe="1m", data_kind="BAR")
        assert len(rows) == 1
        assert rows[0].data_source == "gtimg"
        assert rows[0].close == 4.657
