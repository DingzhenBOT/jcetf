"""ORM -> dict 序列化（P4）。

- 档位中文映射在 API 层完成（复用 opinion_engine.templates），避免前端重实现。
- 时间统一 ISO 字符串（naive UTC），None 安全。
- 不涉及任何规则/引擎逻辑，纯展示转换。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from app.db.models.mapping import EtfMapping
from app.db.models.signal_opinion import Opinion, Signal
from app.opinion_engine.templates import TIER_TEXT, position_text_of, key_metrics_text


def _iso(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return str(v)


def signal_to_dict(sig: Signal) -> Dict[str, Any]:
    tier = sig.signal_type or ""
    return {
        "signal_id": sig.signal_id,
        "strategy_version": sig.strategy_version,
        "generated_at": _iso(sig.generated_at),
        "trading_date": sig.trading_date.isoformat() if sig.trading_date is not None else "",
        "target_etf": sig.target_etf,
        "signal_type": tier,
        "signal_type_text": TIER_TEXT.get(tier, tier),
        "score": sig.score,
        "confidence": sig.confidence,
        "market_regime": sig.market_regime,
        "suggested_action": sig.suggested_action,
        "suggested_position_range": sig.suggested_position_range,
        "position_text": position_text_of(tier, sig.suggested_position_range),
        "one_liner": key_metrics_text(sig.supporting_metrics if isinstance(sig.supporting_metrics, dict) else {}),
        "supporting_metrics": sig.supporting_metrics,
        "risk_flags": sig.risk_flags,
        "triggered_rules": sig.triggered_rules,
        "failed_rules": sig.failed_rules,
        "invalidation_conditions": sig.invalidation_conditions,
        "review_time": _iso(sig.review_time),
    }


def opinion_to_dict(o: Opinion) -> Dict[str, Any]:
    return {
        "opinion_id": o.opinion_id,
        "signal_id": o.signal_id,
        "generated_at": _iso(o.generated_at),
        "trading_date": o.trading_date.isoformat() if o.trading_date is not None else "",
        "phase": o.phase,
        "title": o.title,
        "content": o.content,
        "input_summary": o.input_summary,
        "template_version": o.template_version,
    }


def etf_to_dict(m: EtfMapping, latest: Optional[Signal]) -> Dict[str, Any]:
    return {
        "etf_code": m.etf_code,
        "etf_name": m.etf_name,
        "category": m.category,
        "listing": m.listing,  # '场内' / '场外'：前端区分交易场所
        "related_sector_codes": m.related_sector_codes,
        "related_index_code": m.related_index_code,
        "latest_signal": signal_to_dict(latest) if latest is not None else None,
    }
