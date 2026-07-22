"""量价关系技术分析（方案B，确定性，无网络 / 无随机）。

输入：按 timestamp 升序的 BAR DataFrame（需含 open/high/low/close/volume）。
输出：dict，含量价关系状态、量能状态、识别出的形态、强度分。

方法论参考 A股短线交易技能 references/ta_signals.md：
- 量价关系矩阵：放量涨(做多) / 缩量涨(动力不足) / 放量跌(出货) / 缩量跌(洗盘) / 缩量横(蓄势)。
- 量能状态：放量(>150% MA20) / 温和(100-150%) / 平量(80-100%) / 缩量(<80%) / 极度缩量(<50%)。
- 形态：放量突破 / 缩量洗盘 / 量价背离 / 分段量涨阳线 / 异动放量。

本模块只吃 BAR，不开 HTTP、不碰 Session；全部为确定性计算，样本不足返回空结论。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd


# 量价关系状态 -> 中文展示
VP_STATE_TEXT: Dict[str, str] = {
    "VOL_UP_RISE": "放量上涨",
    "VOL_DOWN_RISE": "缩量上涨",
    "VOL_UP_FALL": "放量下跌",
    "VOL_DOWN_FALL": "缩量下跌",
    "VOL_LOW_FLAT": "缩量横盘",
    "FLAT": "量能平稳",
}

# 形态 code -> 中文展示
VP_PATTERN_TEXT: Dict[str, str] = {
    "breakout_volume": "放量突破",
    "shrink_wash": "缩量洗盘",
    "divergence": "量价背离",
    "segment_up": "分段量涨阳线",
    "anomaly": "异动放量",
}

_MIN_BARS = 21  # 需要 MA20 + 当前根


def classify_vol_state(vol_ratio_ma20: Optional[float]) -> str:
    """量能状态分类（基于量比 = 当日量 / MA20 量）。"""
    if vol_ratio_ma20 is None:
        return "未知"
    if vol_ratio_ma20 > 1.5:
        return "放量"
    if vol_ratio_ma20 >= 1.0:
        return "温和放量"
    if vol_ratio_ma20 >= 0.8:
        return "平量"
    if vol_ratio_ma20 >= 0.5:
        return "缩量"
    return "极度缩量"


def analyze_volume_price(bar_df: Any) -> Dict[str, Any]:
    """对 ETF 日线 BAR 做量价分析，返回结论字典（确定性）。"""
    empty = {
        "vp_state": None,
        "vp_state_text": "数据不足",
        "vp_vol_ratio_state": "未知",
        "vp_vol_ratio_ma20": None,
        "vp_patterns": [],
        "vp_strength": None,
        "vp_anomaly": False,
    }
    if bar_df is None or len(bar_df) == 0:
        return empty
    df = (
        bar_df.sort_values("timestamp").reset_index(drop=True)
        if hasattr(bar_df, "sort_values")
        else pd.DataFrame(bar_df)
    )
    if "close" not in df.columns or "volume" not in df.columns:
        return empty

    close = df["close"].astype("float64")
    vol = df["volume"].astype("float64")
    n = len(df)
    if n < _MIN_BARS:
        return {**empty, "vp_state_text": "样本不足"}

    ma20_close = float(close.iloc[-20:].mean())
    ma20_vol = float(vol.iloc[-20:].mean())
    last_close = float(close.iloc[-1])
    last_vol = float(vol.iloc[-1])
    prev_close = float(close.iloc[-2])
    vol_ratio_ma20 = (last_vol / ma20_vol) if ma20_vol > 0 else None

    change = (last_close / prev_close - 1) if prev_close and prev_close > 0 else 0.0
    up = change > 0.0005
    down = change < -0.0005
    flat = not (up or down)

    vol_state = classify_vol_state(vol_ratio_ma20)
    vol_surge = vol_ratio_ma20 is not None and vol_ratio_ma20 > 1.5

    # 1) 量价关系矩阵
    if up and vol_surge:
        state = "VOL_UP_RISE"
    elif up and vol_ratio_ma20 is not None and vol_ratio_ma20 < 0.8:
        state = "VOL_DOWN_RISE"
    elif down and vol_surge:
        state = "VOL_UP_FALL"
    elif down and vol_ratio_ma20 is not None and vol_ratio_ma20 < 0.8:
        state = "VOL_DOWN_FALL"
    elif flat and vol_ratio_ma20 is not None and vol_ratio_ma20 < 0.8:
        state = "VOL_LOW_FLAT"
    else:
        state = "FLAT"

    # 2) 形态识别
    patterns: List[str] = []
    mom5 = (float(close.iloc[-1]) / float(close.iloc[-6]) - 1) if n >= 6 and close.iloc[-6] else None
    if state == "VOL_UP_RISE" and last_close > ma20_close and (mom5 is None or mom5 > 0):
        patterns.append("breakout_volume")
    if down and vol_ratio_ma20 is not None and vol_ratio_ma20 < 0.8 and last_close >= ma20_close * 0.97:
        patterns.append("shrink_wash")
    recent_high = float(close.iloc[-20:].max())
    if last_close >= recent_high * 0.999 and (vol_ratio_ma20 is None or vol_ratio_ma20 < 1.0):
        patterns.append("divergence")
    if n >= 3 and "open" in df.columns:
        o3 = df["open"].astype("float64").iloc[-3:]
        c3 = close.iloc[-3:]
        v3 = vol.iloc[-3:]
        if all(c3.iloc[i] > o3.iloc[i] for i in range(3)) and v3.iloc[-1] > v3.iloc[-2] > v3.iloc[-3]:
            patterns.append("segment_up")
    anomaly = (vol_ratio_ma20 is not None and vol_ratio_ma20 > 2.5) or abs(change) > 0.05
    if anomaly:
        patterns.append("anomaly")

    # 3) 强度分（0-100）：量价配合 + 趋势位置 + 形态
    strength = 50.0
    if state == "VOL_UP_RISE":
        strength += 25
    elif state == "VOL_DOWN_RISE":
        strength += 5
    elif state == "VOL_UP_FALL":
        strength -= 25
    elif state == "VOL_DOWN_FALL":
        strength += 5  # 洗盘潜在企稳
    elif state == "VOL_LOW_FLAT":
        strength += 0
    if last_close > ma20_close:
        strength += 10
    if "breakout_volume" in patterns or "segment_up" in patterns:
        strength += 10
    if "divergence" in patterns:
        strength -= 15
    if "anomaly" in patterns and not up:
        strength -= 5
    strength = max(0.0, min(100.0, strength))

    return {
        "vp_state": state,
        "vp_state_text": VP_STATE_TEXT.get(state, "量能平稳"),
        "vp_vol_ratio_state": vol_state,
        "vp_vol_ratio_ma20": vol_ratio_ma20,
        "vp_patterns": patterns,
        "vp_strength": strength,
        "vp_anomaly": anomaly,
    }
