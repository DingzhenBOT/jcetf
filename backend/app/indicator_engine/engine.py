"""指标编排（P3）。

IndicatorEngine.compute(bar_df, benchmark_close=None) -> dict
- bar_df：按 timestamp 升序的 BAR（列 close/high/low/volume/amount/timestamp）。
- benchmark_close：可选基准 DataFrame（优先，含 trading_date/close）或兼容序列。
- 只吃 BAR；不开 HTTP、不碰 Session。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from app.indicator_engine import indicators


class IndicatorEngine:
    def compute(
        self, bar_df: Any, benchmark_close: Optional[Any] = None
    ) -> Dict[str, Any]:
        if bar_df is None or len(bar_df) == 0:
            return {}

        df = (
            bar_df.sort_values("timestamp").reset_index(drop=True)
            if hasattr(bar_df, "sort_values")
            else pd.DataFrame(bar_df)
        )
        close = pd.Series(df["close"].astype("float64"))

        out: Dict[str, Any] = {
            "ma20": indicators.sma(close, 20),
            "ma20_slope": indicators.ma_slope_pct(close, 20, 5),
            "rsi14": indicators.rsi(close, 14),
            "macd": indicators.macd(close),
            "mom_5": indicators.momentum(close, 5),
            "mom_10": indicators.momentum(close, 10),
            "mom_20": indicators.momentum(close, 20),
            "vol_ratio": None,
            "atr_pct": None,
            "rs_20d": None,
        }

        if "volume" in df.columns:
            out["vol_ratio"] = indicators.vol_ratio(df["volume"].astype("float64"))
        if "high" in df.columns and "low" in df.columns:
            out["atr_pct"] = indicators.atr_pct(
                df["high"], df["low"], close
            )

        if benchmark_close is not None and len(benchmark_close) > 0:
            # 策略路径传 DataFrame：按共同交易日 inner join，杜绝停牌/源缺日时按位置错配。
            if (
                isinstance(benchmark_close, pd.DataFrame)
                and "trading_date" in df.columns
                and "trading_date" in benchmark_close.columns
                and "close" in benchmark_close.columns
            ):
                target = df[["trading_date", "close"]].copy()
                base = benchmark_close[["trading_date", "close"]].copy()
                target["trading_date"] = pd.to_datetime(target["trading_date"]).dt.date
                base["trading_date"] = pd.to_datetime(base["trading_date"]).dt.date
                target = target.drop_duplicates("trading_date", keep="last")
                base = base.drop_duplicates("trading_date", keep="last")
                aligned = target.merge(base, on="trading_date", how="inner", suffixes=("_target", "_base"))
                out["rs_20d"] = indicators.rolling_rs(
                    aligned["close_target"], aligned["close_base"], 20
                )
            else:
                # 兼容已有调用方；新策略调用一律走上面的交易日对齐路径。
                out["rs_20d"] = indicators.rolling_rs(close, benchmark_close, 20)

        return out
