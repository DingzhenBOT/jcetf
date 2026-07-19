"""策略引擎（P3，DESIGN §9）。

StrategyEngine.evaluate_etf(session, mapping, version, as_of) -> Signal 形态字典（不含
signal_id/trading_date/target_etf/strategy_version，由 evaluation/pipeline 负责持久化与幂等）。

流程（每支生效映射）：
  1. market_score + market_regime：宽基指数 BAR + 全市场宽度（advance_ratio / 成交额放大）。
  2. sector_trend_score：关联板块 BAR（首个 related_sector_code）；缺失 -> None（D4 降级）。
  3. fund_flow_score：板块资金流 BAR，**仅同数据源**；缺失 -> None。
  4. etf_rs_score：ETF 相对关联指数/宽基的滚动 20 日 RS；缺失 -> None。
  5. composite = Σ wᵢ·scoreᵢ（缺失项权重重归一化，D4）。
  6. risk = RiskEngine.evaluate(...)：veto / downgrade / high_vol / chase_high。
  7. tier（决策优先级，§9 + D4）。
  8. confidence = 100 - 缺失项惩罚（缺数据降级置信，但不自动否决，除非 BEAR+缺失）。

纯函数 compute_composite / decide_tier 暴露出来供单测以「强制分数」直接验证档位映射与降级逻辑。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models.market import MarketBreadth
from app.indicator_engine.engine import IndicatorEngine
from app.market_calendar import beijing_to_utc, next_trading_day
from app.opinion_engine.templates import TIER_TEXT
from app.repository import quote_repo
from app.risk_engine.engine import RiskEngine
from app.sector_engine.engine import SectorEngine

# 档位 -> 数值仓位区间 [low, high]（DESIGN §9.6，前端文字化展示）
POSITION_RANGE: Dict[str, List[float]] = {
    "NO_PARTICIPATE": [0, 0],
    "OBSERVE": [0, 10],
    "SMALL_POSITION": [10, 25],
    "OPPORTUNITY_ENHANCE": [25, 50],
    "NO_CHASE_HIGH": [0, 0],
    "MARKET_RISK_HIGH": [0, 0],
}

# 每缺失一个评分组件的置信惩罚
MISSING_PENALTY = 15


def _to_df(rows: List[Any]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    data = [
        {
            "timestamp": r.timestamp,
            "trading_date": r.trading_date,
            "open": r.open,
            "high": r.high,
            "low": r.low,
            "close": r.close,
            "volume": r.volume,
            "amount": r.amount,
            "change_percent": r.change_percent,
            "main_net_inflow": r.main_net_inflow,
            "large_order_inflow": r.large_order_inflow,
            "metric_source": r.metric_source,
        }
        for r in rows
    ]
    return pd.DataFrame(data).sort_values("timestamp").reset_index(drop=True)


def compute_composite(
    scores: Dict[str, Optional[float]],
    weights: Dict[str, float],
    missing_penalty: int = MISSING_PENALTY,
) -> Dict[str, Any]:
    """权重重归一化合成（D4：缺失组件权重分摊给可用组件）。

    返回 {composite, available, missing, confidence}。composite=None 表示无可用组件。
    """
    available = {k: v for k, v in scores.items() if v is not None}
    missing = [k for k in weights if k not in available]
    total_w = sum(weights[k] for k in available)
    if not available or total_w <= 0:
        composite = None
    else:
        norm = {k: weights[k] / total_w for k in available}
        composite = sum(norm[k] * available[k] for k in available)
    confidence = max(0, 100 - len(missing) * missing_penalty)
    return {
        "composite": composite,
        "available": available,
        "missing": missing,
        "confidence": confidence,
    }


def decide_tier(
    composite: Optional[float],
    market_regime: Optional[str],
    risk: Dict[str, Any],
    fund_flow: Optional[Dict[str, Any]],
    etf_rs: Optional[Dict[str, Any]],
    thresholds: Dict[str, Any],
) -> str:
    """档位决策（§9 + D4 决策优先级）。

    1) risk.veto -> NO_PARTICIPATE
    2) risk.chase_high -> NO_CHASE_HIGH
    3) market_regime∈{WEAK,BEAR} 或 high_vol -> MARKET_RISK_HIGH
    4) 否则按 composite（命中降级则先下调一档）映射到 OPPORTUNITY_ENHANCE / SMALL_POSITION / OBSERVE / NO_PARTICIPATE
    """
    if risk.get("veto"):
        return "NO_PARTICIPATE"
    if risk.get("chase_high"):
        return "NO_CHASE_HIGH"
    if market_regime in ("WEAK", "BEAR") or risk.get("high_vol"):
        return "MARKET_RISK_HIGH"

    c = composite if composite is not None else 0.0
    if risk.get("downgrade"):
        c = max(0.0, c - 15)

    opp = float(thresholds.get("opportunity_enhance", 85))
    small = float(thresholds.get("small_position", 75))
    obs = float(thresholds.get("join_observe", 60))

    fund_flow_strong = (
        fund_flow is not None
        and fund_flow.get("score") is not None
        and fund_flow["score"] >= 70
        and fund_flow.get("consecutive_positive_days", 0) >= 3
    )
    etf_rs_strong = (
        etf_rs is not None
        and etf_rs.get("score") is not None
        and etf_rs["score"] >= 60
    )

    if c >= opp and fund_flow_strong and etf_rs_strong:
        return "OPPORTUNITY_ENHANCE"
    if c >= small:
        return "SMALL_POSITION"
    if c >= obs:
        return "OBSERVE"
    return "NO_PARTICIPATE"


class StrategyEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.ind = IndicatorEngine()
        self.sector = SectorEngine()
        self.risk = RiskEngine(settings.strategy.risk_filter)

    # ---- 内部：窗口边界 ----
    def _window(self, as_of: date):
        lookback = self.settings.backfill.lookback_days
        start = as_of - timedelta(days=lookback)
        return start, as_of

    # ---- 内部：市场环境 ----
    def _evaluate_market(self, session: Session, as_of: date):
        """返回 (market_score, market_regime, advance_ratio, index_available, breadth_available)。"""
        start, end = self._window(as_of)
        indices = self.settings.strategy.broad_index_codes
        index_indicators: List[Dict] = []
        for code in indices:
            rows = quote_repo.get_bar_history(session, "INDEX", code, start, end)
            df = _to_df(rows)
            if len(df) == 0:
                continue
            index_indicators.append(self.ind.compute(df))

        # 宽度（advance_ratio / 成交额放大）
        breadth = quote_repo.get_breadth_on_date(session, as_of)
        advance_ratio = None
        amount_ratio = None
        if breadth is not None and breadth.total_rise and breadth.total_fall is not None:
            denom = (breadth.total_rise or 0) + (breadth.total_fall or 0)
            if denom > 0:
                advance_ratio = breadth.total_rise / denom
        if breadth is not None and breadth.total_amount:
            # 近 5 个交易日宽度成交额均值（不含今日）
            recent = (
                session.execute(
                    select(MarketBreadth.total_amount)
                    .where(MarketBreadth.trading_date <= as_of)
                    .order_by(MarketBreadth.trading_date.desc())
                    .limit(6)
                ).scalars().all()
            )
            vals = [v for v in recent if v]
            if len(vals) >= 2:
                avg = sum(vals[1:]) / max(1, len(vals) - 1)  # 排除今日
                if avg > 0:
                    amount_ratio = breadth.total_amount / avg

        # market_score 起点 50
        score = 50.0
        regime = "VOLATILE"
        idx_avail = len(index_indicators) > 0
        if idx_avail:
            prim = index_indicators[0]
            # 取 primary close 判 above_ma20（compute 未返回 close）
            rows = quote_repo.get_bar_history(session, "INDEX", indices[0], start, end)
            pdf = _to_df(rows)
            if len(pdf) > 0:
                last_close = float(pdf["close"].iloc[-1])
                ma20 = prim.get("ma20")
                slope = prim.get("ma20_slope")
                if ma20 is not None and last_close > ma20:
                    score += 35
                if slope is not None and slope > 0:
                    score += 15
                # regime
                mom = prim.get("mom_20")
                above = ma20 is not None and last_close > ma20
                rising = slope is not None and slope > 0
                if above and rising and (advance_ratio or 0) > 0.6:
                    regime = "STRONG_UP"
                elif above and rising:
                    regime = "TREND_UP"
                elif (not above) and slope is not None and slope < 0:
                    regime = "WEAK"
                else:
                    regime = "VOLATILE"
                if (
                    (not above)
                    and slope is not None
                    and slope < 0
                    and advance_ratio is not None
                    and advance_ratio < 0.4
                ):
                    regime = "BEAR"

        if advance_ratio is not None:
            if advance_ratio > 0.60:
                score += 15
            elif advance_ratio < 0.40:
                score += -15
            elif advance_ratio > 0.55:
                score += 5
            elif advance_ratio < 0.45:
                score += -5
        if amount_ratio is not None:
            if amount_ratio > 1.1:
                score += 10
            elif amount_ratio < 0.9:
                score += -5

        score = max(0.0, min(100.0, score))
        breadth_avail = breadth is not None
        return score, regime, advance_ratio, idx_avail, breadth_avail

    # ---- 主入口 ----
    def evaluate_etf(
        self, session: Session, mapping, version: str, as_of: date
    ) -> Dict[str, Any]:
        start, end = self._window(as_of)

        # 1) 市场环境
        market_score, regime, advance_ratio, idx_avail, breadth_avail = self._evaluate_market(session, as_of)

        # 2) ETF 技术
        etf_rows = quote_repo.get_bar_history(session, "ETF", mapping.etf_code, start, end)
        etf_df = _to_df(etf_rows)
        benchmark_close = None
        if mapping.related_index_code:
            idx_rows = quote_repo.get_bar_history(session, "INDEX", mapping.related_index_code, start, end)
            idf = _to_df(idx_rows)
            if len(idf) > 0:
                benchmark_close = list(idf["close"].astype("float64"))
        etf_ind = self.ind.compute(etf_df, benchmark_close) if len(etf_df) > 0 else {}

        # 3) 板块趋势
        sector_trend = None
        sector_code = None
        if mapping.related_sector_codes:
            sector_code = mapping.related_sector_codes[0]
            s_rows = quote_repo.get_bar_history(session, "SECTOR", sector_code, start, end)
            sector_trend = self.sector.evaluate_sector_trend(_to_df(s_rows))

        # 4) 资金持续性（仅同源）
        fund_flow = None
        if sector_code is not None:
            f_rows = quote_repo.get_bar_history(session, "SECTOR", sector_code, start, end)
            f_df = _to_df(f_rows)
            metric_source = f_df["metric_source"].iloc[0] if len(f_df) > 0 else None
            fund_flow = self.sector.evaluate_fund_flow(f_df, metric_source)

        # 5) ETF 相对强弱评分
        etf_rs_score = None
        if etf_ind.get("rs_20d") is not None:
            etf_rs_score = max(0.0, min(100.0, 50 + (etf_ind["rs_20d"] - 1) * 100))

        # 6) 合成（缺失项重归一化，D4）
        scores = {
            "market": market_score,
            "sector_trend": sector_trend["score"] if sector_trend and sector_trend.get("available") else None,
            "fund_flow": fund_flow["score"] if fund_flow and fund_flow.get("available") else None,
            "etf_rs": etf_rs_score,
        }
        comp = compute_composite(scores, self.settings.strategy.composite_weights)

        # 7) 风险
        drawdown_pct = None
        if len(etf_df) > 0 and etf_df["close"].notna().any():
            closes = etf_df["close"].astype("float64").dropna()
            if len(closes) > 0:
                max_close = closes.max()
                last_close = float(closes.iloc[-1])
                if max_close > 0:
                    drawdown_pct = (last_close / max_close - 1) * 100
        sector_surge = bool(
            (sector_trend and sector_trend.get("risk_overheat"))
            or (etf_ind.get("mom_5") is not None and etf_ind["mom_5"] > 0.15)
        )
        missing_data = (not idx_avail) or (not breadth_avail)
        risk = self.risk.evaluate(
            {
                "rsi14": etf_ind.get("rsi14"),
                "sector_surge": sector_surge,
                "market_regime": regime,
                "drawdown_pct": drawdown_pct,
                "atr_pct": etf_ind.get("atr_pct"),
                "missing_data": missing_data,
            }
        )

        # 8) 档位
        tier = decide_tier(
            comp["composite"],
            regime,
            risk,
            fund_flow if fund_flow and fund_flow.get("available") else None,
            {"score": etf_rs_score} if etf_rs_score is not None else None,
            self.settings.strategy.thresholds,
        )

        # 支持指标 / 触发与失败规则
        supporting = {
            "etf_rsi14": etf_ind.get("rsi14"),
            "etf_rs_20d": etf_ind.get("rs_20d"),
            "etf_ma20_slope": etf_ind.get("ma20_slope"),
            "etf_atr_pct": etf_ind.get("atr_pct"),
            "etf_vol_ratio": etf_ind.get("vol_ratio"),
            "sector_score": sector_trend["score"] if sector_trend and sector_trend.get("available") else None,
            "fund_flow_score": fund_flow["score"] if fund_flow and fund_flow.get("available") else None,
            "advance_ratio": advance_ratio,
            "market_regime": regime,
        }
        triggered: List[str] = []
        failed: List[str] = []
        if idx_avail:
            triggered.append("market_index_available")
        else:
            failed.append("broad_index_missing")
        if breadth_avail:
            triggered.append("breadth_available")
        else:
            failed.append("breadth_missing")
        if sector_trend and sector_trend.get("available"):
            triggered.append("sector_trend_available")
        else:
            failed.append("sector_data_missing")
        if fund_flow and fund_flow.get("available"):
            triggered.append("fund_flow_available")
        else:
            failed.append("fund_flow_missing")
        if etf_rs_score is not None:
            triggered.append("etf_rs_available")
        else:
            failed.append("etf_rs_missing")

        # 9) 复核时间：下一交易日的盘前 08:50（北京）-> UTC
        next_day = next_trading_day(as_of + timedelta(days=1))
        review_time = beijing_to_utc(datetime(next_day.year, next_day.month, next_day.day, 8, 50))

        invalidation = {
            "close_below_ma20": bool(
                etf_ind.get("ma20") is not None
                and len(etf_df) > 0
                and float(etf_df["close"].iloc[-1]) < etf_ind["ma20"]
            ),
            "market_regime_bear": regime == "BEAR",
            "rsi_overheat_gt_80": bool(etf_ind.get("rsi14") is not None and etf_ind["rsi14"] > 80),
            "data_incomplete": len(comp["missing"]) > 0,
        }

        return {
            "signal_type": tier,
            "score": comp["composite"],
            "confidence": comp["confidence"],
            "market_regime": regime,
            "triggered_rules": triggered,
            "failed_rules": failed,
            "supporting_metrics": supporting,
            "risk_flags": risk,
            "invalidation_conditions": invalidation,
            "suggested_action": TIER_TEXT.get(tier, tier),
            "suggested_position_range": POSITION_RANGE.get(tier, [0, 0]),
            "review_time": review_time,
            # 便于前端/排查的附加信息（不入库 Signal，但 opinion 可用）
            "_missing": comp["missing"],
        }
