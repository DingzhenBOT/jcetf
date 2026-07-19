"""信号 / 意见 只读查询（P4）。

全部 SELECT，无写。复用 Signal / Opinion ORM（app.db.models.signal_opinion）。
「最新」语义：signal 自然键 (trading_date, target_etf, strategy_version)，version 不可变，
故「每 etf 最新」= 按 target_etf 取 MAX(generated_at) 一行。
"""
from __future__ import annotations

from datetime import date
from typing import List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.signal_opinion import Opinion, Signal

# 历史分页上下限
_MIN_LIMIT = 1
_MAX_LIMIT = 200


def get_latest_signals(session: Session, etf_codes: Optional[List[str]] = None) -> List[Signal]:
    """每 target_etf 取 MAX(generated_at) 一条（子查询 group_by + join）。

    etf_codes 给定则仅返回这些 etf 的最新信号；否则返回全表每 etf 最新一条。
    """
    subq = (
        select(Signal.target_etf, func.max(Signal.generated_at).label("mx"))
        .group_by(Signal.target_etf)
        .subquery()
    )
    stmt = select(Signal).join(
        subq,
        (Signal.target_etf == subq.c.target_etf) & (Signal.generated_at == subq.c.mx),
    )
    if etf_codes:
        stmt = stmt.where(Signal.target_etf.in_(etf_codes))
    return list(session.execute(stmt).scalars().all())


def get_latest_signal_for_etf(session: Session, etf_code: str) -> Optional[Signal]:
    """单 etf 最新一条（get_latest_signals 特化，供 /api/etfs 左连接）。"""
    rows = get_latest_signals(session, [etf_code])
    return rows[0] if rows else None


def get_signal_history(
    session: Session,
    *,
    etf_code: Optional[str] = None,
    trading_date: Optional[date] = None,
    limit: int = 50,
    offset: int = 0,
) -> Tuple[List[Signal], int]:
    """历史信号（降序）。返回 (items, total)。

    limit 夹紧到 [_MIN_LIMIT, _MAX_LIMIT]；offset 夹紧 >= 0。
    """
    limit = max(_MIN_LIMIT, min(_MAX_LIMIT, int(limit)))
    offset = max(0, int(offset))

    base = select(Signal)
    if etf_code is not None:
        base = base.where(Signal.target_etf == etf_code)
    if trading_date is not None:
        base = base.where(Signal.trading_date == trading_date)

    total = session.execute(
        select(func.count()).select_from(base.subquery())
    ).scalar_one()

    stmt = base.order_by(
        Signal.trading_date.desc(), Signal.generated_at.desc()
    ).limit(limit).offset(offset)
    items = list(session.execute(stmt).scalars().all())
    return items, total


def get_opinions_for_etf(
    session: Session,
    etf_code: str,
    phase: Optional[str] = None,
    limit: int = 50,
) -> List[Opinion]:
    """某 ETF 的全部意见（Opinion JOIN Signal ON signal_id WHERE Signal.target_etf=etf_code）。

    可选 phase 过滤；按 generated_at desc。无信号/无意见返回 []。
    """
    limit = max(_MIN_LIMIT, min(_MAX_LIMIT, int(limit)))
    subq = select(Signal.signal_id).where(Signal.target_etf == etf_code).subquery()
    stmt = select(Opinion).where(Opinion.signal_id.in_(select(subq.c.signal_id)))
    if phase is not None:
        stmt = stmt.where(Opinion.phase == phase)
    stmt = stmt.order_by(Opinion.generated_at.desc()).limit(limit)
    return list(session.execute(stmt).scalars().all())
