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
  7. tier（决策优先级，§9 + D4；方案B 量价形态增强为 additive 上调一档）。
  8. confidence = 100 - 缺失项惩罚（缺数据降级置信，但不自动否决，除非 BEAR+缺失）。
  9. 方案B：量价关系技术分析（analyze_volume_price）作为 additive 触发规则写入
     supporting_metrics / triggered_rules，不改变 composite 权重。

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
from app.indicator_engine.ta_volume_price import analyze_volume_price, VP_PATTERN_TEXT
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


# 盘中动量加性修正（P1 修复核心：让综合分随实时行情移动）
INTRADAY_ADJ_MAX = 18.0          # 综合分加性修正上下限
INTRADAY_ADJ_PER_VOL = 12.0     # 每单位日波动率 -> 综合分修正
INTRADAY_VOL_FLOOR = 0.1        # 日波动率下限（%），低于则回退
INTRADAY_VOL_FALLBACK = 1.5      # 无波动率估计时回退的日波动率（%）


def intraday_momentum_adjustment(
    change_pct: Optional[float],
    daily_vol_pct: Optional[float],
) -> Optional[float]:
    """盘中动量加性修正（P1 修复核心：让综合分随实时行情移动）。

    change_pct：ETF 当日涨跌幅（%）。None 表示无数据（盘前/非交易时段/历史回填）-> 返回 None（不加修正）。
    daily_vol_pct：近窗口日收益率标准差（%），作归一化尺度；过小或缺失回退 INTRADAY_VOL_FALLBACK。
    将 change_pct 归一到「日波动率 z 值」(change/vol)，再映射到 [-INTRADAY_ADJ_MAX, +INTRADAY_ADJ_MAX]。

    设计为 additive（与方案B 量价增强一致）：不改变 composite 权重与缺失/置信逻辑，仅平移综合分，
    使档位在盘中随行情上浮/下调。
    """
    if change_pct is None:
        return None
    vol = daily_vol_pct if (daily_vol_pct is not None and daily_vol_pct > INTRADAY_VOL_FLOOR) else INTRADAY_VOL_FALLBACK
    z = change_pct / vol
    return max(-INTRADAY_ADJ_MAX, min(INTRADAY_ADJ_MAX, z * INTRADAY_ADJ_PER_VOL))


def _daily_vol_pct(df: pd.DataFrame) -> Optional[float]:
    """ETF 日线收盘价收益率标准差（%），作盘中动量修正的尺度。样本不足返回 None。"""
    if df is None or len(df) < 3 or "close" not in df:
        return None
    rets = df["close"].astype("float64").pct_change().dropna()
    if len(rets) == 0:
        return None
    return float(rets.std() * 100.0)


def _bar_daily_return(df: pd.DataFrame, as_of: date) -> Optional[float]:
    """as_of 当日 BAR 收盘相对前一交易日的收益率（%），用于实时快照缺失时的回退。

    找不到 as_of 行或前一行时返回 None。仅作为「当日实时」路径的兜底（as_of==今日但 SNAPSHOT 尚未采集）。
    """
    if df is None or len(df) == 0 or "trading_date" not in df or "close" not in df:
        return None
    sub = df[df["trading_date"] == as_of]
    if len(sub) == 0:
        return None
    idx = int(df.index.get_loc(sub.index[0]))
    if idx == 0:
        return None
    prev = float(df["close"].iloc[idx - 1])
    cur = float(df["close"].iloc[idx])
    if prev <= 0:
        return None
    return (cur / prev - 1) * 100.0


def _vp_bearish(vp: Optional[Dict[str, Any]]) -> bool:
    """看空量价形态（方案B+ 降档用，确定性）。

    命中以下任一明确看空形态即返回 True：
    - divergence：价创 20 日新高但量比<1（价升量缩，反弹无力）；
    - anomaly 且下跌方向：异动放量（量比>2.5 或单日涨跌>5%）出现在放量/缩量下跌或横盘；
    - VOL_UP_FALL：放量下跌（出货）。
    仅识别明确看空形态，避免对中性/上涨异动误降档。
    """
    if not vp:
        return False
    patterns = set(vp.get("vp_patterns", []) or [])
    state = vp.get("vp_state")
    if "divergence" in patterns:
        return True
    if "anomaly" in patterns:
        # 异动放量仅在下跌方向看空（出货/大阴线）；上涨方向偏多，不降档
        return state in ("VOL_UP_FALL", "VOL_DOWN_FALL", "VOL_LOW_FLAT")
    if state == "VOL_UP_FALL":
        return True
    return False


def decide_tier(
    composite: Optional[float],
    market_regime: Optional[str],
    risk: Dict[str, Any],
    fund_flow: Optional[Dict[str, Any]],
    etf_rs: Optional[Dict[str, Any]],
    thresholds: Dict[str, Any],
    vp: Optional[Dict[str, Any]] = None,
) -> str:
    """档位决策（§9 + D4 决策优先级，C23 修正：市场弱为降档修正而非硬闸门）。

    1) risk.veto -> NO_PARTICIPATE（仅「大盘 BEAR 且 数据缺失」）
    2) risk.chase_high -> NO_CHASE_HIGH
    3) 市场弱/高波动 -> 综合分降档修正 + 记 caution，不再 blanket MARKET_RISK_HIGH：
       - WEAK：综合分 -10；BEAR：综合分 -18；high_vol：综合分 -5（可叠加）。
       目的：弱势市场下，强势个股仍可给出 SMALL_POSITION / OBSERVE（带控仓提示），
       而非全市场一律「先观望」，丢弃个股层面的 RSI / 相对强弱 / 板块 / 资金流 / 量价分析。
    4) 否则按 composite（命中降级则先下调一档）映射到 OPPORTUNITY_ENHANCE / SMALL_POSITION / OBSERVE / NO_PARTICIPATE
    5) 方案B：量价形态增强（扩展而非覆盖原权重）——仅在量价强势突破 + 相对强弱确认时上调一档。
       vp=None 时退化为原逻辑，保持历史测试不变。

    注：MARKET_RISK_HIGH 档位枚举保留（向后兼容模板），但 C23 起不再由本函数产出；
    caution 通过 supporting_metrics 的 market_caution / high_vol_caution 透传给前端提示。
    """
    if risk.get("veto"):
        return "NO_PARTICIPATE"
    if risk.get("chase_high"):
        return "NO_CHASE_HIGH"

    c = composite if composite is not None else 0.0
    # C23：市场弱 / 高波动 -> 降档修正（不再一票否决 MARKET_RISK_HIGH）
    if market_regime == "BEAR":
        c = max(0.0, c - 18)
    elif market_regime == "WEAK":
        c = max(0.0, c - 10)
    if risk.get("high_vol"):
        c = max(0.0, c - 5)

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
        base = "OPPORTUNITY_ENHANCE"
    elif c >= small:
        base = "SMALL_POSITION"
    elif c >= obs:
        base = "OBSERVE"
    else:
        base = "NO_PARTICIPATE"

    # 方案B+：量价形态驱动档位（扩展而非覆盖原权重）
    # 看空形态优先降档（与上调互斥，保护本金）；否则强势突破上调一档
    if vp:
        if _vp_bearish(vp) and base != "NO_PARTICIPATE":
            _down = {
                "OPPORTUNITY_ENHANCE": "SMALL_POSITION",
                "SMALL_POSITION": "OBSERVE",
                "OBSERVE": "NO_PARTICIPATE",
            }
            return _down.get(base, base)
        if not risk.get("downgrade"):
            bullish = set(vp.get("vp_patterns", []))
            strong_breakout = "breakout_volume" in bullish or "segment_up" in bullish
            if strong_breakout and etf_rs_strong:
                if base == "SMALL_POSITION":
                    return "OPPORTUNITY_ENHANCE"
                if base == "OBSERVE":
                    return "SMALL_POSITION"
    return base


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

    # ---- 内部：盘中切片/进度（C23） ----
    def _morning_slice(self, df: "pd.DataFrame") -> "pd.DataFrame":
        """取上午时段（北京 <= 11:30）的 1m BAR，用于午盘意见。"""
        if df is None or len(df) == 0 or "timestamp" not in df:
            return df
        bj = df["timestamp"] + pd.Timedelta(hours=8)
        mask = (bj.dt.hour < 11) | ((bj.dt.hour == 11) & (bj.dt.minute <= 30))
        return df[mask].reset_index(drop=True)

    def _session_progress(self, df: "pd.DataFrame") -> float:
        """当前交易进度 0-1（北京 9:30-11:30 + 13:00-15:00，共 240 分钟），用于量能折算。"""
        if df is None or len(df) == 0:
            return 0.5
        bj = df["timestamp"].iloc[-1] + pd.Timedelta(hours=8)
        minutes = int(bj.hour) * 60 + int(bj.minute)
        if minutes <= 11 * 60 + 30:
            elapsed = max(0, minutes - (9 * 60 + 30))
        elif minutes >= 13 * 60:
            elapsed = 120 + (minutes - 13 * 60)
        else:
            elapsed = 120  # 午休
        return max(0.05, min(1.0, elapsed / 240.0))

    # ---- 内部：市场环境 ----
    def _evaluate_market(self, session: Session, as_of: date, phase: Optional[str] = None):
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
        # 盘中阶段：用实时 INDEX 1m 分时修正 regime，避免陈旧日线（昨日收盘）把盘中市场误判为 WEAK/BEAR，
        # 进而经 decide_tier 强制 MARKET_RISK_HIGH，导致盘中建议「全偏弱」。仅当指数当日上涨/平盘时抬升
        # regime；当日走弱则保持日线 regime（不强行乐观）。post_close 阶段（含今日已收盘日线）不改。
        if phase and phase != "post_close":
            live_regime = self._intraday_regime(session, as_of, indices)
            if live_regime is not None:
                regime = live_regime
        return score, regime, advance_ratio, idx_avail, breadth_avail

    def _intraday_regime(
        self, session: Session, as_of: date, indices: List[str]
    ) -> Optional[str]:
        """盘中市场环境（实时）：用宽基指数当日 1m 分时估算涨跌幅，映射到 regime。

        返回 None 表示无可用实时分时（退化为日线 regime）；仅当指数当日上涨/平盘时返回
        VOLATILE/TREND_UP（抬升可能被陈旧日线压低的 regime）；当日走弱返回 None（保持日线 WEAK/BEAR）。
        """
        for code in indices:
            rows = quote_repo.get_bar_history(
                session, "INDEX", code, as_of, as_of, timeframe="1m", data_kind="BAR"
            )
            if not rows:
                continue
            closes = [float(r.close) for r in rows if r.close is not None]
            if len(closes) < 2:
                continue
            first, last = closes[0], closes[-1]
            if first == 0:
                continue
            chg = (last / first - 1) * 100
            if chg <= -0.1:
                return None  # 当日走弱，保持日线 regime，不强行乐观
            if chg >= 0.5:
                return "TREND_UP"
            return "VOLATILE"
        return None

    # ---- 主入口 ----
    def evaluate_etf(
        self, session: Session, mapping, version: str, as_of: date, phase: Optional[str] = None
    ) -> Dict[str, Any]:
        start, end = self._window(as_of)

        # 1) 市场环境（盘中阶段透传 phase，用实时指数修正 regime）
        market_score, regime, advance_ratio, idx_avail, breadth_avail = self._evaluate_market(session, as_of, phase=phase)

        # 2) ETF 技术（场外基金 listing='场外' 读 OFF_FUND 净值序列，与场内 ETF BAR 隔离）
        # 用 getattr 兜底：mapping 可能是缺 listing 属性的测试替身；真实 ORM 行有该列（None 默认"场内"）。
        bar_type = "OFF_FUND" if (getattr(mapping, "listing", None) or "场内") == "场外" else "ETF"
        etf_rows = quote_repo.get_bar_history(session, bar_type, mapping.etf_code, start, end)
        etf_df = _to_df(etf_rows)
        benchmark_close = None
        if mapping.related_index_code:
            idx_rows = quote_repo.get_bar_history(session, "INDEX", mapping.related_index_code, start, end)
            idf = _to_df(idx_rows)
            if len(idf) > 0:
                benchmark_close = idf[["trading_date", "close"]]
        # 兜底：跟踪指数（related_index_code）未回填时，用宽基指数（如 000300，回填任务保证存在）
        # 作为 RS 基准，避免 etf_rs 因 related_index_code 缺失而误判 etf_rs_missing。
        if benchmark_close is None and self.settings.strategy.broad_index_codes:
            fb_rows = quote_repo.get_bar_history(
                session, "INDEX", self.settings.strategy.broad_index_codes[0], start, end
            )
            fdf = _to_df(fb_rows)
            if len(fdf) > 0:
                benchmark_close = fdf[["trading_date", "close"]]
        etf_ind = self.ind.compute(etf_df, benchmark_close) if len(etf_df) > 0 else {}
        # 方案B：量价关系技术分析（确定性，additive）
        vp = analyze_volume_price(etf_df) if len(etf_df) > 0 else {}

        # 3) 板块趋势
        sector_trend = None
        sector_code = None
        # 宽基 ETF 无关联板块（related_sector_codes 为空）：板块/资金流属「不适用」而非「数据缺失」，
        # 不应计入缺失项、不应扣置信度（见下方权重裁剪与 failed_rules 门控）。
        has_sector = bool(mapping.related_sector_codes)
        if has_sector:
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

        # 5.5) C23：盘中实时强度（live/lunch 用当日 1m BAR 算强度 + R1/R2）；收盘后三档价位（post_close）
        intraday_strength_info = None
        r1r2 = None
        trade_plan = None
        if phase in ("live", "lunch") and len(etf_df) > 0:
            from app.opinion_engine.intraday_strength import intraday_strength, check_r1_r2

            etf_1m_rows = quote_repo.get_bar_history(
                session, "ETF", mapping.etf_code, as_of, as_of, timeframe="1m", data_kind="BAR"
            )
            etf_1m = _to_df(etf_1m_rows)
            if len(etf_1m) >= 2:
                if phase == "lunch":
                    etf_1m = self._morning_slice(etf_1m)
                idx_1m = None
                if self.settings.strategy.broad_index_codes:
                    idx_rows = quote_repo.get_bar_history(
                        session, "INDEX", self.settings.strategy.broad_index_codes[0],
                        as_of, as_of, timeframe="1m", data_kind="BAR",
                    )
                    idx_1m = _to_df(idx_rows)
                daily_avg_vol = float(etf_df["volume"].astype("float64").mean()) if len(etf_df) else None
                progress = self._session_progress(etf_1m)
                ff_score = fund_flow["score"] if (fund_flow and fund_flow.get("available")) else None
                intraday_strength_info = intraday_strength(
                    etf_1m, idx_1m,
                    daily_avg_volume=daily_avg_vol,
                    trading_progress=progress,
                    sector_flow_score=ff_score,
                )
                r1r2 = check_r1_r2(etf_df, etf_ind, fund_flow)
        if phase == "post_close" and len(etf_df) > 0:
            from app.opinion_engine.levels import compute_trade_plan

            trade_plan = compute_trade_plan(etf_df, etf_ind)

        # 6) 合成（缺失项重归一化，D4）
        # 宽基 ETF 无板块：从权重中移除 sector_trend/fund_flow，使其不被计入「缺失」、不误扣置信度；
        # 有板块的 ETF 仍按完整权重计算，数据真缺失时才降级。
        weights = dict(self.settings.strategy.composite_weights)
        if not has_sector:
            weights.pop("sector_trend", None)
            weights.pop("fund_flow", None)
        scores = {"market": market_score}
        if sector_trend is not None and sector_trend.get("available"):
            scores["sector_trend"] = sector_trend["score"]
        if fund_flow is not None and fund_flow.get("available"):
            scores["fund_flow"] = fund_flow["score"]
        if etf_rs_score is not None:
            scores["etf_rs"] = etf_rs_score
        comp = compute_composite(scores, weights)

        # 6.5) 盘中动量修正（P1）：让综合分随实时行情移动
        # 仅「当日实时」路径生效（as_of==今日 且存在 SNAPSHOT）；历史回填不改分（避免与 mom/rs 双重计入）。
        composite_final = comp["composite"]
        intraday_change = None
        if as_of >= date.today():
            snap = quote_repo.get_latest_snapshot_change_map(session, "ETF", [mapping.etf_code])
            live_cp = snap.get(mapping.etf_code)
            intraday_change = live_cp if live_cp is not None else _bar_daily_return(etf_df, as_of)
        daily_vol = _daily_vol_pct(etf_df)
        intraday_adj = intraday_momentum_adjustment(intraday_change, daily_vol)
        if composite_final is not None and intraday_adj is not None:
            composite_final = max(0.0, min(100.0, composite_final + intraday_adj))

        # C23：盘中强度融入综合分（live 主导、lunch 较轻；post_close 不混入，保持日线语境）
        if intraday_strength_info is not None and intraday_strength_info.get("score") is not None:
            w = 0.35 if phase == "live" else 0.20
            c_base = composite_final if composite_final is not None else 50.0
            composite_final = max(0.0, min(100.0, (1 - w) * c_base + w * intraday_strength_info["score"]))

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
            composite_final,
            regime,
            risk,
            fund_flow if fund_flow and fund_flow.get("available") else None,
            {"score": etf_rs_score} if etf_rs_score is not None else None,
            self.settings.strategy.thresholds,
            vp if vp else None,
        )
        # 方案B+：量价看空降档标记（供 one_liner / 审计；仅当确实改变档位时记）
        vp_downgraded = False
        if vp and _vp_bearish(vp):
            tier_base = decide_tier(
                composite_final, regime, risk,
                fund_flow if fund_flow and fund_flow.get("available") else None,
                {"score": etf_rs_score} if etf_rs_score is not None else None,
                self.settings.strategy.thresholds, None,
            )
            vp_downgraded = tier != tier_base

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
            # C23：市场弱/高波动降档提示（不再一票否决，前端据此显示「控仓」而非 blanket 观望）
            "market_caution": regime in ("WEAK", "BEAR"),
            "high_vol_caution": bool(risk.get("high_vol")),
            # P1 盘中动量修正（随实时行情变化）
            "intraday_change_percent": intraday_change,
            "intraday_adjust": intraday_adj,
            "daily_vol_pct": daily_vol,
            # 方案B：量价关系技术分析
            "vp_state": vp.get("vp_state"),
            "vp_state_text": vp.get("vp_state_text"),
            "vp_vol_ratio_state": vp.get("vp_vol_ratio_state"),
            "vp_vol_ratio_ma20": vp.get("vp_vol_ratio_ma20"),
            "vp_patterns": vp.get("vp_patterns"),
            "vp_strength": vp.get("vp_strength"),
            "vp_anomaly": vp.get("vp_anomaly"),
            # C23：盘中实时强度 / R1/R2 触发（live/lunch 相位）
            "intraday_strength": intraday_strength_info.get("score") if intraday_strength_info else None,
            "intraday_lean": intraday_strength_info.get("lean") if intraday_strength_info else None,
            "intraday_factors": intraday_strength_info.get("factors") if intraday_strength_info else None,
            "r1_signal": bool(r1r2.get("r1")) if r1r2 else False,
            "r2_signal": bool(r1r2.get("r2")) if r1r2 else False,
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
        elif has_sector:
            failed.append("sector_data_missing")
        if fund_flow and fund_flow.get("available"):
            triggered.append("fund_flow_available")
        elif has_sector:
            failed.append("fund_flow_missing")
        if etf_rs_score is not None:
            triggered.append("etf_rs_available")
        else:
            failed.append("etf_rs_missing")
        # P1 盘中动量修正触发规则（change 不为 0 时才记，避免噪音）
        if intraday_adj is not None and intraday_adj != 0:
            triggered.append("intraday_momentum_up" if intraday_adj > 0 else "intraday_momentum_down")
        # 方案B：量价形态作为 additive 触发规则（不改变评分权重）
        for p in vp.get("vp_patterns", []) or []:
            triggered.append("vp_" + p)
        if vp_downgraded:
            triggered.append("vp_downgrade")

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
            "score": composite_final,
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
            # C23：收盘后三档价位（突破/加仓/止损 + 明日预期），仅 post_close 有值
            "trade_plan": trade_plan,
            # 便于前端/排查的附加信息（不入库 Signal，但 opinion 可用）
            "_missing": comp["missing"],
        }
