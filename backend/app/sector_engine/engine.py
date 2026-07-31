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

MIN_FUND_FLOW_OBSERVATIONS = 3


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
        close = pd.Series(df["close"].astype("float64"))
        if close.notna().any():
            m = self.ind.compute(df)
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
        # close 缺失（如 westock 异动榜仅给涨跌幅，无板块指数收盘价）：改用 change_percent 动量
        return self._evaluate_sector_trend_from_change(df)

    def _evaluate_sector_trend_from_change(self, df: Any) -> Dict[str, Any]:
        """close 缺失时的板块趋势降级：用 change_percent（当日涨跌幅）序列做动量评分。

        适用于 westock-data 等只提供每日涨跌幅、无板块收盘价的源。返回 available=True 的动量分，
        使「板块信号从完整历史变为当日异动排名」时引擎仍能产出 sector_trend 分（否则为 0 拖累综合分）。
        """
        cp = pd.Series(df["change_percent"].astype("float64")).dropna()
        if len(cp) == 0:
            return {"available": False, "score": None, "risk_overheat": False, "supporting": {}}
        score = 0.0
        supp: Dict[str, Any] = {}
        recent = cp.tail(5)
        avg5 = float(recent.mean())
        supp["chg_avg5"] = round(avg5, 2)
        if avg5 > 0:
            score += 40
            supp["chg_avg5_pos"] = True
        up_ratio = float((cp > 0).mean())
        supp["up_ratio"] = round(up_ratio, 2)
        if up_ratio >= 0.6:
            score += 25
        if len(cp) >= 2 and cp.iloc[-1] > cp.iloc[-2]:
            score += 10
            supp["accelerating"] = True
        risk_overheat = False
        if len(cp) >= 3 and float(cp.tail(3).mean()) > 5:
            risk_overheat = True
            supp["overheat"] = True
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
        if "main_net_inflow" not in df.columns:
            return {"available": False, "score": None, "consecutive_positive_days": 0,
                    "inflow_strength": None, "divergence": False, "note": "flow metric missing"}

        df["main_net_inflow"] = pd.to_numeric(df["main_net_inflow"], errors="coerce")
        usable = df[df["main_net_inflow"].notna()].copy()

        # 未指定口径时，从“真正有资金流数值”的来源中选可用样本最多、最新的一源；
        # 禁止用最早价格行的 source 把全空资金列误判成有效低分。
        if metric_source is None and "metric_source" in usable.columns and len(usable) > 0:
            candidates = []
            for source, group in usable.groupby("metric_source", dropna=False):
                latest = group["timestamp"].max() if "timestamp" in group.columns else len(group)
                candidates.append((len(group), latest, source))
            metric_source = max(candidates, key=lambda item: (item[0], item[1]))[2]
        if metric_source is not None and "metric_source" in usable.columns:
            usable = usable[usable["metric_source"] == metric_source]

        if len(usable) < MIN_FUND_FLOW_OBSERVATIONS:
            return {
                "available": False,
                "score": None,
                "consecutive_positive_days": 0,
                "inflow_strength": None,
                "divergence": False,
                "metric_source": metric_source,
                "usable_observations": int(len(usable)),
                "note": f"insufficient usable flow observations (<{MIN_FUND_FLOW_OBSERVATIONS})",
            }

        usable = usable.sort_values("timestamp").reset_index(drop=True)

        net = pd.Series(usable["main_net_inflow"].astype("float64"))

        # 末端连续为正天数
        cons = 0
        # 若原始序列含完整 trading_date，则来源缺席某天必须中断“连续流入”。
        if "trading_date" in df.columns and "trading_date" in usable.columns:
            all_dates = list(pd.Series(df["trading_date"]).dropna().drop_duplicates().sort_values())
            values_by_date = {
                row["trading_date"]: row["main_net_inflow"]
                for _, row in usable.drop_duplicates("trading_date", keep="last").iterrows()
            }
            tail_values = [values_by_date.get(day) for day in reversed(all_dates)]
        else:
            tail_values = list(reversed(net.tolist()))
        for v in tail_values:
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
        if "amount" in usable.columns:
            amount = pd.Series(usable["amount"].astype("float64"))
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
        if "large_order_inflow" in usable.columns:
            lo = pd.Series(usable["large_order_inflow"].astype("float64"))
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
            "metric_source": metric_source,
            "usable_observations": int(len(usable)),
        }
