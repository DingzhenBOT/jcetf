"""纯 pandas 技术指标（P3）。

输入：序列（list / pd.Series）/ DataFrame 列；输出 scalar（None 表示样本不足）或 dict。
设计约束：
- 全部为确定性计算，无随机、无网络、无 I/O。
- 样本不足（长度 < 窗口）一律返回 None，调用方据此判定「缺失」并优雅降级。
- pandas 3.0 兼容：用 .astype("float64") 规整（None -> NaN），均值默认跳过 NaN。
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Union

import pandas as pd

Numeric = Union[Sequence[float], "pd.Series"]


def _as_series(x: Numeric) -> "pd.Series":
    return pd.Series(x, dtype="float64")


def sma(series: Numeric, n: int) -> Optional[float]:
    """简单移动平均最新值。"""
    s = _as_series(series)
    if len(s) < n:
        return None
    v = s.iloc[-n:].mean()
    return None if v != v else float(v)


def ma_slope_pct(series: Numeric, n: int = 20, lookback: int = 5) -> Optional[float]:
    """MA(n) 在 lookback 日内的斜率百分比（(MA_now / MA_prev - 1) * 100）。"""
    s = _as_series(series)
    if len(s) < n + lookback:
        return None
    ma = s.rolling(n).mean()
    cur = ma.iloc[-1]
    prev = ma.iloc[-lookback]
    if cur != cur or prev != prev or prev == 0:
        return None
    return float((cur / prev - 1) * 100)


def rsi(series: Numeric, n: int = 14) -> Optional[float]:
    """Wilder RSI(14)。全涨 -> 100；全平 -> 50；样本不足 -> None。"""
    s = _as_series(series)
    if len(s) < n + 1:
        return None
    delta = s.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    avg_loss = loss.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    g = avg_gain.iloc[-1]
    l = avg_loss.iloc[-1]
    if g != g and l != l:
        return None
    if l == 0:
        return 100.0 if (g > 0) else 50.0
    rs = g / l
    return float(100 - 100 / (1 + rs))


def macd(series: Numeric, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """MACD：返回 {dif, dea, hist}；样本不足则全 None。"""
    s = _as_series(series)
    empty = {"dif": None, "dea": None, "hist": None}
    if len(s) < slow + signal:
        return empty
    ema_fast = s.ewm(span=fast, adjust=False).mean()
    ema_slow = s.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = (dif - dea) * 2
    return {
        "dif": float(dif.iloc[-1]),
        "dea": float(dea.iloc[-1]),
        "hist": float(hist.iloc[-1]),
    }


def momentum(series: Numeric, n: int) -> Optional[float]:
    """n 日动量（close/close.shift(n) - 1）。"""
    s = _as_series(series)
    if len(s) < n + 1:
        return None
    prev = s.iloc[-n - 1]
    cur = s.iloc[-1]
    if prev != prev or cur != cur or prev == 0:
        return None
    return float(cur / prev - 1)


def momentum_percentile(series: Numeric, n: int, window: int = 120) -> Optional[float]:
    """最新 n 日动量在最近 window 个动量值中的分位（0-100）。"""
    s = _as_series(series)
    if len(s) < n + 1:
        return None
    mom = (s / s.shift(n) - 1).dropna()
    if len(mom) < 2:
        return None
    last = mom.iloc[-1]
    tail = mom.iloc[-window:]
    if last != last:
        return None
    rank = (tail < last).sum() / len(tail)
    return float(rank * 100)


def vol_ratio(vol_series: Numeric, n: int = 5) -> Optional[float]:
    """量比：最新成交量 / 前 n 日均量。"""
    s = _as_series(vol_series)
    if len(s) < n + 1:
        return None
    mean = s.iloc[-n - 1:-1].mean()
    cur = s.iloc[-1]
    if mean != mean or mean == 0 or cur != cur:
        return None
    return float(cur / mean)


def atr(high: Numeric, low: Numeric, close: Numeric, n: int = 14) -> Optional[float]:
    """Wilder ATR(n)。"""
    h = _as_series(high)
    l = _as_series(low)
    c = _as_series(close)
    if len(c) < n + 1 or len(h) < n + 1 or len(l) < n + 1:
        return None
    prev_close = c.shift(1)
    tr = pd.concat(
        [(h - l), (h - prev_close).abs(), (l - prev_close).abs()], axis=1
    ).max(axis=1)
    atr_vals = tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    v = atr_vals.iloc[-1]
    return None if v != v else float(v)


def atr_pct(high: Numeric, low: Numeric, close: Numeric, n: int = 14) -> Optional[float]:
    """ATR 占收盘价百分比（波动率护栏）。"""
    a = atr(high, low, close, n)
    c = _as_series(close)
    if a is None or len(c) == 0:
        return None
    last_close = c.iloc[-1]
    if last_close != last_close or last_close == 0:
        return None
    return float(a / last_close * 100)


def rolling_rs(target_close: Numeric, base_close: Numeric, n: int = 20) -> Optional[float]:
    """滚动 n 日相对强弱：target 与 base 的累计收益比（prod(1+ret_t)/prod(1+ret_b)）。>1 跑赢。"""
    t = _as_series(target_close).iloc[-n:]
    b = _as_series(base_close).iloc[-n:]
    if len(t) < 2 or len(b) < 2:
        return None
    t_ret = (t / t.shift(1) - 1).dropna()
    b_ret = (b / b.shift(1) - 1).dropna()
    if len(t_ret) == 0 or len(b_ret) == 0:
        return None
    pt, pb = t_ret.prod(), b_ret.prod()
    if pt != pt or pb != pb or pb == 0 or pt == 0:
        return None
    return float(pt / pb)
