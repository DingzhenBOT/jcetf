"""盘中强度与 R1/R2 规则（C23，确定性，无 LLM）。

方法论来源：
- 短线交易·盘中节点评估：五因子加权强度分（相对大盘 / 量能 / 均线多空 / 资金 / 筹码）。
- 持仓监控告警 R1 补仓看多 / R2 超跌抄底。

纯函数、可单测；缺失因子权重重归一化（与 strategy_engine.compute_composite 一致）。
所有输入来自自有采集库（market_quote 1m BAR / 日线 / 板块资金），不依赖外部 CLI。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np
import pandas as pd


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _weighted(scores: Dict[str, Optional[float]], weights: Dict[str, float]) -> Dict[str, Any]:
    """缺失因子权重重归一化（D4）。返回 {score, available, missing}。"""
    avail = {k: v for k, v in scores.items() if v is not None}
    miss = [k for k in weights if k not in avail]
    tw = sum(weights[k] for k in avail) or 1.0
    norm = {k: weights[k] / tw for k in avail}
    score = sum(norm[k] * avail[k] for k in avail)
    return {"score": _clamp(score), "available": list(avail.keys()), "missing": miss}


def intraday_strength(
    etf_1m: pd.DataFrame,
    index_1m: Optional[pd.DataFrame] = None,
    *,
    daily_avg_volume: Optional[float] = None,
    trading_progress: float = 1.0,
    sector_flow_score: Optional[float] = None,
) -> Dict[str, Any]:
    """盘中实时强度（0-100）+ 倾向 + 五因子明细。

    五因子（对齐短线交易盘中节点评估）：相对大盘 30% / 量能 20% / 均线多空 20% / 资金 20% / 筹码 10%。
    筹码因子当前采集不含（无筹码成本分布），恒为缺失 -> 其余因子权重重归一化。

    etf_1m：ETF 当日 1m BAR（含 close/volume/amount）。
    index_1m：宽基指数当日 1m BAR（相对大盘用）。
    daily_avg_volume：近 N 日日均成交量（量能对比基线）。
    trading_progress：当前交易进度 0-1（把当日累计量折算到「当前时点预期量」）。
    sector_flow_score：板块资金持续性评分（可选）。
    """
    factors: Dict[str, Optional[float]] = {}

    # 1) 相对大盘（30%）
    if index_1m is not None and len(index_1m) >= 2 and len(etf_1m) >= 2:
        e = _intraday_chg(etf_1m)
        i = _intraday_chg(index_1m)
        if e is not None and i is not None:
            rel = e - i
            factors["rel_market"] = _clamp(50 + rel * 25)

    # 2) 量能（20%）：当日累计量 vs 当前进度下的预期量
    if daily_avg_volume and daily_avg_volume > 0 and len(etf_1m):
        cum_vol = float(etf_1m["volume"].astype("float64").sum())
        prog = max(0.05, min(1.0, trading_progress))
        expected = daily_avg_volume * prog
        vr = cum_vol / expected if expected > 0 else None
        if vr is not None:
            factors["volume"] = _clamp(50 + (vr - 1) * 80)

    # 3) 均线多空（20%）：现价 vs 当日 VWAP 与 1m MA20
    if len(etf_1m) >= 2:
        last = float(etf_1m["close"].astype("float64").iloc[-1])
        vwap = _vwap(etf_1m)
        ma20_1m = float(etf_1m["close"].astype("float64").iloc[-20:].mean()) if len(etf_1m) >= 20 else last
        if vwap and vwap > 0:
            above_vwap = last >= vwap
            above_ma = last >= ma20_1m
            if above_vwap and above_ma:
                factors["ma"] = 78.0
            elif above_vwap or above_ma:
                factors["ma"] = 58.0
            else:
                factors["ma"] = 28.0

    # 4) 资金（20%）
    if sector_flow_score is not None:
        factors["fund"] = _clamp(sector_flow_score)

    # 5) 筹码（10%）：无数据 -> 缺失，重归一化（未来扩展）

    weights = {"rel_market": 30, "volume": 20, "ma": 20, "fund": 20, "chip": 10}
    res = _weighted(factors, weights)
    score = res["score"]
    lean = "看多" if score >= 60 else ("看空" if score <= 40 else "中性")
    return {
        "score": round(score, 1),
        "lean": lean,
        "factors": {k: round(v, 1) for k, v in factors.items() if v is not None},
        "available": res["available"],
        "missing": res["missing"],
    }


def _intraday_chg(df: pd.DataFrame) -> Optional[float]:
    """当日 1m 首末收盘的涨跌幅（%）。"""
    closes = df["close"].astype("float64").dropna()
    if len(closes) < 2:
        return None
    first, last = float(closes.iloc[0]), float(closes.iloc[-1])
    if first == 0:
        return None
    return (last / first - 1) * 100.0


def _vwap(df: pd.DataFrame) -> Optional[float]:
    """当日 VWAP（成交额/成交量）；无成交额则退化为价格均值。"""
    c = df["close"].astype("float64")
    v = df["volume"].astype("float64")
    amt = df.get("amount")
    if amt is not None and float(amt.astype("float64").sum()) > 0:
        tot = float(amt.astype("float64").sum())
        totv = float(v.sum())
        return tot / totv if totv > 0 else None
    return float(c.mean()) if len(c) else None


def check_r1_r2(
    daily_df: pd.DataFrame,
    etf_ind: Dict[str, Any],
    fund_flow: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """持仓监控告警 R1 补仓看多 / R2 超跌抄底（确定性）。

    R1 补仓看多：收盘价站上 MA20（日）+ 主力资金净流入>0 + 连续≥2天（资金不可用时仅价格条件连续2天）。
    R2 超跌抄底：接近/跌破布林下轨 + 当日量比>1.5 + RSI(6)<20。
    """
    out: Dict[str, Any] = {"r1": False, "r2": False, "detail": {}}
    if daily_df is None or len(daily_df) < 2:
        return out
    closes = daily_df["close"].astype("float64").dropna()
    if len(closes) < 2:
        return out

    ma20_series = closes.rolling(20).mean()
    last_close = float(closes.iloc[-1])
    ma20 = etf_ind.get("ma20")
    if ma20 is None and not ma20_series.isna().iloc[-1]:
        ma20 = float(ma20_series.iloc[-1])

    above_ma = ma20 is not None and last_close > ma20
    # 连续站上 MA20 天数
    consec = 0
    if not ma20_series.isna().iloc[-1]:
        for k in range(1, min(3, len(closes) + 1)):
            idx = -k
            if not ma20_series.isna().iloc[idx] and float(closes.iloc[idx]) > float(ma20_series.iloc[idx]):
                consec += 1
            else:
                break

    fund_pos = False
    if fund_flow and fund_flow.get("available"):
        fs = fund_flow.get("score")
        fund_pos = (fs is not None and fs > 0) or bool((fund_flow.get("main_net_inflow") or 0) > 0)

    # R1
    if above_ma and consec >= 2:
        if fund_pos or fund_flow is None:  # 资金可用且为正，或资金不可用（降级仅看价格）
            out["r1"] = True
    out["detail"]["r1"] = {
        "above_ma20": above_ma,
        "consecutive_above_ma20_days": consec,
        "fund_inflow_positive": fund_pos,
    }

    # R2：布林下轨 / 量比 / RSI6
    boll_lower = etf_ind.get("boll_lower")
    near_lower = boll_lower is not None and last_close <= boll_lower * 1.02
    vol_ratio = etf_ind.get("vol_ratio")
    vol_surge = vol_ratio is not None and vol_ratio > 1.5
    rsi6 = _rsi(closes, 6)
    oversold = rsi6 is not None and rsi6 < 20
    if near_lower and vol_surge and oversold:
        out["r2"] = True
    out["detail"]["r2"] = {
        "near_boll_lower": near_lower,
        "vol_ratio": round(vol_ratio, 2) if vol_ratio is not None else None,
        "rsi6": round(rsi6, 1) if rsi6 is not None else None,
    }
    return out


def _rsi(series: pd.Series, n: int = 6) -> Optional[float]:
    s = series.astype("float64").dropna()
    if len(s) <= n:
        return None
    delta = s.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    roll_up = up.rolling(n).mean()
    roll_down = down.rolling(n).mean()
    rd = roll_down.iloc[-1]
    if rd > 0:
        rs = roll_up.iloc[-1] / rd
        return float(100 - 100 / (1 + rs))
    return 100.0
