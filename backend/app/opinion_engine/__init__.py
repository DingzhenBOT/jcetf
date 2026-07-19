"""意见引擎包（P3）：模板化生成盘中/复盘意见（LLM 仅润色，不判断）。"""
from __future__ import annotations

from app.opinion_engine.engine import OpinionEngine
from app.opinion_engine.phrase import (
    LLMPhraseClient,
    PhraseClient,
    TemplatePhraseClient,
)
from app.opinion_engine.templates import TEMPLATE_V1, TEMPLATE_VERSION, TIER_TEXT

__all__ = [
    "OpinionEngine",
    "PhraseClient",
    "TemplatePhraseClient",
    "LLMPhraseClient",
    "TIER_TEXT",
    "TEMPLATE_V1",
    "TEMPLATE_VERSION",
]
