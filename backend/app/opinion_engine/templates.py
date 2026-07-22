"""意见模板（确定性，template-v2）。

- TIER_TEXT：signal_type 英文档位码 -> 中文展示（直白口语，避免术语，与前端 tier.ts 一致）。
- POSITION_TEXT：仓位动作文字（不含数字区间）；区间由 position_text_of 按
  suggested_position_range 动态拼接，避免重复出现「（x-y%）」。
- REGIME_TEXT：market_regime 英文码 -> 中文展示（确定性，前端 regimeText 同源）。
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

# market_regime（英文市场环境码） -> 中文展示（确定性，前端 regimeText 同源）
REGIME_TEXT: Dict[str, str] = {
    "STRONG_UP": "强势上行",
    "TREND_UP": "震荡上行",
    "VOLATILE": "震荡",
    "WEAK": "偏弱",
    "BEAR": "空头",
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
    "当前市场环境{market_regime}。{key_metrics}"
    "因此建议{position_text}；下次复核：{review_time}。"
)

TEMPLATE_VERSION = "template-v2"


def position_text_of(tier: str, position_range: List[float] | None = None) -> str:
    base = POSITION_TEXT.get(tier, "不加仓")
    if position_range and len(position_range) == 2:
        low, high = position_range
        if not (low == 0 and high == 0):
            return f"{base}（{low:.0f}-{high:.0f}%）"
    return base


def key_metrics_text(supporting: Dict) -> str:
    """把 supporting_metrics 中关键项翻译成因果叙述（人话）；确定性、不引入外部判断。

    量价关系最贴近「盘中该不该动」，故置前展示。
    """
    if not supporting:
        return "当前数据不足，关键指标缺失，建议以观察为主。"
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

    # 原有指标（阈值化人话）
    rsi = supporting.get("etf_rsi14")
    if rsi is not None:
        if rsi > 70:
            parts.append(f"RSI 达 {rsi:.0f}，已进入超买区，注意回调风险")
        elif rsi < 30:
            parts.append(f"RSI 仅 {rsi:.0f}，接近超卖，下行动能或近尾声")
        else:
            parts.append(f"RSI {rsi:.0f}，处于中性区间，未见极端")

    rs = supporting.get("etf_rs_20d")
    if rs is not None:
        if rs > 1.05:
            parts.append(f"近 20 日相对沪深300 强弱 {rs:.2f}，明显强于大盘")
        elif rs < 0.95:
            parts.append(f"近 20 日相对沪深300 强弱 {rs:.2f}，弱于大盘")
        else:
            parts.append(f"近 20 日相对沪深300 强弱 {rs:.2f}，与大盘基本同步")

    sec = supporting.get("sector_score")
    if sec is not None:
        label = "偏强" if sec >= 60 else ("偏弱" if sec < 40 else "温和")
        parts.append(f"所属板块趋势评分 {sec:.0f}，{label}")

    ff = supporting.get("fund_flow_score")
    if ff is not None:
        label = "偏强" if ff >= 60 else ("偏弱" if ff < 40 else "一般")
        parts.append(f"资金持续性 {ff:.0f}，{label}")

    ar = supporting.get("advance_ratio")
    if ar is not None:
        if ar > 0.6:
            parts.append(f"全市场超六成个股上涨（{ar*100:.0f}%），氛围偏多")
        elif ar < 0.4:
            parts.append(f"全市场超六成个股下跌（{(1-ar)*100:.0f}%），氛围偏弱")
        else:
            parts.append(f"上涨家数占比 {ar*100:.0f}%，多空基本均衡")

    if not parts:
        return "当前数据不足，关键指标缺失，建议以观察为主。"
    return "；".join(parts) + "。"
