"""C23：收盘后三档价位单测（P3，确定性，无 LLM）。

方法论来源：A股每日复盘·三档建仓参考 + 短线交易·条件→操作→止损。
核心不变量：止损 < 加仓 < 突破（单调），且均为正。
"""
from datetime import datetime, timedelta

import pandas as pd

from app.opinion_engine.levels import compute_trade_plan


def _daily_uptrend(n=25, start=3.0, end=4.0):
    rows = []
    for i in range(n):
        frac = i / (n - 1)
        c = round(start + (end - start) * frac, 4)
        rows.append({
            "open": c, "high": round(c + 0.02, 4), "low": round(c - 0.02, 4), "close": c,
            "volume": 1_000_000, "amount": 2e9,
            "timestamp": datetime(2026, 7, 30) - timedelta(days=n - i),
        })
    return pd.DataFrame(rows)


def test_trade_plan_monotonic_and_positive():
    df = _daily_uptrend(n=25, start=3.0, end=4.0)
    ind = {
        "ma20": 3.85, "boll_upper": 4.05, "boll_mid": 3.85, "boll_lower": 3.65,
        "ma20_slope": 0.01, "atr_pct": 2.0,
    }
    plan = compute_trade_plan(df, ind)
    bp, ap, sp = plan["breakout_price"], plan["add_price"], plan["stop_price"]
    assert bp is not None and ap is not None and sp is not None
    assert bp > 0 and ap > 0 and sp > 0
    # 核心不变量：止损 < 加仓 < 突破
    assert sp < ap < bp
    # 明日预期区间存在且低<高
    assert plan["expectation_low"] is not None and plan["expectation_high"] is not None
    assert plan["expectation_low"] < plan["expectation_high"]
    # 倾向取值合理
    assert plan["regime_tomorrow"] in ("偏多", "偏弱", "震荡")
    assert plan["breakout_cond"] and plan["add_cond"] and plan["stop_cond"]


def test_trade_plan_insufficient_data_returns_none():
    df = _daily_uptrend(n=3, start=3.9, end=4.0)
    plan = compute_trade_plan(df, {"ma20": 3.95, "atr_pct": 1.5})
    assert plan["breakout_price"] is None
    assert plan["notes"]  # 注明数据不足


def test_trade_plan_downTrend_regime_weak():
    # 下行趋势（收盘 < MA20）-> 明日倾向偏弱。
    df = _daily_uptrend(n=25, start=4.2, end=3.6)
    ind = {
        "ma20": 4.0, "boll_upper": 4.2, "boll_mid": 4.0, "boll_lower": 3.7,
        "ma20_slope": -0.02, "atr_pct": 2.5,
    }
    plan = compute_trade_plan(df, ind)
    assert plan["regime_tomorrow"] == "偏弱"
    assert plan["stop_price"] < plan["add_price"] < plan["breakout_price"]


def test_trade_plan_uses_nearest_support_and_protective_stop():
    df = _daily_uptrend(n=25, start=3.0, end=4.0)
    plan = compute_trade_plan(df, {
        "ma20": 3.92, "boll_mid": 3.88, "boll_lower": 3.80,
        "boll_upper": 4.10, "atr_pct": 2.0,
    })
    # 加仓优先当前价下方最近支撑（MA20），止损优先其下方最近保护位。
    assert plan["add_price"] == 3.92
    assert plan["stop_price"] == 3.88
