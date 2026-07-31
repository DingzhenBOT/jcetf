"""indicator_engine 单测（P3）：纯函数指标在合成 BAR 上的已知值。

约定：
- 线性上涨序列 -> RSI=100；全平序列 -> RSI=50。
- 已知 ATR / 动量 / 量比 / 滚动 RS。
- 样本不足 -> None（优雅降级）。
"""
import pandas as pd

from app.indicator_engine import indicators as I


def test_rsi_linear_rising_is_100():
    s = list(range(1, 101))
    assert I.rsi(s, 14) == 100.0


def test_rsi_flat_is_50():
    s = [10.0] * 100
    assert I.rsi(s, 14) == 50.0


def test_rsi_insufficient_returns_none():
    assert I.rsi([1, 2], 14) is None


def test_sma_known():
    assert I.sma([1, 2, 3, 4, 5], 3) == 4.0
    assert I.sma([1, 2], 5) is None


def test_ma_slope_positive_for_rising():
    s = list(range(1, 7))  # 1..6
    slope = I.ma_slope_pct(s, n=3, lookback=3)
    assert slope is not None and slope > 0


def test_macd_defined_for_rising():
    s = list(range(1, 60))
    m = I.macd(s)
    assert m["dif"] is not None and m["dif"] > 0
    assert m["dea"] is not None and m["hist"] is not None


def test_macd_insufficient_returns_none_dict():
    m = I.macd([1, 2, 3], fast=12, slow=26, signal=9)
    assert m == {"dif": None, "dea": None, "hist": None}


def test_momentum_known():
    assert I.momentum([1, 2, 3], 1) == 0.5


def test_vol_ratio_known():
    # 前 5 日均量=10，最新=20 -> 2.0
    s = [10, 10, 10, 10, 10, 20]
    assert I.vol_ratio(s, 5) == 2.0


def test_bollinger_flat_series_collapses_to_same_price():
    b = I.bollinger([10.0] * 20, 20, 2.0)
    assert b == {"upper": 10.0, "mid": 10.0, "lower": 10.0}
    assert I.bollinger([10.0] * 19, 20) == {"upper": None, "mid": None, "lower": None}


def test_atr_known_constant_range():
    # 每日 high-low=2 且 prev_close 在 [low,high] 内 -> TR 恒为 2 -> ATR=2
    high = [3, 3, 3, 3]
    low = [1, 1, 1, 1]
    close = [1, 1, 1, 1]
    assert I.atr(high, low, close, 3) == 2.0


def test_atr_pct_scales():
    high = [3, 3, 3, 3]
    low = [1, 1, 1, 1]
    close = [2, 2, 2, 2]
    ap = I.atr_pct(high, low, close, 3)
    assert ap is not None and abs(ap - 100.0) < 1e-6  # ATR=2 / close=2 *100 = 100


def test_rolling_rs_equal_series_is_one():
    s = list(range(1, 30))
    assert abs(I.rolling_rs(s, list(range(1, 30)), 20) - 1.0) < 1e-9


def test_rolling_rs_uses_growth_factors_not_product_of_returns():
    target = [100.0 * (2.0 ** (i / 20)) for i in range(21)]
    base = [100.0 * (1.1 ** (i / 20)) for i in range(21)]
    assert abs(I.rolling_rs(target, base, 20) - (2.0 / 1.1)) < 1e-12
    assert I.rolling_rs(target[:-1], base[:-1], 20) is None


def test_indicator_engine_compute_empty():
    assert I is not None
    from app.indicator_engine.engine import IndicatorEngine

    eng = IndicatorEngine()
    assert eng.compute(pd.DataFrame(), benchmark_close=None) == {}


def test_indicator_engine_compute_with_benchmark_sets_rs():
    import pandas as pd

    from app.indicator_engine.engine import IndicatorEngine

    df = pd.DataFrame(
        {
            "timestamp": pd.date_range("2024-01-01", periods=24, freq="D"),
            "close": list(range(1, 25)),
            "high": [c + 1 for c in range(1, 25)],
            "low": [c - 1 for c in range(1, 25)],
            "volume": [10] * 24,
        }
    )
    eng = IndicatorEngine()
    out = eng.compute(df, benchmark_close=list(range(2, 26)))
    assert "rs_20d" in out
    assert out["rs_20d"] is not None


def test_indicator_engine_aligns_rs_on_common_trading_dates():
    from app.indicator_engine.engine import IndicatorEngine

    dates = pd.date_range("2024-01-01", periods=22, freq="D")
    target = pd.DataFrame({
        "timestamp": dates,
        "trading_date": dates.date,
        "close": [100.0 + i for i in range(22)],
    })
    benchmark = pd.DataFrame({
        "trading_date": [d.date() for i, d in enumerate(dates) if i != 10],
        "close": [2.0 * (100.0 + i) for i in range(22) if i != 10],
    })
    out = IndicatorEngine().compute(target, benchmark)
    assert abs(out["rs_20d"] - 1.0) < 1e-12
