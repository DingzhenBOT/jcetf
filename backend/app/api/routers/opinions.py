"""意见查询路由（P4）。

GET /api/opinions/{etf} -> 某 ETF 的全部意见（按 signal_id 关联；可选 ?phase=）。
未知 ETF -> 404；phase 非法 -> 422。
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Path, Query

from app.api.deps import get_db
from app.api.schemas import OpinionOut, OpinionsForEtf
from app.api.serializers import opinion_to_dict
from app.db.session import Session
from app.errors import NotFoundError, ValidationError
from app.repository import mapping_repo, signal_repo

router = APIRouter(prefix="/api/opinions", tags=["opinions"])

_VALID_PHASES = {"pre_market", "midday", "pre_close", "post_close"}


@router.get("/{etf}", response_model=OpinionsForEtf)
def opinions_for_etf(
    etf: str = Path(..., description="ETF 代码，如 510300"),
    phase: Optional[str] = Query(None, description="按阶段过滤：pre_market/midday/pre_close/post_close"),
    limit: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_db),
):
    # 1) etf 必须在生效映射中（未知 -> 404）
    active = {m.etf_code for m in mapping_repo.get_active_mappings(session)}
    if etf not in active:
        raise NotFoundError(f"etf not found or not active: {etf}")

    # 2) phase 校验
    if phase is not None and phase not in _VALID_PHASES:
        raise ValidationError(
            f"invalid phase: {phase!r} (expected one of {sorted(_VALID_PHASES)})"
        )

    rows = signal_repo.get_opinions_for_etf(session, etf, phase=phase, limit=limit)
    return OpinionsForEtf(etf_code=etf, items=[opinion_to_dict(o) for o in rows])
