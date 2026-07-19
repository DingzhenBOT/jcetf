"""持仓分析（P6，对齐 DESIGN § 按需持仓分析 / §9.5）。

设计铁律：
- 无状态、默认不落库：只读 SQLite，复用 signal_repo / quote_repo。
- 每条持仓 -> 确定性动作 HOLD / REDUCE / EXIT / RECONFIRM（§9.5）+ 盈亏（若有当前价）。
- 全部由规则引擎确定性填充，模板只拼中文不改数值（DESIGN §0 / §9.5）。
- 缺失数据 -> 降级而非崩溃（与 D4 一致）：当前价缺失则盈亏为 None。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.db.models.signal_opinion import Signal
from app.opinion_engine.templates import TIER_TEXT, position_text_of
from app.repository import quote_repo, signal_repo

# 服务端硬上限（DESIGN § 输入校验）
MAX_POSITIONS = 20
# 信号超过该天数视为 STALE -> 倾向于 RECONFIRM（仅数据新鲜度提示，非否决）
STALE_THRESHOLD_DAYS = 5


def _days_since(dt: Optional[datetime]) -> Optional[float]:
    """距现在的天数（naive UTC 对 naive UTC，匹配存储语义）。None 安全。"""
    if dt is None:
        return None
    return (datetime.utcnow() - dt).total_seconds() / 86400.0


def _invalidation_texts(inv: Optional[Dict[str, Any]]) -> List[str]:
    """Signal.invalidation_conditions（bool 字典）-> 中文触发项列表（仅 True 项，确定性）。"""
    if not inv:
        return []
    labels = {
        "close_below_ma20": "价格跌破 MA20",
        "market_regime_bear": "市场环境转为空头(BEAR)",
        "rsi_overheat_gt_80": "RSI 过热(>80)",
        "data_incomplete": "数据不完整，信号可靠性下降",
    }
    return [labels[k] for k, v in inv.items() if v]


def _rs_negative(signal: Optional[Signal]) -> bool:
    """ETF 相对强弱是否为负：supporting_metrics.etf_rs_20d < 1.0（跑输基准）。"""
    if signal is None or not signal.supporting_metrics:
        return False
    rs = signal.supporting_metrics.get("etf_rs_20d")
    return isinstance(rs, (int, float)) and rs < 1.0


def _decide_action(
    signal: Optional[Signal],
    prev_signal: Optional[Signal],
    rs_negative: bool,
) -> str:
    """§9.5 动作推导（确定性）。优先级 EXIT > REDUCE > RECONFIRM > HOLD。"""
    if signal is None:
        # 无信号：数据不全 -> 等待重新确认
        return "RECONFIRM"

    rf = signal.risk_flags or {}
    regime = signal.market_regime
    inv = signal.invalidation_conditions or {}

    # EXIT（触发退出条件）：硬否决 / 市场转空 / 相对强弱转负 / 跌破短期趋势线
    if rf.get("veto") or regime == "BEAR" or rs_negative or inv.get("close_below_ma20"):
        return "EXIT"

    # REDUCE（降低仓位）：降级但未否决 / 综合分下降 / 禁止追高档位
    score_drop = (
        prev_signal is not None
        and signal.score is not None
        and prev_signal.score is not None
        and (prev_signal.score - signal.score) >= 5
    )
    if rf.get("downgrade") or score_drop or signal.signal_type == "NO_CHASE_HIGH":
        return "REDUCE"

    # RECONFIRM（等待重新确认）：信号模糊 / 数据不完整 / 弱势市场 / 信号过期
    if (
        signal.signal_type in ("NO_PARTICIPATE", "OBSERVE")
        or (signal.failed_rules and len(signal.failed_rules) > 0)
        or regime == "WEAK"
        or (_days_since(signal.generated_at) or 0.0) > STALE_THRESHOLD_DAYS
    ):
        return "RECONFIRM"

    return "HOLD"


def _reason_risk(action: str, signal: Optional[Signal], rs_negative: bool) -> Tuple[str, str]:
    """基于动作与信号生成确定性中文 reason / risk（不改数值，只拼文案）。"""
    if signal is None:
        return "暂无该 ETF 的最新信号，无法给出持仓建议", "建议先补充信号数据后再评估"

    tier_text = TIER_TEXT.get(signal.signal_type, signal.signal_type)
    regime = signal.market_regime or "未知"
    rs_word = "为负" if rs_negative else "为正"
    reason_map = {
        "HOLD": f"市场环境{regime}，ETF相对强弱仍{rs_word}，当前公共建议为「{tier_text}」",
        "REDUCE": f"综合评分下降或触发降级条件，公共建议为「{tier_text}」",
        "EXIT": f"触发退出条件（市场环境{regime}或相对强弱{rs_word}），公共建议为「{tier_text}」",
        "RECONFIRM": f"信号模糊或数据不完整（公共建议「{tier_text}」），需等待重新确认",
    }
    risk_map = {
        "HOLD": "若价格跌破短期趋势线或市场环境转空需重新评估",
        "REDUCE": "若进一步跌破趋势线或市场转弱应加速降低仓位",
        "EXIT": "确认退出后避免抄底，等待下一轮信号",
        "RECONFIRM": "数据补全且信号稳定后再做决策",
    }
    return reason_map.get(action, ""), risk_map.get(action, "")


def analyze_position(pos: Dict[str, Any], session: Session) -> Dict[str, Any]:
    etf_code: str = pos["etf_code"]
    cost_price: float = float(pos["cost_price"])
    quantity = pos.get("quantity")

    signal = signal_repo.get_latest_signal_for_etf(session, etf_code)
    prev_list, _ = signal_repo.get_signal_history(session, etf_code=etf_code, limit=2)
    prev_signal = prev_list[1] if len(prev_list) > 1 else None

    rs_negative = _rs_negative(signal)
    action = _decide_action(signal, prev_signal, rs_negative)
    reason, risk = _reason_risk(action, signal, rs_negative)

    # 盈亏：需要当前价（ETF 最新 SNAPSHOT 的 close）。缺失则降级为 None。
    return_percent: Optional[float] = None
    pnl_amount: Optional[float] = None
    quote = quote_repo.get_latest_quote(session, "ETF", etf_code)
    if quote is not None and quote.close is not None:
        cur = float(quote.close)
        return_percent = (cur - cost_price) / cost_price * 100.0
        if quantity is not None:
            pnl_amount = float(quantity) * (cur - cost_price)

    suggested_range = None
    suggested_text = None
    review_time = None
    inv_texts: List[str] = []
    if signal is not None:
        suggested_range = signal.suggested_position_range  # List[float] | None
        suggested_text = position_text_of(signal.signal_type, suggested_range)
        inv_texts = _invalidation_texts(signal.invalidation_conditions)
        if signal.review_time is not None:
            review_time = signal.review_time.isoformat()

    return {
        "etf_code": etf_code,
        "action": action,
        "reason": reason,
        "risk": risk,
        "return_percent": None if return_percent is None else round(return_percent, 2),
        "pnl_amount": None if pnl_amount is None else round(pnl_amount, 2),
        "suggested_position_text": suggested_text,
        "suggested_position_range": suggested_range,
        "invalidation_conditions": inv_texts,
        "review_time": review_time,
    }


def analyze_portfolio(positions: List[Dict[str, Any]], session: Session) -> List[Dict[str, Any]]:
    """无状态批量分析。positions 为已通过基础类型校验的字典列表。"""
    return [analyze_position(p, session) for p in positions]
