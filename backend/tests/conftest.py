"""pytest 公共 fixtures（P0 + P4）。

- 每个测试前后清空配置缓存，保证 env 覆盖测试互不串扰。
- P4：提供 api_client fixture —— 临时 SQLite 播种映射/信号/意见/宽度/指数 BAR，
  并通过 app.dependency_overrides[get_db] 把 API 切到该临时库，验证只读查询端点。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.config import clear_cache
from app.db import init_db, make_engine, session_scope
from app.db.models.market import MarketBreadth, MarketQuote
from app.db.models.signal_opinion import Opinion, Signal
from app.main import app
from app.repository import mapping_repo, quote_repo


@pytest.fixture(autouse=True)
def _reset_config_cache():
    clear_cache()
    yield
    clear_cache()


def _seed(tmp_path, with_breadth: bool = True, with_etf_quote: bool = False):
    """建库 + 播种：3 支生效 ETF（其中 510050 无信号）、信号、意见、宽度、指数 BAR。"""
    from app.config import get_settings

    s = get_settings(force_reload=True)
    s.paths.sqlite_path_abs = tmp_path / "etf_monitor.db"
    s.paths.backup_dir_abs = tmp_path / "backups"
    s.paths.log_dir_abs = tmp_path / "logs"
    eng = make_engine(s)
    init_db(eng, s)

    with session_scope(eng) as session:
        # 3 支生效 ETF
        mapping_repo.upsert_mapping(
            session, etf_code="510300", etf_name="沪深300ETF",
            related_sector_codes=["BK0465"], related_index_code="000300",
            category="宽基", mapping_version="v1",
            valid_from=date(2000, 1, 1), valid_to=None, notes="t",
        )
        mapping_repo.upsert_mapping(
            session, etf_code="510500", etf_name="中证500ETF",
            related_sector_codes=["BK0736"], related_index_code="000905",
            category="宽基", mapping_version="v1",
            valid_from=date(2000, 1, 1), valid_to=None, notes="t",
        )
        mapping_repo.upsert_mapping(
            session, etf_code="510050", etf_name="上证50ETF",
            related_sector_codes=["BK0469"], related_index_code="000016",
            category="宽基", mapping_version="v1",
            valid_from=date(2000, 1, 1), valid_to=None, notes="t",
        )

        # 信号：510300=MARKET_RISK_HIGH, 510500=NO_PARTICIPATE（测信号风险汇总），510050 无信号
        def _sig(code, tier, gen, score=35.0, conf=55):
            return Signal(
                signal_id=f"sig-{code}-{tier}", strategy_version="v1.0.0-test",
                generated_at=gen, trading_date=date(2025, 7, 18), target_etf=code,
                signal_type=tier, score=score, confidence=conf,
                market_regime="VOLATILE", suggested_action=tier,
                suggested_position_range=[0, 0],
                supporting_metrics={"advance_ratio": None, "market_regime": "VOLATILE"},
                risk_flags={"high_vol": True}, triggered_rules=["market_index_available"],
                failed_rules=["broad_index_missing", "breadth_missing"],
                invalidation_conditions={"market_regime_bear": False},
                review_time=datetime(2025, 7, 21, 0, 50),
            )

        session.add(_sig("510300", "MARKET_RISK_HIGH", datetime(2025, 7, 18, 15, 10)))
        # 同一 etf 的更旧一条（验证 latest 取 MAX(generated_at)）
        session.add(_sig("510300", "OBSERVE", datetime(2025, 7, 17, 15, 10), score=70))
        session.add(_sig("510500", "NO_PARTICIPATE", datetime(2025, 7, 18, 15, 10)))

        # 意见：510300 两条（不同 phase），验证按 generated_at desc + phase 过滤
        session.add(Opinion(
            opinion_id="op1", signal_id="sig-510300-MARKET_RISK_HIGH",
            generated_at=datetime(2025, 7, 18, 15, 10), trading_date=date(2025, 7, 18),
            phase="post_close", title="复盘", content="沪深300ETF｜市场风险较高。",
            input_summary={"etf_code": "510300"}, template_version="template-v1",
        ))
        session.add(Opinion(
            opinion_id="op2", signal_id="sig-510300-MARKET_RISK_HIGH",
            generated_at=datetime(2025, 7, 18, 11, 30), trading_date=date(2025, 7, 18),
            phase="midday", title="盘中", content="沪深300ETF｜盘中观察。",
            input_summary={"etf_code": "510300"}, template_version="template-v1",
        ))

        if with_breadth:
            # 宽度一行（供 breadth/latest + overview）
            session.add(MarketBreadth(
                trading_date=date(2025, 7, 18), timestamp=datetime(2025, 7, 18, 7, 0),
                total_rise=2200, total_fall=2300, total_flat=300,
                limit_up=45, limit_down=12, total_amount=9.0e10,
                data_source="sina", data_quality_status="OK",
            ))

        # 指数 BAR（供 overview 的 indices）
        quote_repo.upsert_market_quotes(session, [{
            "data_source": "sina", "symbol_type": "INDEX", "symbol": "000300",
            "data_kind": "BAR", "timeframe": "1d", "trading_date": date(2025, 7, 18),
            "timestamp": datetime(2025, 7, 18, 15, 0),
            "open": 3990.0, "high": 4010.0, "low": 3980.0, "close": 4000.0,
            "previous_close": 3980.0, "volume": 1_000_000, "amount": 2.0e11,
            "change_percent": 0.5, "turnover_rate": None, "main_net_inflow": None,
            "large_order_inflow": None, "rise_count": None, "fall_count": None,
            "limit_up_count": None, "limit_down_count": None,
            "collected_at": datetime(2025, 7, 18, 15, 5), "source_timestamp": None,
            "metric_source": "sina", "metric_definition_version": "v1",
            "source_switched": 0, "data_quality_status": "OK",
        }])

        # 可选：510300 的 ETF 最新 SNAPSHOT（供 P6 盈亏计算测试）
        if with_etf_quote:
            quote_repo.upsert_market_quotes(session, [{
                "data_source": "sina", "symbol_type": "ETF", "symbol": "510300",
                "data_kind": "SNAPSHOT", "timeframe": "snapshot", "trading_date": date(2025, 7, 18),
                "timestamp": datetime(2025, 7, 18, 15, 0),
                "open": 3.90, "high": 4.05, "low": 3.88, "close": 4.00,
                "previous_close": 3.90, "volume": 5_000_000, "amount": 2.0e10,
                "change_percent": 2.56, "turnover_rate": None, "main_net_inflow": None,
                "large_order_inflow": None, "rise_count": None, "fall_count": None,
                "limit_up_count": None, "limit_down_count": None,
                "collected_at": datetime(2025, 7, 18, 15, 5), "source_timestamp": None,
                "metric_source": "sina", "metric_definition_version": "v1",
                "source_switched": 0, "data_quality_status": "OK",
            }])

    return s, eng


def _make_client(eng):
    from app.db.session import make_session_factory

    sf = make_session_factory(eng)

    def _override():
        session = sf()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override
    client = TestClient(app)
    return client


@pytest.fixture()
def api_client(tmp_path):
    s, eng = _seed(tmp_path, with_breadth=True)
    client = _make_client(eng)
    with client:
        yield client
    app.dependency_overrides.clear()
    eng.dispose()


@pytest.fixture()
def api_client_no_breadth(tmp_path):
    s, eng = _seed(tmp_path, with_breadth=False)
    client = _make_client(eng)
    with client:
        yield client
    app.dependency_overrides.clear()
    eng.dispose()


@pytest.fixture()
def api_client_quote(tmp_path):
    """带 510300 ETF 最新行情的客户端（供 P6 盈亏计算测试）。"""
    s, eng = _seed(tmp_path, with_breadth=True, with_etf_quote=True)
    client = _make_client(eng)
    with client:
        yield client
    app.dependency_overrides.clear()
    eng.dispose()


# --------------------------------------------------------------------------- #
# P7 回测测试数据（合成 ETF/指数/宽度 BAR，250+ 交易日；含样本内/外各一段行情）
# --------------------------------------------------------------------------- #
def _weekdays(start: date, n: int):
    out = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _seed_backtest(session, settings, n_days: int = 260):
    """在已开 session 内播种回测所需数据：映射 + ETF BAR + 指数 BAR + 每日宽度。

    价格形态：上涨 -> 下跌 -> 再上涨，使样本内/外各产生交易（便于验证 R5 分离）。
    """
    import math

    mapping_repo.upsert_mapping(
        session, etf_code="510300", etf_name="沪深300ETF",
        related_sector_codes=["BK0465"], related_index_code="000300",
        category="宽基", mapping_version="v1",
        valid_from=date(2000, 1, 1), valid_to=None, notes="bt",
    )

    days = _weekdays(date(2024, 1, 1), n_days)
    base = 3.5
    prices = []
    for i in range(n_days):
        if i < 150:
            trend = 1 + 0.004 * i
        elif i < 200:
            trend = 1 + 0.004 * 150 - 0.004 * (i - 150)
        else:
            trend = 1 + 0.004 * 150 - 0.004 * 50 + 0.004 * (i - 200)
        prices.append(round(max(0.5, base * trend * (1 + 0.01 * math.sin(i / 7.0))), 4))

    etf_rows = []
    idx_rows = []
    for i, d in enumerate(days):
        close = prices[i]
        prev = prices[i - 1] if i > 0 else close
        op = round(prev * (1 + 0.002 * math.sin(i)), 4)
        hi = round(max(op, close) * 1.003, 4)
        lo = round(min(op, close) * 0.997, 4)
        etf_rows.append({
            "data_source": "sina", "symbol_type": "ETF", "symbol": "510300",
            "data_kind": "BAR", "timeframe": "1d", "trading_date": d,
            "timestamp": datetime(d.year, d.month, d.day, 15, 0),
            "open": op, "high": hi, "low": lo, "close": close,
            "previous_close": prev, "volume": 1_000_000, "amount": 2.0e9,
            "change_percent": round((close / prev - 1) * 100, 3) if i > 0 else 0.0,
            "turnover_rate": None, "main_net_inflow": None, "large_order_inflow": None,
            "rise_count": None, "fall_count": None, "limit_up_count": None, "limit_down_count": None,
            "collected_at": datetime(d.year, d.month, d.day, 15, 5), "source_timestamp": None,
            "metric_source": "sina", "metric_definition_version": "v1",
            "source_switched": 0, "data_quality_status": "OK",
        })
        iclose = round(3000 * (1 + 0.003 * i) * (1 + 0.008 * math.sin(i / 9.0)), 2)
        iprev = round(3000 * (1 + 0.003 * (i - 1)) * (1 + 0.008 * math.sin((i - 1) / 9.0)), 2) if i > 0 else iclose
        iop = round(iprev * 1.001, 2)
        idx_rows.append({
            "data_source": "sina", "symbol_type": "INDEX", "symbol": "000300",
            "data_kind": "BAR", "timeframe": "1d", "trading_date": d,
            "timestamp": datetime(d.year, d.month, d.day, 15, 0),
            "open": iop, "high": round(max(iop, iclose) * 1.002, 2), "low": round(min(iop, iclose) * 0.998, 2),
            "close": iclose, "previous_close": iprev, "volume": 1_000_000, "amount": 2.0e11,
            "change_percent": round((iclose / iprev - 1) * 100, 3) if i > 0 else 0.0,
            "turnover_rate": None, "main_net_inflow": None, "large_order_inflow": None,
            "rise_count": None, "fall_count": None, "limit_up_count": None, "limit_down_count": None,
            "collected_at": datetime(d.year, d.month, d.day, 15, 5), "source_timestamp": None,
            "metric_source": "sina", "metric_definition_version": "v1",
            "source_switched": 0, "data_quality_status": "OK",
        })
    quote_repo.upsert_market_quotes(session, etf_rows)
    quote_repo.upsert_market_quotes(session, idx_rows)
    for i, d in enumerate(days):
        adv = 0.55 + 0.1 * math.sin(i / 11.0)
        quote_repo.upsert_breadth(session, {
            "trading_date": d, "timestamp": datetime(d.year, d.month, d.day, 7, 0),
            "total_rise": int(adv * 5000), "total_fall": int((1 - adv) * 5000), "total_flat": 200,
            "limit_up": 40, "limit_down": 15, "total_amount": 9.0e10,
            "data_source": "sina", "data_quality_status": "OK",
        })
    return days


@pytest.fixture()
def backtest_db(tmp_path):
    """回测引擎单测用：(eng, settings, days)，已播种合成历史数据。"""
    s, eng = _seed(tmp_path, with_breadth=True)
    from app.db.session import session_scope

    with session_scope(eng) as session:
        days = _seed_backtest(session, s)
    yield eng, s, days
    eng.dispose()


@pytest.fixture()
def backtest_client(tmp_path):
    """回测端点测试用：TestClient（读库覆盖）+ eng（供测试内模拟 Worker 执行）。"""
    s, eng = _seed(tmp_path, with_breadth=True)
    from app.db.session import session_scope

    with session_scope(eng) as session:
        _seed_backtest(session, s)
    client = _make_client(eng)
    with client:
        yield client, eng
    app.dependency_overrides.clear()
    eng.dispose()
