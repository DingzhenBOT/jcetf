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
    TEMPLATE_V1,
    TEMPLATE_VERSION,
    TIER_TEXT,
    key_metrics_text,
    position_text_of,
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

        # 数值确定性格式化（避免 NaN/None 污染文案）
        score_s = f"{score:.1f}" if isinstance(score, (int, float)) else "—"
        conf_s = f"{confidence:.0f}" if isinstance(confidence, (int, float)) else "—"
        regime_s = regime if regime else "未知"
        review_s = review.strftime("%Y-%m-%d %H:%M") if hasattr(review, "strftime") else (str(review) if review else "—")

        content = TEMPLATE_V1.format(
            etf=signal.get("target_etf", ""),
            tier_text=tier_text,
            score=score_s,
            confidence=conf_s,
            market_regime=regime_s,
            key_metrics=key_metrics_text(signal.get("supporting_metrics", {}) or {}),
            position_text=position_text_of(tier, pos),
            review_time=review_s,
        )
        # 仅润色文案，不改数值
        content = self.phrase.phrase(content)

        title = f"{signal.get('target_etf', '')} {tier_text}"
        return {
            "title": title,
            "content": content,
            "template_version": TEMPLATE_VERSION,
            "model_version": None,
            "phase": phase,
        }
