"""收盘后三档价位（C23，确定性，无 LLM）。

方法论来源：
- A股每日复盘·三档建仓参考：左侧试探（支撑+底分型+缩量止跌）/ 趋势跟进（均线收复+MACD 拐头）/ 突破加仓（前高突破+放量确认）。
- 短线交易·条件→操作→止损框架：突破上车 / 回踩加仓 / 破位止损，均给具体价位与触发条件。

纯函数、可单测；价位由日线 + 技术指标（MA20 / 布林带 / 前高前低 / ATR）确定性计算。
所有输入来自自有采集库，不依赖外部 CLI。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd


def _num(x, digits: int = 3):
    if x is None:
        return None
    try:
        return round(float(x), digits)
    except (TypeError, ValueError):
        return None


def compute_trade_plan(
    daily_df: pd.DataFrame,
    etf_ind: Dict[str, Any],
    *,
    lookback: int = 20,
) -> Dict[str, Any]:
    """计算明日三档操作参考（突破/加仓/止损）+ 明日预期区间 + 倾向。

    返回结构（price 为 None 表示数据不足，对应条件注明）：
    {
      "breakout_price": float|None, "breakout_cond": str,
      "add_price": float|None,       "add_cond": str,
      "stop_price": float|None,       "stop_cond": str,
      "expectation_low": float|None,  "expectation_high": float|None,
      "regime_tomorrow": str,
      "notes": [str],
    }
    """
    notes: list[str] = []
    plan: Dict[str, Any] = {
        "breakout_price": None, "breakout_cond": "",
        "add_price": None, "add_cond": "",
        "stop_price": None, "stop_cond": "",
        "expectation_low": None, "expectation_high": None,
        "regime_tomorrow": "震荡",
        "notes": notes,
    }

    if daily_df is None or len(daily_df) < 5:
        notes.append("日线样本不足，无法计算三档价位")
        return plan

    closes = daily_df["close"].astype("float64").dropna()
    highs = daily_df["high"].astype("float64").dropna()
    lows = daily_df["low"].astype("float64").dropna()
    if len(closes) < 2:
        notes.append("日线收盘价缺失，无法计算三档价位")
        return plan

    last_close = float(closes.iloc[-1])
    win = min(lookback, len(closes))
    recent_high = float(highs.iloc[-win:].max())
    recent_low = float(lows.iloc[-win:].min())

    ma20 = etf_ind.get("ma20")
    boll_upper = etf_ind.get("boll_upper")
    boll_mid = etf_ind.get("boll_mid")
    boll_lower = etf_ind.get("boll_lower")
    ma20_slope = etf_ind.get("ma20_slope")
    atr = etf_ind.get("atr_pct")  # % 形式

    # ---- 突破上车价：前高（或布林上轨，取高者），略高于当前 ----
    breakout = recent_high
    if boll_upper is not None:
        breakout = max(breakout, float(boll_upper))
    breakout = max(breakout, last_close * 1.005)
    plan["breakout_price"] = _num(breakout)
    plan["breakout_cond"] = "放量站上前高/布林上轨即可上车；无量假突破需警惕回落"

    # ---- 加仓价：回踩 MA20（或布林中轨/近期支撑），取当前价下方最近的有效支撑 ----
    add_candidates = [c for c in [ma20, boll_mid, recent_low] if c is not None and c < last_close]
    add = max(add_candidates) if add_candidates else recent_low
    if add is None or add >= breakout:
        add = min(last_close * 0.985, (last_close + breakout) / 2)
    plan["add_price"] = _num(add)
    plan["add_cond"] = "缩量回踩上述支撑企稳可加仓；跌破则放弃加仓"

    # ---- 止损价：只采用加仓价下方的有效保护位，并取最近一档以限制损失 ----
    stop_candidates = [recent_low]
    if boll_lower is not None:
        stop_candidates.append(float(boll_lower))
    if atr and atr > 0:
        stop_candidates.append(last_close * (1 - 1.5 * atr / 100.0))
    valid_stops = [c for c in stop_candidates if add is not None and 0 < c < add]
    stop = max(valid_stops) if valid_stops else add * 0.97
    plan["stop_price"] = _num(stop)
    plan["stop_cond"] = "跌破关键前低/布林下轨即止损，不抱幻想"

    # ---- 明日预期区间：±1 ATR（百分比换算为价格） ----
    if atr and atr > 0:
        plan["expectation_low"] = _num(last_close * (1 - atr / 100.0))
        plan["expectation_high"] = _num(last_close * (1 + atr / 100.0))
    elif len(closes) >= 2:
        # 退化为近 5 日波动
        recent = closes.iloc[-min(5, len(closes)):]
        sd = float(recent.pct_change().std() * 100.0) if len(recent) > 1 else 2.0
        sd = sd if sd > 0 else 2.0
        plan["expectation_low"] = _num(last_close * (1 - sd / 100.0))
        plan["expectation_high"] = _num(last_close * (1 + sd / 100.0))
    else:
        notes.append("ATR 缺失，预期区间用近 5 日波动近似")

    # ---- 明日倾向 ----
    if ma20 is not None and last_close > ma20 and (ma20_slope is None or ma20_slope > 0):
        plan["regime_tomorrow"] = "偏多"
    elif ma20 is not None and last_close < ma20:
        plan["regime_tomorrow"] = "偏弱"
    else:
        plan["regime_tomorrow"] = "震荡"

    return plan
