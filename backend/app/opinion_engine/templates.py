"""意见模板（P3，template-v1，确定性）。

- TIER_TEXT：signal_type 英文档位码 -> 中文展示（D2）。
- POSITION_TEXT：仓位数值区间 -> 前端文字（DESIGN §9.6 先文字为主）。
- TEMPLATE_V1：固定占位符模板；OpinionEngine 仅做字符串填充，不改数值。
"""
from __future__ import annotations

from typing import Dict, List

from app.indicator_engine.ta_volume_price import VP_PATTERN_TEXT

# signal_type（英文档位码，D2） -> 中文展示（直白口语，避免术语）
TIER_TEXT: Dict[str, str] = {
    "NO_PARTICIPATE": "先别碰",
    "OBSERVE": "加入观察",
    "SMALL_POSITION": "小仓位试一试",
    "OPPORTUNITY_ENHANCE": "可以加仓",
    "NO_CHASE_HIGH": "别追高",
    "MARKET_RISK_HIGH": "市场风险大，先观望",
}

# suggested_position_range（数值 [low, high]） -> 文字（DESIGN §9.6，直白）
# 文字本身不含数字区间；区间由 position_text_of 统一追加（避免重复）。
POSITION_TEXT: Dict[str, str] = {
    "NO_PARTICIPATE": "不加仓",
    "OBSERVE": "轻仓试错",
    "SMALL_POSITION": "低仓位持有",
    "OPPORTUNITY_ENHANCE": "可以适度加仓",
    "NO_CHASE_HIGH": "别再加，等回调",
    "MARKET_RISK_HIGH": "减仓观望",
}

TEMPLATE_V1: str = (
    "{etf}｜{tier_text}（综合 {score} 分 / 置信 {confidence}%）。"
    "市场环境：{market_regime}。{key_metrics}"
    "建议仓位：{position_text}。下次复核：{review_time}。"
)

TEMPLATE_VERSION = "template-v1"


def position_text_of(tier: str, position_range: List[float] | None = None) -> str:
    base = POSITION_TEXT.get(tier, "不加仓")
    if position_range and len(position_range) == 2:
        low, high = position_range
        if not (low == 0 and high == 0):
            return f"{base}（{low:.0f}-{high:.0f}%）"
    return base


def key_metrics_text(supporting: Dict) -> str:
    """把 supporting_metrics 中关键项拼成一句直白中文；确定性、不引入外部判断。

    量价关系最贴近「盘中该不该动」，故置前展示。
    """
    if not supporting:
        return "（数据不足，关键指标缺失）"
    parts: List[str] = []
    # 量价关系（最直白，置前）
    vp_text = supporting.get("vp_state_text")
    vp_vol = supporting.get("vp_vol_ratio_state")
    if vp_text and vp_text not in ("数据不足", "样本不足"):
        s = f"量价：{vp_text}"
        if vp_vol and vp_vol != "未知":
            s += f"（{vp_vol}）"
        parts.append(s)
    vp_patterns = supporting.get("vp_patterns") or []
    if vp_patterns:
        names = [VP_PATTERN_TEXT.get(p, p) for p in vp_patterns]
        parts.append("量价信号：" + "、".join(names))
    # 原有指标
    if supporting.get("etf_rsi14") is not None:
        parts.append(f"RSI {supporting['etf_rsi14']:.0f}")
    if supporting.get("etf_rs_20d") is not None:
        rs = supporting["etf_rs_20d"]
        parts.append(f"20日相对强弱 {rs:.2f}")
    if supporting.get("sector_score") is not None:
        parts.append(f"板块趋势 {supporting['sector_score']:.0f}")
    if supporting.get("fund_flow_score") is not None:
        parts.append(f"资金持续性 {supporting['fund_flow_score']:.0f}")
    if supporting.get("advance_ratio") is not None:
        parts.append(f"上涨家数占比 {supporting['advance_ratio']*100:.0f}%")
    if not parts:
        return "（暂无可用指标）"
    return "关键指标：" + "；".join(parts) + "。"
