"""指标引擎包（P3）。

纯函数指标（indicators）+ 编排（engine）。只消费 BAR 行（不开 HTTP、不碰 Session）。
"""
from __future__ import annotations

from app.indicator_engine.engine import IndicatorEngine
from app.indicator_engine.indicators import (
    atr,
    atr_pct,
    macd,
    ma_slope_pct,
    momentum,
    momentum_percentile,
    rolling_rs,
    rsi,
    sma,
    vol_ratio,
)

__all__ = [
    "IndicatorEngine",
    "sma",
    "ma_slope_pct",
    "rsi",
    "macd",
    "momentum",
    "momentum_percentile",
    "vol_ratio",
    "atr",
    "atr_pct",
    "rolling_rs",
]
