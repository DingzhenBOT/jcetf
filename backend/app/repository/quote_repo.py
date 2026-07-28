"""行情/宽度/数据源状态 的写入（DESIGN §5 / P2，幂等）。

- market_quote 按唯一键 (data_source, symbol_type, symbol, data_kind, timeframe, timestamp)
  用 ON CONFLICT DO UPDATE 幂等写入（同键更新非唯一字段）。
- market_breadth 按 (data_source, trading_date) 每日一条，先查后写保证幂等
  （worker 单实例，无并发，故无需唯一约束/冲突子句）。
- data_source_status 按 (data_source, symbol_type) upsert。
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from datetime import date

from sqlalchemy import case, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.db.models.market import MarketBreadth, MarketQuote
from app.db.models.system import DataSourceStatus

# 唯一键（冲突目标）
_QUOTE_UNIQUE = [
    "data_source",
    "symbol_type",
    "symbol",
    "data_kind",
    "timeframe",
    "timestamp",
]

# 冲突时更新的非唯一字段（不含 PK/唯一键/时间戳语义列）
_QUOTE_UPDATE_COLS = [
    "open",
    "high",
    "low",
    "close",
    "previous_close",
    "volume",
    "amount",
    "change_percent",
    "turnover_rate",
    "main_net_inflow",
    "large_order_inflow",
    "rise_count",
    "fall_count",
    "limit_up_count",
    "limit_down_count",
    "collected_at",
    "source_timestamp",
    "metric_source",
    "metric_definition_version",
    "source_switched",
    "data_quality_status",
]

# 数据源优先级：与 DataSourceConfig 一致（preferred > fallback > dormant）。
# 读路径按此优先级对同一交易日多源数据去重，避免切换主源后 K 线重影。
_SOURCE_PRIORITY = {"sina": 1, "ths": 2, "tx": 3, "em": 4}


def _source_priority_case():
    """返回 data_source -> priority 的 SQL CASE 表达式。"""
    return case(_SOURCE_PRIORITY, value=MarketQuote.data_source, else_=5)


def _best_source_per_date_subquery(
    symbol_type: str,
    symbol: str,
    data_kind: str,
    timeframe: str,
    date_range: Optional[tuple] = None,
):
    """每个 trading_date 的最佳（优先级最高）数据源子查询。

    返回 (symbol, trading_date, min_priority) 三列，用于与主表 join 去重。
    """
    priority = _source_priority_case()
    stmt = (
        select(
            MarketQuote.symbol,
            MarketQuote.trading_date,
            func.min(priority).label("min_priority"),
        )
        .where(
            MarketQuote.symbol_type == symbol_type,
            MarketQuote.symbol == symbol,
            MarketQuote.data_kind == data_kind,
            MarketQuote.timeframe == timeframe,
            MarketQuote.data_quality_status != "ANOMALY",
        )
    )
    if date_range is not None:
        stmt = stmt.where(
            MarketQuote.trading_date >= date_range[0],
            MarketQuote.trading_date <= date_range[1],
        )
    stmt = stmt.group_by(MarketQuote.symbol, MarketQuote.trading_date)
    return stmt.subquery()


def upsert_market_quotes(session: Session, rows: List[Dict]) -> int:
    """批量幂等写入 market_quote。返回处理行数。"""
    if not rows:
        return 0
    stmt = sqlite_insert(MarketQuote).values(rows)
    update_cols = {c: getattr(stmt.excluded, c) for c in _QUOTE_UPDATE_COLS}
    stmt = stmt.on_conflict_do_update(index_elements=_QUOTE_UNIQUE, set_=update_cols)
    session.execute(stmt)
    return len(rows)


def purge_intraday_before(session: Session, keep_date: date) -> int:
    """清理「前一交易日」的分时数据：删除 trading_date < keep_date 的 1m 分时(BAR)行。

    用于满足「每个交易日开盘抓数据，把前一个交易日的数据刷掉」——只保留当前交易日的
    盘中分时，避免 market_quote 无限累积多日 1m 数据、且分时图 x 轴只画当日。
    幂等：盘中多次调用只在第一天首次删除（此后无更旧行）。
    """
    deleted = (
        session.query(MarketQuote)
        .filter(
            MarketQuote.data_kind == "BAR",
            MarketQuote.timeframe == "1m",
            MarketQuote.trading_date < keep_date,
        )
        .delete()
    )
    return int(deleted)


def get_last_source_for_symbol_type(session: Session, symbol_type: str) -> Optional[str]:
    """返回该 symbol_type 最近一条行情的数据源（用于切源标记）。"""
    row = (
        session.execute(
            select(MarketQuote.data_source)
            .where(MarketQuote.symbol_type == symbol_type)
            .order_by(MarketQuote.timestamp.desc())
            .limit(1)
        ).first()
    )
    return row[0] if row else None


def upsert_breadth(session: Session, row: Dict) -> int:
    """每日一条市场宽度（按 data_source + trading_date 幂等）。"""
    existing = session.execute(
        select(MarketBreadth).where(
            MarketBreadth.trading_date == row["trading_date"],
            MarketBreadth.data_source == row["data_source"],
        )
    ).first()
    if existing:
        obj = existing[0]
        for k, v in row.items():
            setattr(obj, k, v)
    else:
        session.add(MarketBreadth(**row))
    return 1


def get_data_source_status(
    session: Session, data_source: str, symbol_type: str
) -> Optional[DataSourceStatus]:
    row = (
        session.execute(
            select(DataSourceStatus).where(
                DataSourceStatus.data_source == data_source,
                DataSourceStatus.symbol_type == symbol_type,
            )
        ).first()
    )
    return row[0] if row else None


def record_data_source_status(
    session: Session,
    *,
    data_source: str,
    symbol_type: Optional[str],
    status: str,
    last_success_at: Optional[datetime],
    last_attempt_at: Optional[datetime],
    consecutive_failures: int,
    note: Optional[str],
) -> None:
    """upsert 数据源健康状态（DESIGN §7 前端 STALE 标记依据）。"""
    stmt = sqlite_insert(DataSourceStatus).values(
        data_source=data_source,
        symbol_type=symbol_type,
        last_success_at=last_success_at,
        last_attempt_at=last_attempt_at,
        consecutive_failures=consecutive_failures,
        status=status,
        note=note,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["data_source", "symbol_type"],
        set_={
            "last_success_at": stmt.excluded.last_success_at,
            "last_attempt_at": stmt.excluded.last_attempt_at,
            "consecutive_failures": stmt.excluded.consecutive_failures,
            "status": stmt.excluded.status,
            "note": stmt.excluded.note,
        },
    )
    session.execute(stmt)


# --------------------------------------------------------------------------- #
# 读函数（P3：引擎层依赖；复用已有索引）
# --------------------------------------------------------------------------- #
def get_latest_quote(
    session: Session,
    symbol_type: str,
    symbol: str,
    data_kind: str = "SNAPSHOT",
    timeframe: str = "snapshot",
) -> Optional[MarketQuote]:
    """该 (symbol_type, symbol, data_kind, timeframe) 的最新一条行情（idx_quote_symbol_time）。

    按时间戳倒序；时间戳相同时按数据源优先级（sina > ths > tx > em）选择，
    避免多源共存时返回非首选源的数据。
    """
    row = (
        session.execute(
            select(MarketQuote)
            .where(
                MarketQuote.symbol_type == symbol_type,
                MarketQuote.symbol == symbol,
                MarketQuote.data_kind == data_kind,
                MarketQuote.timeframe == timeframe,
                MarketQuote.data_quality_status != "ANOMALY",
            )
            .order_by(MarketQuote.timestamp.desc(), _source_priority_case().asc())
            .limit(1)
        ).first()
    )
    return row[0] if row else None


def get_latest_snapshot_change_map(
    session: Session,
    symbol_type: str,
    symbols: List[str],
) -> Dict[str, Optional[float]]:
    """每个 symbol 的最新 SNAPSHOT 的 change_percent（盘中实时当日涨幅）。

    单条聚合查询拿到每个 symbol 的最新时间戳，再回查 change_percent。
    规模小（ETF 映射通常 < 100 支），查询次数 O(1+N) 可接受。
    """
    out: Dict[str, Optional[float]] = {s: None for s in symbols}
    if not symbols:
        return out
    latest = session.execute(
        select(MarketQuote.symbol, func.max(MarketQuote.timestamp).label("mx"))
        .where(
            MarketQuote.symbol_type == symbol_type,
            MarketQuote.symbol.in_(symbols),
            MarketQuote.data_kind == "SNAPSHOT",
            MarketQuote.timeframe == "snapshot",
        )
        .group_by(MarketQuote.symbol)
    ).all()
    for sym, mx in latest:
        rec = session.execute(
            select(MarketQuote.change_percent).where(
                MarketQuote.symbol_type == symbol_type,
                MarketQuote.symbol == sym,
                MarketQuote.data_kind == "SNAPSHOT",
                MarketQuote.timeframe == "snapshot",
                MarketQuote.timestamp == mx,
            )
        ).first()
        out[sym] = float(rec[0]) if rec and rec[0] is not None else None
    return out


def get_bar_history(
    session: Session,
    symbol_type: str,
    symbol: str,
    start_date: date,
    end_date: date,
    timeframe: str = "1d",
    data_kind: str = "BAR",
) -> List[MarketQuote]:
    """[start_date, end_date] 区间内的 BAR（升序，idx_quote_symbol_time + trading_date）。

    对同一交易日的多源数据按优先级去重（sina > ths > tx > em），避免数据源切换后
    K 线出现重影（em + sina 共存导致同一交易日返回两条 BAR）。
    过滤 data_quality_status == "ANOMALY" 的脏数据（#67）。
    """
    priority = _source_priority_case()
    best = _best_source_per_date_subquery(
        symbol_type, symbol, data_kind, timeframe, date_range=(start_date, end_date)
    )
    rows = session.execute(
        select(MarketQuote)
        .join(
            best,
            (MarketQuote.symbol == best.c.symbol)
            & (MarketQuote.trading_date == best.c.trading_date)
            & (priority == best.c.min_priority),
        )
        .where(
            MarketQuote.symbol_type == symbol_type,
            MarketQuote.symbol == symbol,
            MarketQuote.data_kind == data_kind,
            MarketQuote.timeframe == timeframe,
            MarketQuote.trading_date >= start_date,
            MarketQuote.trading_date <= end_date,
            MarketQuote.data_quality_status != "ANOMALY",
        )
        .order_by(MarketQuote.timestamp.asc())
    ).scalars().all()
    return list(rows)


def get_max_bar_timestamp(
    session: Session,
    symbol_type: str,
    symbol: str,
    timeframe: str = "1d",
    data_kind: str = "BAR",
) -> Optional[datetime]:
    """该 (symbol_type, symbol) 的 BAR 最大时间戳（增量回填起点判断）。

    与 get_bar_history 保持一致的数据源优先级（sina > ths > tx > em），按去重后的
    最佳源行取最大时间戳，避免 em 旧数据把回填起点顶到最新而漏补 sina 数据。
    仅取非 ANOMALY 行（#67）：坏数据若带最新时间戳会阻断增量回填，过滤后正常续拉覆盖。
    """
    priority = _source_priority_case()
    best = _best_source_per_date_subquery(symbol_type, symbol, data_kind, timeframe)
    row = (
        session.execute(
            select(func.max(MarketQuote.timestamp))
            .join(
                best,
                (MarketQuote.symbol == best.c.symbol)
                & (MarketQuote.trading_date == best.c.trading_date)
                & (priority == best.c.min_priority),
            )
            .where(
                MarketQuote.symbol_type == symbol_type,
                MarketQuote.symbol == symbol,
                MarketQuote.data_kind == data_kind,
                MarketQuote.timeframe == timeframe,
                MarketQuote.data_quality_status != "ANOMALY",
            )
        ).first()
    )
    return row[0] if row else None


def get_breadth_on_date(session: Session, trading_date: date) -> Optional[MarketBreadth]:
    """指定交易日的市场宽度（idx_breadth_date）。"""
    row = (
        session.execute(
            select(MarketBreadth).where(MarketBreadth.trading_date == trading_date)
        ).first()
    )
    return row[0] if row else None


def get_sector_quotes(
    session: Session,
    sector_type: str,
    codes: List[str],
    trading_date: Optional[date] = None,
) -> List[MarketQuote]:
    """指定板块类型的快照行情（idx_quote_trade_type）；codes 空返回 []。"""
    if not codes:
        return []
    stmt = select(MarketQuote).where(
        MarketQuote.symbol_type == sector_type,
        MarketQuote.symbol.in_(codes),
    )
    if trading_date is not None:
        stmt = stmt.where(MarketQuote.trading_date == trading_date)
    return list(session.execute(stmt).scalars().all())


def get_latest_breadth(session: Session) -> Optional[MarketBreadth]:
    """宽度表按 trading_date 最大的一条（idx_breadth_date）；无数据返回 None。

    供 /api/market/breadth/latest（P4）。
    """
    row = (
        session.execute(
            select(MarketBreadth)
            .order_by(MarketBreadth.trading_date.desc())
            .limit(1)
        ).first()
    )
    return row[0] if row else None
