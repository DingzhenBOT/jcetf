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


def basis_text(
    supporting: Dict,
    input_summary: Dict | None = None,
    phase: str | None = None,
) -> str:
    """用算法关键指标生成专业「分析依据」叙述（前端「查看依据」渲染，替代原始 KV）。

    输入 supporting_metrics（StrategyEngine 产出）：etf_rsi14 / etf_rs_20d / etf_ma20_slope /
    etf_atr_pct / etf_vol_ratio / sector_score / fund_flow_score / advance_ratio / market_regime /
    intraday_change_percent / vp_state / vp_state_text / vp_vol_ratio_state / vp_patterns 等。
    input_summary：as_of / etf_code / sector_code / related_index_code / market_regime（标注标的）。
    phase：pre_market/midday/pre_close/post_close（决定「盘中/复盘」语境前缀）。

    确定性、不引入外部判断；缺失项明确标注，避免误读为「中性」。
    """
    if not supporting:
        return "当前算法依据不足：未获取到该标的的任何技术指标，建议以观察为主，等待数据补全后复核。"

    parts: List[str] = []
    phase_prefix = {
        "pre_market": "盘前",
        "midday": "盘中",
        "pre_close": "收盘前",
        "post_close": "收盘复盘",
    }.get(phase or "", "")

    etf_code = (input_summary or {}).get("etf_code")
    sector_code = (input_summary or {}).get("sector_code")
    related_index = (input_summary or {}).get("related_index_code")

    # 0) 标的 + 市场环境 + 市场宽度（首句）
    env = supporting.get("market_regime")
    env_s = REGIME_TEXT.get(env, env) if env else "未知"
    ar = supporting.get("advance_ratio")
    if ar is not None:
        width_label = "偏多" if ar > 0.6 else ("偏弱" if ar < 0.4 else "多空均衡")
        width_s = f"全市场上涨家数占比 {ar * 100:.0f}%（{width_label}）"
    else:
        width_s = "市场宽度数据缺失"
    lead = f"{phase_prefix}依据：标的 {etf_code or '—'}"
    if sector_code:
        lead += f" 所属板块 {sector_code}"
    if related_index:
        lead += f"（跟踪指数 {related_index}）"
    lead += f"，当前市场环境「{env_s}」；{width_s}。"
    parts.append(lead)

    # 1) ETF 技术面（RSI / 相对强弱 / MA20 斜率 / ATR 波动）
    rsi = supporting.get("etf_rsi14")
    rs = supporting.get("etf_rs_20d")
    slope = supporting.get("etf_ma20_slope")
    atr = supporting.get("etf_atr_pct")
    if any(v is not None for v in (rsi, rs, slope, atr)):
        tech = "ETF 技术面："
        if rsi is not None:
            tech += (
                f"RSI14={rsi:.0f}"
                + ("（超买，警惕回调）" if rsi > 70 else ("（超卖，下行动能或近尾声）" if rsi < 30 else "（中性）"))
                + "；"
            )
        if rs is not None:
            tech += (
                f"近 20 日相对沪深300 强弱 RS={rs:.2f}"
                + ("（明显强于大盘）" if rs > 1.05 else ("（弱于大盘）" if rs < 0.95 else "（与大盘同步）"))
                + "；"
            )
        if slope is not None:
            tech += f"MA20 斜率 {slope * 100:+.1f}%（" + ("向上，短期趋势偏强" if slope > 0 else "向下，短期趋势偏弱") + "）；"
        if atr is not None:
            tech += f"ATR 波动率 {atr:.1f}%（" + ("波动较大，仓位需控风险" if atr > 3 else "波动温和") + "）。"
        parts.append(tech)
    else:
        parts.append("ETF 技术面：未获取到该标的场内日 K 线（如场外联接基金无场内行情），无法计算 RSI / 相对强弱 / 均线 / 波动率，技术信号不参与评分。")

    # 2) 量价关系（最贴近「该不该动」）
    vp_text = supporting.get("vp_state_text")
    vp_patterns = supporting.get("vp_patterns") or []
    if vp_text and vp_text not in ("数据不足", "样本不足"):
        s = f"量价关系：{vp_text}"
        if vp_patterns:
            s += "；形态：" + "、".join(VP_PATTERN_TEXT.get(p, p) for p in vp_patterns)
        parts.append(s + "。")

    # 3) 板块趋势 + 资金持续性
    sec = supporting.get("sector_score")
    ff = supporting.get("fund_flow_score")
    if sec is not None or ff is not None:
        seg = "板块与资金："
        if sec is not None:
            seg += f"所属板块趋势评分 {sec:.0f}（" + ("偏强" if sec >= 60 else ("偏弱" if sec < 40 else "温和")) + "）；"
        if ff is not None:
            seg += f"板块资金持续性 {ff:.0f}（" + ("偏强" if ff >= 60 else ("偏弱" if ff < 40 else "一般")) + "）。"
        parts.append(seg)
    else:
        parts.append("板块与资金：该标的未关联板块或板块资金数据缺失，板块趋势与主力资金持续性未纳入评分。")

    # 4) 数据完整性 -> 置信说明（解释为何综合分偏保守）
    miss_etf = all(v is None for v in (rsi, rs, slope, atr))
    miss_sector = sec is None
    miss_flow = ff is None
    missing = []
    if miss_etf:
        missing.append("ETF 技术面")
    if miss_sector:
        missing.append("板块趋势")
    if miss_flow:
        missing.append("资金持续性")
    if ar is None:
        missing.append("市场宽度")
    if missing:
        parts.append(
            f"数据完整性：{'、'.join(missing)} 缺失，置信度相应下调，综合分偏保守；"
            "以上结论主要基于已获取的市场环境信号，建议结合其他信息复核后再决策。"
        )

    return "".join(parts)
