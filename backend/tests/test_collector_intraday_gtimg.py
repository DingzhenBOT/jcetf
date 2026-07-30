"""C19 腾讯分时源测试：collect_intraday_minute 优先腾讯 gtimg、降级 sina。

不依赖真实网络：用 fake gtimg_intraday_fetcher / fake provider 注入确定性 DataFrame；验证：
- 注入 gtimg 分时 -> 写入 source='gtimg' 的 INTRADAY_MINUTE(BAR/1m) 行，覆盖 ETF+指数；
- gtimg 失败 -> 优雅降级到 sina（provider.get_intraday_minute），写入 source='sina'；
- 双源均失败 -> 记 FAILED 状态 + 不抛异常（bucket.failed 累加，ok 不变）。

背景：sina 在 CVM 返回两周前旧分时数据（C19 实测 7/15），腾讯 web.ifzq.gtimg.cn 返回当日，故切腾讯优先。
"""
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from app.config import get_settings
from app.collector.collector import Collector
from app.data_provider.base import BaseDataProvider
from app.db import init_db, make_engine, session_scope
from app.market_calendar import trading_date_for
from app.repository import mapping_repo


def _intraday_df(source: str) -> pd.DataFrame:
    """构造与 normalize.normalize_intraday_minute 期望同列名的分时 DataFrame。

    日期取「今天」（trading_date_for），与 collect_intraday_minute 的 tdate 一致——
    盘中分时本就是当日的，normalize 仅保留「当天」分钟（过滤 sina 等多日旧数据）。
    """
    td = trading_date_for()
    return pd.DataFrame([
        {"day": pd.Timestamp(td.year, td.month, td.day, 9, 30, 0), "open": 4.70, "high": 4.70, "low": 4.70, "close": 4.70, "volume": 100.0},
        {"day": pd.Timestamp(td.year, td.month, td.day, 9, 31, 0), "open": 4.71, "high": 4.71, "low": 4.71, "close": 4.71, "volume": 200.0},
    ])


def _tagged_df(source: str) -> pd.DataFrame:
    df = _intraday_df(source)
    df.attrs["__source"] = source
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
        return _tagged_df("sina")


def _setup(tmp_path):
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


def test_intraday_prefers_gtimg(tmp_path):
    s, eng = _setup(tmp_path)
    captured = {}

    def fake_gtimg(code, kind):
        captured[(kind, code)] = True
        return _tagged_df("gtimg")

    c = Collector(_Provider(), s, gtimg_intraday_fetcher=fake_gtimg)
    with session_scope(eng) as session:
        res = c.collect_intraday_minute(session)

    assert res["ok"] == 2 and res["failed"] == 0
    # gtimg 被优先调用（ETF + 指数）
    assert captured.get(("ETF", "510300"))
    assert captured.get(("INDEX", "000300"))

    with session_scope(eng) as session:
        from app.db.models.market import MarketQuote
        etf = session.query(MarketQuote).filter_by(
            symbol_type="ETF", symbol="510300", data_source="gtimg", timeframe="1m"
        ).all()
        idx = session.query(MarketQuote).filter_by(
            symbol_type="INDEX", symbol="000300", data_source="gtimg", timeframe="1m"
        ).all()
        assert len(etf) == 2 and len(idx) == 2
        assert etf[0].close == 4.70


def test_intraday_falls_back_to_sina(tmp_path):
    s, eng = _setup(tmp_path)

    def boom(code, kind):
        raise RuntimeError("simulated gtimg intraday outage")

    c = Collector(_Provider(), s, gtimg_intraday_fetcher=boom)
    with session_scope(eng) as session:
        res = c.collect_intraday_minute(session)

    # gtimg 失败 -> 降级 sina，仍成功采集
    assert res["ok"] == 2 and res["failed"] == 0

    with session_scope(eng) as session:
        from app.db.models.market import MarketQuote
        etf = session.query(MarketQuote).filter_by(
            symbol_type="ETF", symbol="510300", data_source="sina", timeframe="1m"
        ).all()
        assert len(etf) == 2  # 降级到 sina 生效


def test_intraday_both_fail_records_failed(tmp_path):
    s, eng = _setup(tmp_path)

    class _NoSina(_Provider):
        def get_intraday_minute(self, symbol_type, code):
            raise RuntimeError("sina also down")

    def boom(code, kind):
        raise RuntimeError("simulated gtimg intraday outage")

    c = Collector(_NoSina(), s, gtimg_intraday_fetcher=boom)
    with session_scope(eng) as session:
        res = c.collect_intraday_minute(session)  # 不应抛异常

    assert res["ok"] == 0 and res["failed"] == 2


def test_gtimg_client_parse(monkeypatch):
    """直接验证 gtimg_client.fetch_intraday_minute 的 JSON 解析（mock HTTP，不联网）。"""
    import io
    import json
    import urllib.request

    payload = {
        "data": {
            "sh510300": {
                "data": {
                    "date": "2026-07-27",
                    "data": [
                        "0930 4.702 50503 23746511.00",
                        "0931 4.709 106479 50094192.00",
                        "0932 4.710 150000 71000000.00",
                    ],
                }
            }
        }
    }

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(payload).encode("utf-8")

    def _fake_urlopen(req, timeout=10):
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    from app.data_provider import gtimg_client
    df = gtimg_client.fetch_intraday_minute("510300", "ETF")
    assert df.attrs.get("__source") == "gtimg"
    assert list(df.columns) == ["day", "open", "high", "low", "close", "volume"]
    assert len(df) == 3
    # volume 取增量：50503, 106479-50503=55976, 150000-106479=43521
    assert df["volume"].tolist() == [50503.0, 55976.0, 43521.0]
    assert df["close"].tolist() == [4.702, 4.709, 4.710]
    # day 为北京时 naive datetime，且均为当日
    assert all(r.day == 27 for r in df["day"])
    assert df["open"].equals(df["high"]) and df["high"].equals(df["low"]) and df["low"].equals(df["close"])



def test_intraday_no_purge_when_all_fetch_fail(tmp_path):
    """先采后清回归（#3a）：双源全失败时 collect_intraday_minute 不应清掉既有分时数据。

    旧实现「先 purge 再 fetch」——一旦采集对指数失败即清了补不回，导致沪深300 分时消失。
    新实现仅在本次确有新数据写入（bucket.ok>0）后才清旧日；全失败则不 purge，保留既有数据。
    """
    from datetime import datetime, timedelta

    from app.db.models.market import MarketQuote

    s, eng = _setup(tmp_path)

    # 预置「更早交易日」既有分时（trading_date < 今日，若 purge 会被删）
    old = trading_date_for() - timedelta(days=5)
    with session_scope(eng) as session:
        session.add(MarketQuote(
            data_source="gtimg", symbol_type="ETF", symbol="510300",
            data_kind="BAR", timeframe="1m", trading_date=old,
            timestamp=datetime(old.year, old.month, old.day, 9, 30),
            close=4.6, collected_at=datetime.utcnow(),
        ))
        session.add(MarketQuote(
            data_source="gtimg", symbol_type="INDEX", symbol="000300",
            data_kind="BAR", timeframe="1m", trading_date=old,
            timestamp=datetime(old.year, old.month, old.day, 9, 30),
            close=4000.0, collected_at=datetime.utcnow(),
        ))
        session.commit()
        assert session.query(MarketQuote).count() == 2

    class _NoSina(_Provider):
        def get_intraday_minute(self, symbol_type, code):
            raise RuntimeError("sina also down")

    def boom(code, kind):
        raise RuntimeError("simulated gtimg intraday outage")

    c = Collector(_NoSina(), s, gtimg_intraday_fetcher=boom)
    with session_scope(eng) as session:
        res = c.collect_intraday_minute(session)
        assert res["ok"] == 0 and res["failed"] == 2
        # 关键：既有分时数据必须保留（未被 purge 清掉）
        after = session.query(MarketQuote).count()
        assert after == 2, "双源失败时空数据不应被清除（先采后清）"


def test_intraday_rows_fresh_logic():
    """_intraday_rows_fresh：拒绝未来异常（>5min）的分钟数据，接受当日（含早前）与临近 now 的。"""
    from app.collector.collector import _intraday_rows_fresh

    NOW = datetime(2026, 7, 29, 6, 0, 0)
    assert _intraday_rows_fresh([{"timestamp": NOW}, {"timestamp": NOW - timedelta(minutes=5)}], NOW) is True
    assert _intraday_rows_fresh([{"timestamp": NOW - timedelta(minutes=60)}], NOW) is True  # 同日早前分钟，正常
    assert _intraday_rows_fresh([{"timestamp": NOW + timedelta(minutes=60)}], NOW) is False  # 未来异常（陈旧错标）
    assert _intraday_rows_fresh([], NOW) is False
    assert _intraday_rows_fresh([{"timestamp": None}], NOW) is False


def test_intraday_rows_fresh_rejects_stale_lag_during_trading():
    """盘中滞后关卡：NOW=北京14:00(UTC06:00)。
    - 最新分钟落后>120min（如北京11:30，sina 冻结在上午）-> 拒绝；
    - 落后<120min（如北京13:30，午后刚开始）-> 接受（不误杀同日早前分钟）；
    - 非盘中时段不触发滞后关卡。
    """
    from app.collector.collector import _intraday_rows_fresh

    NOW = datetime(2026, 7, 29, 6, 0, 0)  # UTC = 14:00 北京（盘中时段）
    # 落后 150min（北京11:30）-> sina 冻结在上午，视为陈旧拒绝
    assert _intraday_rows_fresh([{"timestamp": NOW - timedelta(minutes=150)}], NOW) is False
    # 落后 30min（北京13:30）-> 午后刚开始，正常更新，接受
    assert _intraday_rows_fresh([{"timestamp": NOW - timedelta(minutes=30)}], NOW) is True
    # 落后 1min（北京13:59）-> 正常更新，接受
    assert _intraday_rows_fresh([{"timestamp": NOW - timedelta(minutes=1)}], NOW) is True
    # 非盘中时段（北京 20:00 = UTC 12:00）不触发滞后关卡：落后 150min 仍接受
    OFF = datetime(2026, 7, 29, 12, 0, 0)  # UTC = 20:00 北京（盘后）
    assert _intraday_rows_fresh([{"timestamp": OFF - timedelta(minutes=150)}], OFF) is True


def test_intraday_sina_stale_rejected(tmp_path, monkeypatch):
    """gtimg 偶败降级 sina 时，若 sina 返回未来异常分时（陈旧错标到今日），守卫应拒绝注入。"""
    from app.db.models.market import MarketQuote

    s, eng = _setup(tmp_path)
    NOW = datetime(2026, 7, 29, 6, 0, 0)  # UTC = 14:00 北京
    monkeypatch.setattr(Collector, "_now", lambda self: NOW)

    # sina 返回未来异常分时：北京 15:30（晚于 NOW 14:00 北京，属陈旧错标）
    stale = pd.DataFrame([
        {"day": pd.Timestamp(2026, 7, 29, 15, 30, 0), "open": 4.7, "high": 4.7, "low": 4.7, "close": 4.7, "volume": 100.0},
        {"day": pd.Timestamp(2026, 7, 29, 15, 31, 0), "open": 4.7, "high": 4.7, "low": 4.7, "close": 4.7, "volume": 100.0},
    ])
    stale.attrs["__source"] = "sina"

    class _StaleSina(_Provider):
        def get_intraday_minute(self, symbol_type, code):
            return stale

    def boom(code, kind):
        raise RuntimeError("gtimg down")

    c = Collector(_StaleSina(), s, gtimg_intraday_fetcher=boom)
    with session_scope(eng) as session:
        res = c.collect_intraday_minute(session)

    assert res["ok"] == 0 and res["failed"] == 2  # 未来异常 sina 被拒，计失败（不污染当日分时）
    with session_scope(eng) as session:
        assert session.query(MarketQuote).filter_by(data_source="sina", timeframe="1m").count() == 0


def test_intraday_sina_fresh_accepted(tmp_path, monkeypatch):
    """gtimg 偶败降级 sina 时，若 sina 返回临近 now 的当日分时，应正常入库。"""
    from app.db.models.market import MarketQuote
    from datetime import date as _date

    s, eng = _setup(tmp_path)
    NOW = datetime(2026, 7, 29, 6, 0, 0)  # UTC = 14:00 北京
    monkeypatch.setattr(Collector, "_now", lambda self: NOW)
    # 锁定交易日与分时日期一致（避免沙箱真实日期翻滚导致 normalize 按 trading_date 过滤掉硬编码日期行）
    monkeypatch.setattr("app.collector.collector.trading_date_for", lambda *a, **k: _date(2026, 7, 29))

    fresh = pd.DataFrame([
        {"day": pd.Timestamp(2026, 7, 29, 14, 0, 0), "open": 4.7, "high": 4.7, "low": 4.7, "close": 4.7, "volume": 100.0},
        {"day": pd.Timestamp(2026, 7, 29, 14, 1, 0), "open": 4.7, "high": 4.7, "low": 4.7, "close": 4.7, "volume": 100.0},
    ])
    fresh.attrs["__source"] = "sina"

    class _FreshSina(_Provider):
        def get_intraday_minute(self, symbol_type, code):
            return fresh

    def boom(code, kind):
        raise RuntimeError("gtimg down")

    c = Collector(_FreshSina(), s, gtimg_intraday_fetcher=boom)
    with session_scope(eng) as session:
        res = c.collect_intraday_minute(session)

    assert res["ok"] == 2 and res["failed"] == 0
    with session_scope(eng) as session:
        assert session.query(MarketQuote).filter_by(data_source="sina", timeframe="1m").count() == 4  # 2 标的 x 2 行


def test_purge_intraday_before_keeps_only_current_day(tmp_path):
    """只保留 keep_date 当日的 1m 分时；更旧交易日 1m 被删；1d/快照不受影响。"""
    from datetime import datetime, timedelta

    from app.db.models.market import MarketQuote
    from app.repository import quote_repo

    s = get_settings(force_reload=True)
    s.paths.sqlite_path_abs = tmp_path / "etf_monitor.db"
    s.paths.backup_dir_abs = tmp_path / "backups"
    s.paths.log_dir_abs = tmp_path / "logs"
    eng = make_engine(s)
    init_db(eng, s)

    old = date(2024, 1, 9)
    new = date(2024, 1, 10)

    def _row(td: date, tf: str, kind: str, minute: int):
        return MarketQuote(
            data_source="gtimg", symbol_type="ETF", symbol="510300",
            data_kind=kind, timeframe=tf, trading_date=td,
            timestamp=datetime(2024, 1, td.day, 9, 30 + minute),
            close=4.7, collected_at=datetime.utcnow(),
        )

    with session_scope(eng) as session:
        # 旧交易日 1m（应删）、新交易日 1m（应留）、旧交易日 1d（应留）、旧交易日快照（应留）
        session.add(_row(old, "1m", "BAR", 0))
        session.add(_row(old, "1m", "BAR", 1))
        session.add(_row(new, "1m", "BAR", 0))
        session.add(_row(old, "1d", "BAR", 0))
        session.add(_row(old, "snapshot", "SNAPSHOT", 0))
        session.commit()

        before = session.query(MarketQuote).count()
        assert before == 5

        purged = quote_repo.purge_intraday_before(session, new)
        assert purged == 2  # 仅旧交易日 1m 两条

        remaining = session.query(MarketQuote).order_by(MarketQuote.timeframe, MarketQuote.trading_date).all()
        assert len(remaining) == 3
        assert {(r.timeframe, r.trading_date.isoformat()) for r in remaining} == {
            ("1m", "2024-01-10"),
            ("1d", "2024-01-09"),
            ("snapshot", "2024-01-09"),
        }
