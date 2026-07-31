"""方案B：场外基金（listing='场外'）端到端单测。

验证场外开放式基金（如 110020 沪深300ETF联接A）注册进 etf_mapping（listing='场外'）后，
复用场内 ETF 引擎产出同构意见的设计：

- 归一化：基金净值序列 -> symbol_type=OFF_FUND 的 BAR（open=high=low=close=NAV，previous_close=前一日NAV）。
- 适配器：akshare 东财中文列（净值日期/单位净值/日增长率）-> 英文列（date/nav/change_percent）。
- 采集回填：backfill_history 将 listing='场外' 路由到 collect_offexchange_nav_history（OFF_FUND 管道），
  与场内 ETF 历史物理隔离；桩提供方缺失该方法时被优雅降级（记 FAILED，不抛上层）。
- 策略引擎：evaluate_etf 对 listing='场外' 读取 OFF_FUND 净值序列（bar_type=OFF_FUND），
  收盘后产出三档价位 trade_plan；盘中/lunch 不产三档（场外 T+1 无盘中分时）。
- 评估流水线：live/lunch 相位跳过场外（skipped_offexchange 计数，不计入 errors）；post_close 评估场外。
- API：/api/market/etf/{code}/history 对场外基金读取 OFF_FUND 净值序列，返回 open==close 的净值点。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import get_db
from app.collector import normalize
from app.collector.collector import Collector
from app.config import get_settings
from app.data_provider.akshare_adapter import AkShareAdapter
from app.data_provider.base import BaseDataProvider
from app.db import init_db, make_engine, session_scope
from app.db.models.signal_opinion import Opinion, Signal
from app.db.session import make_session_factory
from app.evaluation.pipeline import post_collection_evaluate
from app.main import app
from app.repository import mapping_repo, quote_repo
from app.strategy_engine.engine import StrategyEngine
from app.strategy_engine.rules import RULES_V1
from app.strategy_versioning import mint_strategy_version


# --------------------------------------------------------------------------- #
# 测试夹具 / 辅助
# --------------------------------------------------------------------------- #
def _weekdays_ending(end, n):
    out = []
    d = end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return list(reversed(out))


def _bar(symbol_type: str, symbol: str, d: date, close: float, prev_close: float | None):
    """构造一条日线 BAR 字典（净值/ETF/指数通用；净值模式 open=high=low=close）。"""
    return {
        "data_source": "em", "symbol_type": symbol_type, "symbol": symbol,
        "data_kind": "BAR", "timeframe": "1d", "trading_date": d,
        "timestamp": datetime(d.year, d.month, d.day, 15, 0),
        "open": close, "high": close, "low": close, "close": close,
        "previous_close": prev_close,
        "volume": 1_000_000, "amount": 2e9,
        "change_percent": round((close / prev_close - 1) * 100, 3) if prev_close else 0.0,
        "turnover_rate": None, "main_net_inflow": None, "large_order_inflow": None,
        "rise_count": None, "fall_count": None, "limit_up_count": None, "limit_down_count": None,
        "collected_at": datetime(d.year, d.month, d.day, 15, 5), "source_timestamp": None,
        "metric_source": "em", "metric_definition_version": "v1",
        "source_switched": 0, "data_quality_status": "OK",
    }


def _seed_db(tmp_path, *, with_on_exchange: bool = True, seed_off_fund: bool = True):
    s = get_settings(force_reload=True)
    s.paths.sqlite_path_abs = tmp_path / "etf_monitor.db"
    s.paths.backup_dir_abs = tmp_path / "backups"
    s.paths.log_dir_abs = tmp_path / "logs"
    s.strategy.broad_index_codes = ["000300"]
    eng = make_engine(s)
    init_db(eng, s)

    T = date.today()
    days = _weekdays_ending(T, 30)
    with session_scope(eng) as session:
        mapping_repo.upsert_mapping(
            session, etf_code="110020", etf_name="沪深300ETF联接A",
            related_sector_codes=[], related_index_code="000300", category="宽基",
            mapping_version="v1", listing="场外",
            valid_from=date(2000, 1, 1), valid_to=None, notes="t",
        )
        if with_on_exchange:
            mapping_repo.upsert_mapping(
                session, etf_code="510300", etf_name="沪深300ETF",
                related_sector_codes=[], related_index_code="000300", category="宽基",
                mapping_version="v1", listing="场内",
                valid_from=date(2000, 1, 1), valid_to=None, notes="t",
            )
        # 跟踪指数 000300 BAR（RS 基准 + market regime）
        idx_rows, prev = [], 3900.0
        for i, d in enumerate(days):
            c = round(prev * (1 + 0.003 * (1 if i % 2 == 0 else -1)), 2)
            idx_rows.append(_bar("INDEX", "000300", d, c, prev))
            prev = c
        quote_repo.upsert_market_quotes(session, idx_rows)
        # 场外净值 BAR（OFF_FUND）— seed_off_fund=False 时留空，由回填补数
        if seed_off_fund:
            nav_rows, prev = [], 1.500
            for i, d in enumerate(days):
                nav = round(prev * (1 + 0.004 * (1 if i % 2 == 0 else -1)), 4)
                nav_rows.append(_bar("OFF_FUND", "110020", d, nav, prev))
                prev = nav
            quote_repo.upsert_market_quotes(session, nav_rows)
        # 场内 510300 ETF BAR（混合回填对照）
        if with_on_exchange:
            etf_rows, prev = [], 3.900
            for i, d in enumerate(days):
                c = round(prev * (1 + 0.005 * (1 if i % 2 == 0 else -1)), 4)
                etf_rows.append(_bar("ETF", "510300", d, c, prev))
                prev = c
            quote_repo.upsert_market_quotes(session, etf_rows)
    return s, eng, T


class _FakeProvider(BaseDataProvider):
    """历史方法返回合成数据；场外净值走独立方法。板块历史不可达（模拟 em 被墙）。"""

    def get_trade_calendar(self):
        return ["20240102"]

    def get_index_snapshot(self):
        return pd.DataFrame()

    def get_etf_snapshot(self):
        return pd.DataFrame()

    def get_sector_ranking(self, sector_type):
        return pd.DataFrame()

    def get_market_breadth_raw(self):
        return pd.DataFrame()

    def get_etf_history(self, symbol, start, end):
        return pd.DataFrame()

    def get_index_history(self, symbol, start, end):
        return pd.DataFrame()

    def get_sector_history(self, symbol, start, end):
        raise RuntimeError("em sector history unreachable (simulated)")

    def get_sector_fund_flow_history(self, symbol, start, end):
        raise RuntimeError("em sector fund flow unreachable (simulated)")

    def get_open_fund_nav_history(self, symbol, start=None, end=None):
        # 英文列（与 akshare_adapter.get_open_fund_nav_history 输出一致）
        df = pd.DataFrame([
            {"date": date(2024, 1, 2), "nav": 1.5000, "change_percent": 1.20},
            {"date": date(2024, 1, 3), "nav": 1.5180, "change_percent": 1.20},
            {"date": date(2024, 1, 4), "nav": 1.5300, "change_percent": 0.79},
        ])
        df.attrs["__source"] = "em"
        return df


# --------------------------------------------------------------------------- #
# 1) 归一化：基金净值 -> OFF_FUND BAR
# --------------------------------------------------------------------------- #
def test_normalize_off_fund_nav_em_columns():
    df = pd.DataFrame([
        {"净值日期": "2024-01-02", "单位净值": 1.5000, "日增长率": 1.20},
        {"净值日期": "2024-01-03", "单位净值": 1.5180, "日增长率": 1.20},
        {"净值日期": "2024-01-04", "单位净值": 1.5300, "日增长率": 0.79},
    ])
    df.attrs["__source"] = "em"
    rows = normalize.normalize_off_fund_nav(df, "em", "110020", datetime(2024, 1, 4, 16, 0))
    assert len(rows) == 3
    assert rows[0]["symbol_type"] == "OFF_FUND" and rows[0]["symbol"] == "110020"
    assert rows[0]["data_kind"] == "BAR" and rows[0]["timeframe"] == "1d"
    # 净值序列：close 承载 NAV；open/high/low 同取 NAV（无 OHLC）
    assert rows[0]["close"] == 1.5000
    assert rows[0]["open"] == 1.5000 and rows[0]["high"] == 1.5000 and rows[0]["low"] == 1.5000
    assert rows[0]["previous_close"] is None  # 首行无前一日
    assert rows[0]["change_percent"] == 1.20
    # 第二行 previous_close = 上一行 NAV；涨跌幅取源值
    assert rows[1]["previous_close"] == 1.5000
    assert rows[1]["change_percent"] == 1.20
    assert rows[2]["previous_close"] == 1.5180


def test_normalize_off_fund_nav_derives_change_percent_when_missing():
    df = pd.DataFrame([
        {"净值日期": "2024-01-02", "单位净值": 1.5000},
        {"净值日期": "2024-01-03", "单位净值": 1.5300},
    ])
    df.attrs["__source"] = "em"
    rows = normalize.normalize_off_fund_nav(df, "em", "110020", datetime(2024, 1, 3, 16, 0))
    assert rows[0]["change_percent"] is None  # 首行无前一日，无法反算
    # 第二行：(1.53 / 1.50 - 1) * 100 = 2.0
    assert abs(rows[1]["change_percent"] - 2.0) < 1e-6


# --------------------------------------------------------------------------- #
# 2) 适配器：东财中文列 -> 英文列
# --------------------------------------------------------------------------- #
def test_adapter_get_open_fund_nav_history_parses_chinese(monkeypatch):
    import akshare as ak

    fake = pd.DataFrame([
        {"净值日期": "2024-01-02", "单位净值": 1.5000, "日增长率": 1.20},
        {"净值日期": "2024-01-03", "单位净值": 1.5180, "日增长率": 1.20},
    ])
    monkeypatch.setattr(ak, "fund_open_fund_info_em", lambda **_kw: fake)

    a = AkShareAdapter(get_settings(force_reload=True))
    out = a.get_open_fund_nav_history("110020")
    assert list(out.columns) == ["date", "nav", "change_percent"]
    assert out.attrs["__source"] == "em"
    assert out.iloc[0]["date"] == date(2024, 1, 2)
    assert out.iloc[0]["nav"] == 1.5000
    assert out.iloc[0]["change_percent"] == 1.20


# --------------------------------------------------------------------------- #
# 3) 采集：collect_offexchange_nav_history 落 OFF_FUND BAR；回填路由到场外
# --------------------------------------------------------------------------- #
def test_collect_offexchange_nav_history_writes_off_fund_bars(tmp_path):
    s, eng, _ = _seed_db(tmp_path, seed_off_fund=False)
    c = Collector(_FakeProvider(), s)
    with session_scope(eng) as session:
        res = c.collect_offexchange_nav_history(session, "110020", "20240101", "20240105")
        assert res["status"] == "OK" and res["count"] == 3
        bars = quote_repo.get_bar_history(session, "OFF_FUND", "110020", date(2024, 1, 1), date(2024, 1, 31))
        assert len(bars) == 3
        assert bars[0].symbol_type == "OFF_FUND"
        assert bars[0].close == 1.5000
        # 与场内 ETF BAR 物理隔离：ETF 类型下无 110020 行
        etf_of_110020 = quote_repo.get_bar_history(session, "ETF", "110020", date(2024, 1, 1), date(2024, 1, 31))
        assert etf_of_110020 == []


def test_backfill_routes_off_fund_and_tallies(tmp_path):
    # 留空 OFF_FUND 历史，让回填真正走 collect_offexchange_nav_history 补数
    s, eng, _ = _seed_db(tmp_path, with_on_exchange=True, seed_off_fund=False)
    c = Collector(_FakeProvider(), s)
    with session_scope(eng) as session:
        r = c.backfill_history(session, as_of=date.today())
        # 场外 110020 走 OFF_FUND 管道并成功补数（FakeProvider 返回 2024-01-02..04 三行净值）
        assert r["off_fund"]["ok"] >= 1
        off_bars = quote_repo.get_bar_history(session, "OFF_FUND", "110020", date(2024, 1, 1), date(2024, 1, 31))
        assert len(off_bars) == 3
        # 场内 510300 仍走 ETF 管道（已 seed 的 BAR 不受场外路由影响）
        etf_bars = quote_repo.get_bar_history(session, "ETF", "510300", date.today() - timedelta(days=120), date.today())
        assert len(etf_bars) >= 25


# --------------------------------------------------------------------------- #
# 4) 策略引擎：场外读 OFF_FUND + 收盘后三档价位
# --------------------------------------------------------------------------- #
def _off_fund_mapping(session, as_of):
    ms = [m for m in mapping_repo.get_active_mappings(session, as_of) if (m.listing or "场内") == "场外"]
    assert ms, "测试需至少一个 listing='场外' 映射"
    return ms[0]


def test_engine_evaluate_off_fund_reads_off_fund_and_trade_plan(tmp_path):
    s, eng, T = _seed_db(tmp_path, with_on_exchange=False)
    with session_scope(eng) as session:
        version = mint_strategy_version(session, s, RULES_V1)
        m = _off_fund_mapping(session, T)
        engine = StrategyEngine(s)
        sig = engine.evaluate_etf(session, m, version, T, phase="post_close")
        # 读 OFF_FUND 成功 -> etf_rs 可用，无 etf_rs_missing 误判
        assert "etf_rs_missing" not in sig["failed_rules"]
        # 收盘后三档价位（基于 NAV 序列）
        tp = sig["trade_plan"]
        assert tp is not None
        assert tp["breakout_price"] > tp["add_price"] > tp["stop_price"]
        assert tp["expectation_low"] < tp["expectation_high"]


def test_engine_off_fund_live_has_no_trade_plan(tmp_path):
    s, eng, T = _seed_db(tmp_path, with_on_exchange=False)
    with session_scope(eng) as session:
        version = mint_strategy_version(session, s, RULES_V1)
        m = _off_fund_mapping(session, T)
        engine = StrategyEngine(s)
        sig = engine.evaluate_etf(session, m, version, T, phase="live")
        # 场外 T+1 无盘中分时 -> live 相位不产三档价位
        assert sig["trade_plan"] is None
        # 但仍读取 OFF_FUND 净值序列（etf_rs 可用）
        assert "etf_rs_missing" not in sig["failed_rules"]


# --------------------------------------------------------------------------- #
# 5) 评估流水线：live/lunch 跳过场外；post_close 评估场外
# --------------------------------------------------------------------------- #
def test_pipeline_skips_offexchange_in_live_phase(tmp_path):
    s, eng, T = _seed_db(tmp_path, with_on_exchange=False)
    with session_scope(eng) as session:
        res = post_collection_evaluate(session, s, phase="live", as_of=T)
        assert res.get("skipped_offexchange", 0) >= 1
        # 场外无 live 相位 Opinion
        n = session.execute(
            select(Opinion).join(Signal, Opinion.signal_id == Signal.signal_id)
            .where(Signal.target_etf == "110020", Opinion.phase == "live")
        ).scalars().all()
        assert len(n) == 0


def test_pipeline_post_close_evaluates_offexchange(tmp_path):
    s, eng, T = _seed_db(tmp_path, with_on_exchange=False)
    with session_scope(eng) as session:
        res = post_collection_evaluate(session, s, phase="post_close", as_of=T)
        assert res.get("skipped_offexchange", 0) == 0
        o = session.execute(
            select(Opinion).join(Signal, Opinion.signal_id == Signal.signal_id)
            .where(Signal.target_etf == "110020", Opinion.phase == "post_close")
        ).scalars().first()
        assert o is not None
        assert o.trade_plan is not None
        assert o.trade_plan["breakout_price"] > o.trade_plan["add_price"] > o.trade_plan["stop_price"]


# --------------------------------------------------------------------------- #
# 6) API：/api/market/etf/{code}/history 对场外基金返回净值序列
# --------------------------------------------------------------------------- #
def test_api_etf_history_returns_off_fund_nav_series(tmp_path):
    s, eng, T = _seed_db(tmp_path, with_on_exchange=False)
    sf = make_session_factory(eng)

    def _override():
        session = sf()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override
    try:
        client = TestClient(app)
        r = client.get("/api/market/etf/110020/history?days=60")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["code"] == "110020"
        assert body["name"] == "沪深300ETF联接A"
        assert len(body["points"]) >= 25
        # 净值序列：open/close/high/low 相等（无 OHLC，close 即 NAV）
        p0 = body["points"][0]
        assert p0["open"] == p0["close"] == p0["high"] == p0["low"]
    finally:
        app.dependency_overrides.clear()
        eng.dispose()
