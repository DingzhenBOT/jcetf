"""ETF 列表路由（P4）。

GET /api/etfs -> ETF 列表（含最新信号摘要，左连接）；无信号 ETF 的 latest_signal=null。
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends

from app.api.deps import get_db
from app.api.schemas import EtfListItem
from app.api.serializers import etf_to_dict
from app.db.session import Session
from app.repository import mapping_repo, signal_repo, quote_repo

router = APIRouter(prefix="/api/etfs", tags=["etfs"])


@router.get("", response_model=List[EtfListItem])
@router.get("/", response_model=List[EtfListItem])
def list_etfs(session: Session = Depends(get_db)):
    """生效 ETF 列表，附带每支最新信号摘要与当日涨幅（无信号则 latest_signal=null）。"""
    mappings = mapping_repo.get_active_mappings(session)
    mappings.sort(key=lambda m: m.etf_code)
    # 批量取每支最新 SNAPSHOT 的当日涨幅（盘中实时），避免 N+1
    change_map = quote_repo.get_latest_snapshot_change_map(
        session, "ETF", [m.etf_code for m in mappings]
    )
    result = []
    for m in mappings:
        latest = signal_repo.get_latest_signal_for_etf(session, m.etf_code)
        result.append(etf_to_dict(m, latest, change_map.get(m.etf_code)))
    return result
