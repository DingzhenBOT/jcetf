"""市场总览路由（P4，纯只读聚合，不调用任何 engine）。

GET /api/market/breadth/latest -> 最新市场宽度（涨跌家数/涨跌停/上涨占比/成交额）
GET /api/market/overview      -> 主要指数实时 SNAPSHOT（回退 BAR）+ 宽度 + 成交额 + 信号风险汇总（P5 每 60s 轮询）

注：指数优先取实时 SNAPSHOT（盘中每 3 分钟更新，含真实涨跌）；SNAPSHOT 与 BAR 均缺失（如数据源不可达）
    时该指数项仅含名称、无涨跌数据，前端标「观察期数据不足」。接口本身不抛 500。
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends

from app.api.deps import get_db
from app.api.schemas import (
    BreadthOut,
    IndexHistoryOut,
    IndexHistoryPoint,
    IndexSnapshotOut,
    IntradayOut,
    IntradayPoint,
    MarketOverviewOut,
)
from datetime import date, timedelta

from app.config import get_settings
from app.db.session import Session
from app.market_calendar import beijing_now
from app.opinion_engine.index_read import humanize_index_read
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
        # 优先最新实时 SNAPSHOT（盘中每 3 分钟更新，含实时涨跌）；缺失（如数据源不可达）回退日线 BAR（收盘/历史）
        q = quote_repo.get_latest_quote(
            session, "INDEX", code, data_kind="SNAPSHOT", timeframe="snapshot"
        )
        if q is None:
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


@router.get("/index/{code}/history", response_model=IndexHistoryOut)
def index_history(code: str, days: int = 60, session: Session = Depends(get_db)):
    """指数日线历史 + 人话自解读。

    days：回溯交易日数（默认 60）。无数据返回空 points + 观察期提示（不 404）。
    """
    end = date.today()
    start = end - timedelta(days=int(days) * 2 + 30)  # 自然日窗口，容忍非交易日
    rows = quote_repo.get_bar_history(
        session, "INDEX", code, start, end, timeframe="1d", data_kind="BAR"
    )
    name = INDEX_LABELS.get(code, code)
    # 仅保留含收盘价的 BAR，按交易日升序
    rows = [r for r in rows if r.close is not None]
    points = [
        IndexHistoryPoint(
            date=(r.trading_date.isoformat() if r.trading_date is not None else r.timestamp.isoformat()[:10]),
            close=float(r.close),
            volume=float(r.volume or 0),
            amount=float(r.amount or 0),
            change_percent=(float(r.change_percent) if r.change_percent is not None else None),
        )
        for r in rows
    ]
    read_result = humanize_index_read(code, name, rows)
    return IndexHistoryOut(
        code=code,
        name=name,
        points=points,
        read=read_result["read"],
        signals=read_result["signals"],
    )


@router.get("/etf/{code}/history", response_model=IndexHistoryOut)
def etf_history(code: str, days: int = 60, session: Session = Depends(get_db)):
    """ETF 日线历史 + 人话自解读（与指数端点对称，复用 humanize_index_read）。

    days：回溯交易日数（默认 60）。无数据返回空 points + 观察期提示（不 404）。
    """
    end = date.today()
    start = end - timedelta(days=int(days) * 2 + 30)  # 自然日窗口，容忍非交易日
    rows = quote_repo.get_bar_history(
        session, "ETF", code, start, end, timeframe="1d", data_kind="BAR"
    )
    name_map = {m.etf_code: m.etf_name for m in mapping_repo.get_active_mappings(session)}
    name = name_map.get(code, code)
    rows = [r for r in rows if r.close is not None]
    points = [
        IndexHistoryPoint(
            date=(r.trading_date.isoformat() if r.trading_date is not None else r.timestamp.isoformat()[:10]),
            close=float(r.close),
            volume=float(r.volume or 0),
            amount=float(r.amount or 0),
            change_percent=(float(r.change_percent) if r.change_percent is not None else None),
        )
        for r in rows
    ]
    read_result = humanize_index_read(code, name, rows)
    return IndexHistoryOut(
        code=code,
        name=name,
        points=points,
        read=read_result["read"],
        signals=read_result["signals"],
    )


@router.get("/{type}/{code}/intraday", response_model=IntradayOut)
def intraday(
    type: str,
    code: str,
    day: Optional[str] = None,
    session: Session = Depends(get_db),
):
    """盘中 1 分钟分时（类似同花顺分时图）。

    type：etf | index；day：交易日 YYYY-MM-DD（默认今天）。
    返回当日 1m 序列（价格/均价/成交量）+ 昨收（用于着色与涨跌幅）。
    """
    symbol_type = "ETF" if type.lower() == "etf" else "INDEX"
    trading_date = date.fromisoformat(day) if day else date.today()
    rows = quote_repo.get_bar_history(
        session, symbol_type, code, trading_date, trading_date, timeframe="1m", data_kind="BAR"
    )

    name_map = {m.etf_code: m.etf_name for m in mapping_repo.get_active_mappings(session)}
    if symbol_type == "INDEX":
        name = INDEX_LABELS.get(code, code)
    else:
        name = name_map.get(code, code)

    snap = quote_repo.get_latest_quote(
        session, symbol_type, code, data_kind="SNAPSHOT", timeframe="snapshot"
    )
    prev_close = float(snap.previous_close) if snap and snap.previous_close is not None else None

    points: List[IntradayPoint] = []
    cum_vol = 0.0
    cum_pv = 0.0
    for r in rows:
        vol = float(r.volume or 0)
        cum_vol += vol
        cum_pv += (float(r.close or 0)) * vol
        avg = (cum_pv / cum_vol) if cum_vol else float(r.close or 0)
        # 存储的是 UTC，转回北京时间用于分时图展示（HH:MM 形式）
        ts_bj = beijing_now(r.timestamp) if r.timestamp is not None else None
        points.append(
            IntradayPoint(
                time=(ts_bj.isoformat() if ts_bj is not None else ""),
                price=float(r.close or 0),
                avg=float(avg),
                volume=vol,
            )
        )
    # 轻量人话：基于末点价 vs 昨收
    read = ""
    if points and prev_close:
        last = points[-1].price
        cp = (last / prev_close - 1) * 100 if prev_close else 0.0
        read = (
            f"{name} 当日分时：昨收 {prev_close:.3f}，最新 {last:.3f}"
            f"（{cp:+.2f}%），共 {len(points)} 个分钟点。"
        )
    elif points:
        read = f"{name} 当日分时：共 {len(points)} 个分钟点（昨收缺失）。"
    return IntradayOut(
        code=code,
        name=name,
        date=trading_date.isoformat(),
        prev_close=prev_close,
        points=points,
        read=read,
        signals=[],
    )
