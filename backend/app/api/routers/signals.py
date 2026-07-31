"""信号查询路由（P4）。

GET /api/signals/latest  -> 每支生效 ETF 最新一条公共信号（P5 每 30s 轮询）
GET /api/signals/history -> 历史信号（etf_code / trading_date 过滤 + 分页）
"""
from __future__ import annotations

from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, Path, Query

from app.api.deps import get_db
from app.api.schemas import SignalHistoryPage, SignalOut
from app.api.serializers import signal_to_dict
from app.db.session import Session
from app.errors import ConflictError, NotFoundError
from app.repository import mapping_repo, signal_repo

router = APIRouter(prefix="/api/signals", tags=["signals"])


@router.get("/latest", response_model=List[SignalOut])
def signals_latest(session: Session = Depends(get_db)):
    """每支生效 ETF 的最新一条信号。空库返回 []（不 404）。"""
    codes = [m.etf_code for m in mapping_repo.get_active_mappings(session)]
    rows = signal_repo.get_latest_signals(session, codes) if codes else []
    # 按 etf 代码稳定排序，便于前端 diff
    rows.sort(key=lambda s: s.target_etf)
    return [signal_to_dict(s) for s in rows]


@router.get("/history", response_model=SignalHistoryPage)
def signals_history(
    etf_code: Optional[str] = Query(None, description="按 ETF 代码过滤"),
    trading_date: Optional[str] = Query(None, description="交易日 YYYY-MM-DD"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db),
):
    """历史信号（降序）。trading_date 校验为合法日期，非法 -> 422。"""
    td: Optional[date] = None
    if trading_date is not None:
        try:
            td = date.fromisoformat(trading_date)
        except ValueError:
            from app.errors import ValidationError

            raise ValidationError(f"invalid trading_date: {trading_date!r} (expected YYYY-MM-DD)")

    items, total = signal_repo.get_signal_history(
        session, etf_code=etf_code, trading_date=td, limit=limit, offset=offset
    )
    return SignalHistoryPage(
        items=[signal_to_dict(s) for s in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/{etf}/refresh", response_model=SignalOut)
def signal_refresh(etf: str = Path(..., description="ETF 代码，如 510300"), session: Session = Depends(get_db)):
    """按需重算该 ETF 的盘中实时信号（phase=live），亚秒级；呼应「想立刻查看意见就能」。

    全程在跨进程写锁下执行（非阻塞）：worker 正在写库时返回 409，前端稍后重试。
    为简化，重算作用于全部生效映射（数量少、耗时低），随后返回本 ETF 最新信号。
    """
    active = {m.etf_code for m in mapping_repo.get_active_mappings(session)}
    if etf not in active:
        raise NotFoundError(f"etf not found or not active: {etf}")

    from app.config import get_settings
    from app.db.lock import db_writer_lock
    from app.evaluation.pipeline import post_collection_evaluate

    settings = get_settings()
    with db_writer_lock(settings, blocking=False) as acquired:
        if not acquired:
            raise ConflictError("worker 正在写库，请稍后重试刷新")
        post_collection_evaluate(session, settings, phase="live")

    sig = signal_repo.get_latest_signal_for_etf(session, etf)
    if sig is None:
        raise NotFoundError(f"refresh 后仍未生成信号: {etf}")
    return signal_to_dict(sig)
