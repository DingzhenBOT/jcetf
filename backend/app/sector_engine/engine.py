"""板块趋势 + 资金持续性（P3，DESIGN §9.1-2）。

- evaluate_sector_trend：板块 BAR 的技术评分（站上 MA20 且上行、动量分位、RSI 健康/过热）。
- evaluate_fund_flow：资金持续性，**仅同数据源（metric_source）** 计算（大单/主力口径必须同源才可比）。
  - 主力净流入连续为正天数（默认 >=3 加分）；
  - 净流入强度 = 净流入 / 板块成交额（跨期累计）；
  - 大单同向确认加分，背离减分。
- 任一输入缺失 -> 返回 available=False、score=None（调用方据此降级权重，不否决，见 D4）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from app.indicator_engine.engine import IndicatorEngine


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


class SectorEngine:
    def __init__(self) -> None:
        self.ind = IndicatorEngine()

    def evaluate_sector_trend(self, sector_bar_df: Any) -> Dict[str, Any]:
        """板块趋势评分（0-100）。无 BAR -> available=False。"""
        if sector_bar_df is None or len(sector_bar_df) == 0:
            return {"available": False, "score": None, "risk_overheat": False, "supporting": {}}

        df = (
            sector_bar_df.sort_values("timestamp").reset_index(drop=True)
            if hasattr(sector_bar_df, "sort_values")
            else pd.DataFrame(sector_bar_df)
        )
        m = self.ind.compute(df)
        close = pd.Series(df["close"].astype("float64"))
        last_close = float(close.iloc[-1])

        score = 0.0
        supp: Dict[str, Any] = {}
        risk_overheat = False

        if m["ma20"] is not None:
            above = last_close > m["ma20"]
            supp["above_ma20"] = above
            if above:
                score += 35
            if m["ma20_slope"] is not None and m["ma20_slope"] > 0:
                score += 20
                supp["ma20_rising"] = True

        if m["mom_20"] is not None:
            supp["mom_20"] = m["mom_20"]
            if m["mom_20"] > 0:
                score += 15
                supp["mom20_pos"] = True

        if m["rsi14"] is not None:
            supp["rsi14"] = m["rsi14"]
            if 50 <= m["rsi14"] <= 70:
                score += 15
                supp["rsi_healthy"] = True
            elif m["rsi14"] > 80:
                risk_overheat = True
                supp["rsi_overheat"] = True
            elif m["rsi14"] >= 40:
                score += 8

        if m["mom_5"] is not None and m["mom_5"] > 0:
            score += 5
            supp["mom5_pos"] = True

        return {
            "available": True,
            "score": _clamp(score),
            "risk_overheat": risk_overheat,
            "supporting": supp,
        }

    def evaluate_fund_flow(
        self, flow_df: Any, metric_source: Optional[str]
    ) -> Dict[str, Any]:
        """资金持续性评分（0-100），仅同数据源口径可比。无同源数据 -> available=False。"""
        if flow_df is None or len(flow_df) == 0:
            return {"available": False, "score": None, "consecutive_positive_days": 0,
                    "inflow_strength": None, "divergence": False, "note": "empty"}

        df = (
            flow_df.sort_values("timestamp").reset_index(drop=True)
            if hasattr(flow_df, "sort_values")
            else pd.DataFrame(flow_df)
        )
        # 同数据源过滤（资金口径必须一致）
        if metric_source is not None and "metric_source" in df.columns:
            df = df[df["metric_source"] == metric_source]
        if len(df) == 0:
            return {"available": False, "score": None, "consecutive_positive_days": 0,
                    "inflow_strength": None, "divergence": False, "note": "no same-source flow"}

        net = pd.Series(df["main_net_inflow"].astype("float64"))

        # 末端连续为正天数
        cons = 0
        for v in reversed(net.tolist()):
            if v is not None and v == v and v > 0:
                cons += 1
            else:
                break

        score = 0.0
        if cons >= 3:
            score += 40
        elif cons == 2:
            score += 25
        elif cons == 1:
            score += 10

        # 净流入强度 = 净流入合计 / 成交额合计
        inflow_strength: Optional[float] = None
        if "amount" in df.columns:
            amount = pd.Series(df["amount"].astype("float64"))
            denom = amount.abs().sum()
            if denom > 0:
                inflow_strength = float(net.sum() / denom)
                if inflow_strength > 0.01:
                    score += 30
                elif inflow_strength > 0:
                    score += 15
                elif inflow_strength > -0.01:
                    score += 5

        # 大单同向确认 / 背离
        divergence = False
        if "large_order_inflow" in df.columns:
            lo = pd.Series(df["large_order_inflow"].astype("float64"))
            last_net = net.iloc[-1]
            last_lo = lo.iloc[-1] if not lo.empty else None
            if last_net is not None and last_net == last_net and last_lo is not None and last_lo == last_lo:
                if (last_net > 0) == (last_lo > 0):
                    score += 10
                else:
                    divergence = True
                    score = max(0.0, score - 10)

        return {
            "available": True,
            "score": _clamp(score),
            "consecutive_positive_days": cons,
            "inflow_strength": inflow_strength,
            "divergence": divergence,
        }
