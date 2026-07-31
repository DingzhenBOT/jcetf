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

from app.db.base import utcnow
from app.db.models.signal_opinion import Opinion, Signal

# 历史分页上下限
_MIN_LIMIT = 1
_MAX_LIMIT = 200

# 「最新信号」最大时效（天）：超过该时长的信号视为过期，不再作为"当前信号"返回
# （用户诉求：最新信号超过两天应清除，避免冻结的脏信号一直挂着；历史记录 /api/signals/history
# 不受影响，仍保留全部）。None 表示不过滤。
# C18-hotfix：worker 刚重启、尚无当日新信号时，None 避免旧信号被误清导致前端"数据全没"；
# 等 worker 稳定产出当日信号后，可恢复为 2（需重启 API）。
LATEST_SIGNAL_MAX_AGE_DAYS: Optional[int] = None


def get_latest_signals(
    session: Session,
    etf_codes: Optional[List[str]] = None,
    max_age_days: Optional[int] = LATEST_SIGNAL_MAX_AGE_DAYS,
) -> List[Signal]:
    """每 target_etf 取 MAX(generated_at) 一条（子查询 group_by + join）。

    etf_codes 给定则仅返回这些 etf 的最新信号；否则返回全表每 etf 最新一条。
    max_age_days 给定时，子查询仅统计该窗口内的信号——过期信号不计入"最新"，
    对应 etf 因此无最新信号（返回列表排除），但其历史记录仍然完整。
    """
    from datetime import timedelta

    subq_stmt = select(Signal.target_etf, func.max(Signal.generated_at).label("mx")).group_by(
        Signal.target_etf
    )
    if max_age_days is not None:
        cutoff = utcnow() - timedelta(days=max_age_days)
        subq_stmt = subq_stmt.where(Signal.generated_at >= cutoff)
    subq = subq_stmt.subquery()
    stmt = select(Signal).join(
        subq,
        (Signal.target_etf == subq.c.target_etf) & (Signal.generated_at == subq.c.mx),
    )
    if etf_codes:
        stmt = stmt.where(Signal.target_etf.in_(etf_codes))
    # 按综合分降序：所有信号列表（首页最新信号表、复盘清单）统一以分排序。
    # SQLite 下 NULL 视为最小，DESC 时自动排末，无需 nullslast。
    stmt = stmt.order_by(Signal.score.desc())
    return list(session.execute(stmt).scalars().all())


def get_latest_signal_for_etf(
    session: Session, etf_code: str, max_age_days: Optional[int] = LATEST_SIGNAL_MAX_AGE_DAYS
) -> Optional[Signal]:
    """单 etf 最新一条（get_latest_signals 特化，供 /api/etfs 左连接）。"""
    rows = get_latest_signals(session, [etf_code], max_age_days)
    return rows[0] if rows else None


def get_previous_comparable_signal(session: Session, current: Signal) -> Optional[Signal]:
    """取同策略版本、严格早于当前交易日的最近信号。

    同日不同策略版本/阶段不可用于“分数下降”比较，否则策略升版当天会把规则差异
    误判成市场恶化。
    """
    return session.execute(
        select(Signal)
        .where(
            Signal.target_etf == current.target_etf,
            Signal.strategy_version == current.strategy_version,
            Signal.trading_date < current.trading_date,
        )
        .order_by(Signal.trading_date.desc(), Signal.generated_at.desc())
        .limit(1)
    ).scalar_one_or_none()


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
