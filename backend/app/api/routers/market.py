"""市场总览路由（P4，纯只读聚合，不调用任何 engine）。

GET /api/market/breadth/latest -> 最新市场宽度（涨跌家数/涨跌停/上涨占比/成交额）
GET /api/market/overview      -> 主要指数最新 BAR + 宽度 + 成交额 + 信号风险汇总（P5 每 30s 轮询）

注：em 不可达时指数 BAR / 宽度可能缺失，接口返回 null 而非 500，前端据以标「观察期数据不足」。
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends

from app.api.deps import get_db
from app.api.schemas import (
    BreadthOut,
    IndexSnapshotOut,
    MarketOverviewOut,
)
from app.config import get_settings
from app.db.session import Session
from app.repository import quote_repo, signal_repo
from app.repository import mapping_repo

router = APIRouter(prefix="/api/market", tags=["market"])

# 宽基代码 -> 中文名（与 settings.strategy.broad_index_codes 默认一致）
INDEX_LABELS = {
    "000300": "沪深300",
    "000001": "上证综指",
    "399001": "深证成指",
}


def _compute_market_risk_level(counts: dict, total: int) -> str:
    """按 MARKET_RISK_HIGH + NO_PARTICIPATE 占比推导风险等级（只读汇总，非规则重算）。"""
    if total == 0:
        return "未知"
    risky = counts.get("MARKET_RISK_HIGH", 0) + counts.get("NO_PARTICIPATE", 0)
    frac = risky / total
    if frac < 0.25:
        return "偏低"
    if frac < 0.5:
        return "中性"
    if frac < 0.75:
        return "偏高"
    return "高"


@router.get("/breadth/latest", response_model=BreadthOut)
def breadth_latest(session: Session = Depends(get_db)):
    """最新市场宽度；无数据返回字段全 null（不 404）。"""
    b = quote_repo.get_latest_breadth(session)
    if b is None:
        return BreadthOut()
    rise = b.total_rise or 0
    fall = b.total_fall or 0
    denom = rise + fall
    advance_ratio = (rise / denom) if denom > 0 else None
    return BreadthOut(
        trading_date=b.trading_date.isoformat() if b.trading_date is not None else None,
        total_rise=b.total_rise,
        total_fall=b.total_fall,
        total_flat=b.total_flat,
        limit_up=b.limit_up,
        limit_down=b.limit_down,
        total_amount=b.total_amount,
        advance_ratio=advance_ratio,
        data_source=b.data_source,
    )


@router.get("/overview", response_model=MarketOverviewOut)
def market_overview(session: Session = Depends(get_db)):
    """主要指数最新 BAR + 宽度 + 信号风险汇总。"""
    settings = get_settings()
    codes = settings.strategy.broad_index_codes

    indices: List[IndexSnapshotOut] = []
    index_dates: List[str] = []
    for code in codes:
        q = quote_repo.get_latest_quote(
            session, "INDEX", code, data_kind="BAR", timeframe="1d"
        )
        if q is not None:
            indices.append(
                IndexSnapshotOut(
                    code=code,
                    name=INDEX_LABELS.get(code, code),
                    close=q.close,
                    change_percent=q.change_percent,
                    source=q.data_source,
                )
            )
            if q.trading_date is not None:
                index_dates.append(q.trading_date.isoformat())
        else:
            indices.append(IndexSnapshotOut(code=code, name=INDEX_LABELS.get(code, code)))

    # 宽度
    b = quote_repo.get_latest_breadth(session)
    breadth: Optional[BreadthOut] = None
    breadth_date: Optional[str] = None
    if b is not None:
        rise = b.total_rise or 0
        fall = b.total_fall or 0
        denom = rise + fall
        advance_ratio = (rise / denom) if denom > 0 else None
        breadth = BreadthOut(
            trading_date=b.trading_date.isoformat() if b.trading_date is not None else None,
            total_rise=b.total_rise,
            total_fall=b.total_fall,
            total_flat=b.total_flat,
            limit_up=b.limit_up,
            limit_down=b.limit_down,
            total_amount=b.total_amount,
            advance_ratio=advance_ratio,
            data_source=b.data_source,
        )
        if b.trading_date is not None:
            breadth_date = b.trading_date.isoformat()

    # 信号风险汇总（来自最新信号，只读统计）
    active_codes = [m.etf_code for m in mapping_repo.get_active_mappings(session)]
    latest = signal_repo.get_latest_signals(session, active_codes) if active_codes else []
    counts: dict = {}
    for s in latest:
        counts[s.signal_type] = counts.get(s.signal_type, 0) + 1
    signal_risk = {
        "counts": counts,
        "total": len(latest),
        "market_risk_level": _compute_market_risk_level(counts, len(latest)),
    }

    # as_of：indices / breadth 中的最大交易日
    candidates = [d for d in index_dates + ([breadth_date] if breadth_date else [])]
    as_of = max(candidates) if candidates else None

    return MarketOverviewOut(
        as_of=as_of,
        indices=indices,
        breadth=breadth,
        signal_risk=signal_risk,
    )
