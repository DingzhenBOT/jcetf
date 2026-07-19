"""意见模板（P3，template-v1，确定性）。

- TIER_TEXT：signal_type 英文档位码 -> 中文展示（D2）。
- POSITION_TEXT：仓位数值区间 -> 前端文字（DESIGN §9.6 先文字为主）。
- TEMPLATE_V1：固定占位符模板；OpinionEngine 仅做字符串填充，不改数值。
"""
from __future__ import annotations

from typing import Dict, List

# signal_type（英文档位码，D2） -> 中文展示
TIER_TEXT: Dict[str, str] = {
    "NO_PARTICIPATE": "暂不参与",
    "OBSERVE": "加入观察",
    "SMALL_POSITION": "允许小仓位试错",
    "OPPORTUNITY_ENHANCE": "机会增强",
    "NO_CHASE_HIGH": "禁止追高",
    "MARKET_RISK_HIGH": "市场风险较高",
}

# suggested_position_range（数值 [low, high]） -> 文字（DESIGN §9.6）
POSITION_TEXT: Dict[str, str] = {
    "NO_PARTICIPATE": "不新增",
    "OBSERVE": "轻仓试错（0-10%）",
    "SMALL_POSITION": "维持低仓位（10-25%）",
    "OPPORTUNITY_ENHANCE": "可适度加仓（25-50%）",
    "NO_CHASE_HIGH": "逐步降低风险敞口",
    "MARKET_RISK_HIGH": "逐步降低风险敞口",
}

TEMPLATE_V1: str = (
    "{etf}｜{tier_text}（综合 {score} 分 / 置信 {confidence}%）。"
    "市场环境：{market_regime}。{key_metrics}"
    "建议仓位：{position_text}。下次复核：{review_time}。"
)

TEMPLATE_VERSION = "template-v1"


def position_text_of(tier: str, position_range: List[float] | None = None) -> str:
    base = POSITION_TEXT.get(tier, "不新增")
    if position_range and len(position_range) == 2:
        return f"{base}（{position_range[0]:.0f}-{position_range[1]:.0f}%）"
    return base


def key_metrics_text(supporting: Dict) -> str:
    """把 supporting_metrics 中关键项拼成一句中文；确定性、不引入外部判断。"""
    if not supporting:
        return "（数据不足，关键指标缺失）"
    parts: List[str] = []
    if supporting.get("etf_rsi14") is not None:
        parts.append(f"ETF RSI {supporting['etf_rsi14']:.0f}")
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
