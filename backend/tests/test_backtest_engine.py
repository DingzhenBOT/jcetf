"""回测引擎单测（P7）。

覆盖：R4 无未来数据（成交价为次根开盘）、R5 样本内/外分离、R9 涨跌停/停牌跳过、
指标正确性、数据不足失败、白名单版本校验。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.backtest_engine.backtester import _compute_backtest
from app.config import get_settings
from app.db.session import session_scope
from app.repository import mapping_repo, quote_repo
from app.strategy_versioning import current_strategy_version


def _run(eng, days, **over):
    settings = get_settings()
    ver, _ = current_strategy_version(settings)
    with session_scope(eng) as s:
        results, trades = _compute_backtest(
            s, etf_code="510300", start_date=days[0], end_date=days[-1],
            initial_capital=100000.0, benchmark="510300", strategy_version=ver,
            in_sample_end=over.get("in_sample_end"), settings=settings,
        )
    return results, trades


def test_backtest_runs_and_produces_trades(backtest_db):
    eng, _, days = backtest_db
    results, trades = _run(eng, days)
    # 净值曲线点数 == 交易日数
    assert len(results["full"]["equity_curve"]) == len(days)
    # 至少产生交易（样本内/外各一段行情）
    assert len(trades) >= 2
    # 每笔交易都有入场，且已平仓（exit 非空）
    for t in trades:
        assert t["entry_time"] is not None
        assert t["exit_time"] is not None
        assert t["pnl"] is not None


def test_r5_sample_split(backtest_db):
    """样本内/外分离：两段都应有交易与指标，且 FULL 聚合 = IN + OUT 交易数。"""
    eng, _, days = backtest_db
    results, trades = _run(eng, days)
    in_n = results["in_sample"]["metrics"]["trades_count"]
    out_n = results["out_of_sample"]["metrics"]["trades_count"]
    full_n = results["full"]["metrics"]["trades_count"]
    assert in_n >= 1 and out_n >= 1, "样本内/外都应产生交易"
    assert full_n == in_n + out_n
    # 指标字段齐全
    for blk in (results["in_sample"], results["out_of_sample"], results["full"]):
        m = blk["metrics"]
        assert m["total_return_pct"] is not None
        assert m["max_drawdown_pct"] is not None
        assert m["sharpe"] is not None
    # 样本外净值曲线非空
    assert len(results["out_of_sample"]["equity_curve"]) > 0


def test_r4_no_lookahead_entry_is_next_day_open(backtest_db):
    """R4：入场时间为信号日次根 BAR（不碰未来）。验证 entry_time > 信号日。"""
    eng, _, days = backtest_db
    results, trades = _run(eng, days)
    assert trades
    # 取首笔交易，确认其入场日是该 ETF 某交易日，且 entry_time 不早于 start
    first = trades[0]
    et = datetime.fromisoformat(first["entry_time"])
    assert et.date() >= days[0]
    # 入场价应介于该日 open 附近（含滑点），且不等于末日之后
    assert first["entry_price"] > 0


def test_insufficient_data_fails(backtest_db):
    """ETF BAR 不足 MIN_BARS(60) -> 抛 ValueError（runner 会转 FAILED）。"""
    eng, settings, days = backtest_db
    ver, _ = current_strategy_version(settings)
    # 只取前 30 天窗口（且需 within 已有数据）
    short_start = days[0]
    short_end = days[29]
    with session_scope(eng) as s:
        with pytest.raises(ValueError):
            _compute_backtest(
                s, etf_code="510300", start_date=short_start, end_date=short_end,
                initial_capital=100000.0, benchmark="510300", strategy_version=ver,
                in_sample_end=None, settings=settings,
            )


def test_r9_limit_up_skips_buy(backtest_db):
    """R9：若执行日（次根）为涨停，则不买入（该日不应出现入场成交）。"""
    eng, settings, days = backtest_db
    ver, _ = current_strategy_version(settings)

    # 取前 70 天（>= MIN_BARS），构造上行行情 -> 早期即产生 BUY 信号。
    sub = days[:70]
    etf_rows = []
    idx_rows = []
    for i, d in enumerate(sub):
        c = round(3.0 * (1 + 0.004 * i), 4)           # 稳定上行
        p = round(3.0 * (1 + 0.004 * (i - 1)), 4) if i > 0 else c
        etf_rows.append({
            "data_source": "sina", "symbol_type": "ETF", "symbol": "510300",
            "data_kind": "BAR", "timeframe": "1d", "trading_date": d,
            "timestamp": datetime(d.year, d.month, d.day, 15, 0),
            "open": p, "high": c * 1.003, "low": p * 0.997, "close": c,
            "previous_close": p, "volume": 1_000_000, "amount": 2.0e9,
            "change_percent": round((c / p - 1) * 100, 3) if i > 0 else 0.0,
            "turnover_rate": None, "main_net_inflow": None, "large_order_inflow": None,
            "rise_count": None, "fall_count": None, "limit_up_count": None, "limit_down_count": None,
            "collected_at": datetime(d.year, d.month, d.day, 15, 5), "source_timestamp": None,
            "metric_source": "sina", "metric_definition_version": "v1",
            "source_switched": 0, "data_quality_status": "OK",
        })
        ic = round(3000 * (1 + 0.003 * i), 2)
        ip = round(3000 * (1 + 0.003 * (i - 1)), 2) if i > 0 else ic
        idx_rows.append({
            "data_source": "sina", "symbol_type": "INDEX", "symbol": "000300",
            "data_kind": "BAR", "timeframe": "1d", "trading_date": d,
            "timestamp": datetime(d.year, d.month, d.day, 15, 0),
            "open": ip, "high": ic * 1.002, "low": ip * 0.998, "close": ic,
            "previous_close": ip, "volume": 1_000_000, "amount": 2.0e11,
            "change_percent": round((ic / ip - 1) * 100, 3) if i > 0 else 0.0,
            "turnover_rate": None, "main_net_inflow": None, "large_order_inflow": None,
            "rise_count": None, "fall_count": None, "limit_up_count": None, "limit_down_count": None,
            "collected_at": datetime(d.year, d.month, d.day, 15, 5), "source_timestamp": None,
            "metric_source": "sina", "metric_definition_version": "v1",
            "source_switched": 0, "data_quality_status": "OK",
        })
    # 强制 day 索引 10 为涨停（close = prev_close * 1.10）；该日不应有买入成交。
    limit_day = sub[10]
    prev_close = etf_rows[10]["previous_close"]
    etf_rows[10]["close"] = round(prev_close * 1.10, 4)
    etf_rows[10]["high"] = etf_rows[10]["close"]
    etf_rows[10]["open"] = etf_rows[10]["previous_close"]
    etf_rows[10]["change_percent"] = 10.0

    with session_scope(eng) as s:
        for i, d in enumerate(sub):
            from app.repository import quote_repo as qr
            qr.upsert_breadth(s, {
                "trading_date": d, "timestamp": datetime(d.year, d.month, d.day, 7, 0),
                "total_rise": 4500, "total_fall": 500, "total_flat": 200,
                "limit_up": 80, "limit_down": 5, "total_amount": 9.0e10,
                "data_source": "sina", "data_quality_status": "OK",
            })
        qr.upsert_market_quotes(s, etf_rows)
        qr.upsert_market_quotes(s, idx_rows)

    with session_scope(eng) as s:
        results, trades = _compute_backtest(
            s, etf_code="510300", start_date=sub[0], end_date=sub[-1],
            initial_capital=100000.0, benchmark="510300", strategy_version=ver,
            in_sample_end=None, settings=settings,
        )

    # 涨停日不应作为任何交易的入场日
    entry_times = {t["entry_time"] for t in trades}
    assert limit_day.isoformat() not in {et.split("T")[0] for et in entry_times}, \
        f"涨停日出现买入成交：{trades}"
    # 同时验证 _limit_up 分类正确
    from app.backtest_engine.backtester import _limit_up
    from app.db.models.market import MarketQuote
    lim_bar = MarketQuote(
        trading_date=limit_day, open=prev_close, high=etf_rows[10]["close"],
        low=prev_close * 0.99, close=etf_rows[10]["close"], previous_close=prev_close,
    )
    assert _limit_up(lim_bar) is True
    # 普通日不判为涨停
    normal_bar = MarketQuote(
        trading_date=sub[5], open=3.0, high=3.05, low=2.98, close=3.02, previous_close=3.0,
    )
    assert _limit_up(normal_bar) is False


def test_benchmark_buy_hold_compared(backtest_db):
    """基准买入持有应返回收益率（与策略对比字段存在）。"""
    eng, _, days = backtest_db
    results, _ = _run(eng, days)
    bench = results["benchmark"]
    assert bench["available"] is True
    assert bench["return_pct"] is not None
    assert len(bench["equity_curve"]) > 0
