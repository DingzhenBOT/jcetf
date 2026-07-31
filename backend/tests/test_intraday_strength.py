"""C23：盘中强度与 R1/R2 规则单测（P3，确定性，无 LLM）。

方法论来源：
- 短线交易·盘中节点评估：五因子加权强度（相对大盘/量能/均线/资金/筹码）。
- 持仓监控告警 R1 补仓看多 / R2 超跌抄底。
"""
from datetime import datetime, timedelta

import pandas as pd

from app.opinion_engine.intraday_strength import check_r1_r2, intraday_strength


def _etf_1m(n=30, start=4.0, step=0.0017):
    rows = []
    t0 = datetime(2026, 7, 30, 9, 30, 0)
    for i in range(n):
        c = round(start + step * i, 4)
        rows.append({
            "open": c, "high": c + 0.002, "low": c - 0.002, "close": c,
            "volume": 100_000 + i * 1_000, "amount": c * (100_000 + i * 1_000),
            "timestamp": t0 + timedelta(minutes=i),
        })
    return pd.DataFrame(rows)


def _idx_1m(n=30, start=3400.0, step=0.0):
    rows = []
    t0 = datetime(2026, 7, 30, 9, 30, 0)
    for i in range(n):
        c = round(start + step * i, 2)
        rows.append({
            "open": c, "high": c, "low": c, "close": c,
            "volume": 1_000_000, "amount": 1e9,
            "timestamp": t0 + timedelta(minutes=i),
        })
    return pd.DataFrame(rows)


def _daily(n=25, closes=None):
    if closes is None:
        closes = [round(3.5 + 0.02 * i, 4) for i in range(n)]
    rows = []
    for i, c in enumerate(closes):
        rows.append({
            "open": c, "high": c + 0.02, "low": c - 0.02, "close": c,
            "volume": 1_000_000, "amount": 2e9,
            "timestamp": datetime(2026, 7, 30) - timedelta(days=n - i),
        })
    return pd.DataFrame(rows)


def test_intraday_strength_rising_etf_is_bullish():
    # ETF 震荡上行 + 指数横盘 -> 相对大盘为正；均线/量能支撑 -> 强度偏高、看多。
    etf = _etf_1m(n=30, start=4.0, step=0.0017)
    idx = _idx_1m(n=30, start=3400.0, step=0.0)
    r = intraday_strength(etf, idx, daily_avg_volume=3_000_000, trading_progress=1.0)
    assert r["score"] >= 60
    assert r["lean"] == "看多"
    # 五因子中相对大盘/量能/均线可用；资金/筹码缺失（重归一化）
    assert "rel_market" in r["factors"]
    assert "volume" in r["factors"]
    assert "ma" in r["factors"]
    assert "fund" in r["missing"]
    assert "chip" in r["missing"]
    assert r["available"] == ["rel_market", "volume", "ma"]


def test_intraday_strength_declining_etf_is_bearish():
    # ETF 下行 + 指数上行 -> 相对大盘为负 -> 倾向看空。
    etf = _etf_1m(n=30, start=4.0, step=-0.0017)
    idx = _idx_1m(n=30, start=3400.0, step=1.0)
    r = intraday_strength(etf, idx, daily_avg_volume=3_000_000, trading_progress=1.0)
    assert r["lean"] == "看空"


def test_intraday_strength_missing_index_skips_rel_factor():
    # 无指数分时 -> 相对大盘因子缺失（不报错，重归一化其余因子）。
    etf = _etf_1m(n=30)
    r = intraday_strength(etf, None, daily_avg_volume=3_000_000, trading_progress=1.0)
    assert "rel_market" not in r["factors"]
    assert "rel_market" in r["missing"]
    assert r["score"] >= 0


def test_check_r1_triggers_on_uptrend_above_ma20():
    # R1 补仓看多：收盘价站上 MA20 + 连续≥2天（资金不可用则仅看价格条件）。
    df = _daily(n=25, closes=[round(3.5 + 0.02 * i, 4) for i in range(25)])
    r = check_r1_r2(df, {"ma20": 3.7}, fund_flow=None)
    assert r["r1"] is True
    assert r["r2"] is False
    assert r["detail"]["r1"]["consecutive_above_ma20_days"] >= 2


def test_check_r2_triggers_on_oversold_near_lower_band():
    # R2 超跌抄底：接近/跌破布林下轨 + 量比>1.5 + RSI6<20。
    closes = [4.0] * 19 + [3.8, 3.5, 3.2, 2.9, 2.7, 2.5]
    df = _daily(n=25, closes=closes)
    r = check_r1_r2(df, {"boll_lower": 2.55, "vol_ratio": 2.0}, fund_flow=None)
    assert r["r2"] is True
    assert r["r1"] is False
    assert r["detail"]["r2"]["rsi6"] < 20


def test_check_r1r2_empty_returns_false():
    r = check_r1_r2(pd.DataFrame(), {}, fund_flow=None)
    assert r["r1"] is False and r["r2"] is False
