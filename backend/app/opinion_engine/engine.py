"""意见生成（P3，D1：模板化，无 LLM）。

OpinionEngine.generate(signal, phase, input_summary) -> dict
- signal：strategy_engine 产出的 Signal 形态字典（含 signal_type/score/confidence/market_regime/
  suggested_position_range/supporting_metrics/review_time 等）。
- phase：pre_market/midday/pre_close/post_close（决定意见「盘中/复盘」语境，不影响档位）。
- 返回 {title, content, template_version, model_version=None}。
- 仅做模板填充 + 可选 PhraseClient 润色；**绝不**修改 signal_type/score/confidence/position。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from app.opinion_engine.phrase import PhraseClient, TemplatePhraseClient
from app.opinion_engine.templates import (
    REGIME_TEXT,
    TEMPLATE_V1,
    TEMPLATE_LIVE,
    TEMPLATE_LUNCH,
    TEMPLATE_VERSION,
    TIER_TEXT,
    basis_text,
    key_metrics_text,
    position_text_of,
    r1r2_text,
    trade_plan_text,
)


class OpinionEngine:
    def __init__(self, phrase: Optional[PhraseClient] = None) -> None:
        self.phrase = phrase or TemplatePhraseClient()

    def generate(
        self, signal: Dict[str, Any], phase: str, input_summary: Dict[str, Any] | None = None
    ) -> Dict[str, Any]:
        tier = signal.get("signal_type", "NO_PARTICIPATE")
        tier_text = TIER_TEXT.get(tier, tier)
        score = signal.get("score")
        confidence = signal.get("confidence")
        regime = signal.get("market_regime")
        pos = signal.get("suggested_position_range")
        review = signal.get("review_time")
        supporting = signal.get("supporting_metrics", {}) or {}

        # 数值确定性格式化（避免 NaN/None 污染文案）
        score_s = f"{score:.1f}" if isinstance(score, (int, float)) else "—"
        conf_s = f"{confidence:.0f}" if isinstance(confidence, (int, float)) else "—"
        regime_s = REGIME_TEXT.get(regime, regime) if regime else "未知"
        review_s = review.strftime("%Y-%m-%d %H:%M") if hasattr(review, "strftime") else (str(review) if review else "—")

        key_metrics = key_metrics_text(supporting)
        position_text = position_text_of(tier, pos)

        # C23：按 phase 选模板（盘中实时/午盘突出强度与倾向；收盘后追加三档价位）
        if phase == "live":
            strength = supporting.get("intraday_strength")
            lean = supporting.get("intraday_lean") or "中性"
            strength_s = f"{strength:.0f}" if isinstance(strength, (int, float)) else "—"
            content = TEMPLATE_LIVE.format(
                etf=signal.get("target_etf", ""),
                strength=strength_s,
                lean=lean,
                key_metrics=key_metrics,
                r1r2=r1r2_text(supporting),
                position_text=position_text,
                review_time=review_s,
            )
        elif phase == "lunch":
            strength = supporting.get("intraday_strength")
            lean = supporting.get("intraday_lean") or "中性"
            strength_s = f"{strength:.0f}" if isinstance(strength, (int, float)) else "—"
            content = TEMPLATE_LUNCH.format(
                etf=signal.get("target_etf", ""),
                strength=strength_s,
                lean=lean,
                key_metrics=key_metrics,
                r1r2=r1r2_text(supporting),
                position_text=position_text,
            )
        else:
            content = TEMPLATE_V1.format(
                etf=signal.get("target_etf", ""),
                tier_text=tier_text,
                score=score_s,
                confidence=conf_s,
                market_regime=regime_s,
                key_metrics=key_metrics,
                position_text=position_text,
                review_time=review_s,
            )
            # 收盘后复盘：追加明日三档价位（突破/加仓/止损）
            if phase == "post_close" and signal.get("trade_plan"):
                content += trade_plan_text(signal["trade_plan"])

        # 仅润色文案，不改数值
        content = self.phrase.phrase(content)

        title = f"{signal.get('target_etf', '')} {tier_text}"
        # 专业「分析依据」叙述：用算法关键指标替代原始 KV，供前端「查看依据」渲染
        basis = basis_text(supporting, input_summary, phase)
        return {
            "title": title,
            "content": content,
            "basis_text": basis,
            "template_version": TEMPLATE_VERSION,
            "model_version": None,
            "phase": phase,
            # C23：收盘后三档价位透传，便于序列化落库（Opinion.trade_plan）
            "trade_plan": signal.get("trade_plan"),
        }
