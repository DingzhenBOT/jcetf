"""行情类模型：market_quote（统一行情）+ market_breadth（全市场宽度）。"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utcnow


class MarketQuote(Base):
    """统一行情表（指数/板块/ETF 的 SNAPSHOT 与 BAR 都进这里）。

    唯一键 data_source+symbol_type+symbol+data_kind+timeframe+timestamp 保证幂等写入。
    """

    __tablename__ = "market_quote"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    data_source: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol_type: Mapped[str] = mapped_column(String(16), nullable=False)  # INDEX/INDUSTRY/CONCEPT/ETF
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    data_kind: Mapped[str] = mapped_column(String(8), nullable=False)     # SNAPSHOT/BAR
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)     # snapshot/1m/3m/5m/1d
    trading_date: Mapped[date] = mapped_column(Date, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)  # UTC naive
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)
    previous_close: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    amount: Mapped[float | None] = mapped_column(Float)
    change_percent: Mapped[float | None] = mapped_column(Float)
    turnover_rate: Mapped[float | None] = mapped_column(Float)
    main_net_inflow: Mapped[float | None] = mapped_column(Float)
    large_order_inflow: Mapped[float | None] = mapped_column(Float)
    rise_count: Mapped[int | None] = mapped_column(Integer)
    fall_count: Mapped[int | None] = mapped_column(Integer)
    limit_up_count: Mapped[int | None] = mapped_column(Integer)
    limit_down_count: Mapped[int | None] = mapped_column(Integer)
    collected_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    source_timestamp: Mapped[datetime | None] = mapped_column(DateTime)  # 源无则 NULL
    metric_source: Mapped[str | None] = mapped_column(String(32))
    metric_definition_version: Mapped[str | None] = mapped_column(String(32))
    source_switched: Mapped[int] = mapped_column(Integer, default=0)
    data_quality_status: Mapped[str] = mapped_column(String(16), default="OK")

    __table_args__ = (
        # SQLite 下 UniqueConstraint 会变内联自动索引；用 Index(unique=True) 才能得到具名唯一索引
        Index(
            "uq_market_quote",
            "data_source", "symbol_type", "symbol", "data_kind", "timeframe", "timestamp",
            unique=True,
        ),
        Index("idx_quote_symbol_time", "symbol", "data_kind", "timeframe", "timestamp"),
        Index("idx_quote_trade_type", "trading_date", "symbol_type"),
    )


class MarketBreadth(Base):
    """全市场宽度 / 情绪（每日从全市场快照累计，无历史 API，DESIGN §3.1）。"""

    __tablename__ = "market_breadth"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trading_date: Mapped[date] = mapped_column(Date, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    total_rise: Mapped[int | None] = mapped_column(Integer)
    total_fall: Mapped[int | None] = mapped_column(Integer)
    total_flat: Mapped[int | None] = mapped_column(Integer)
    limit_up: Mapped[int | None] = mapped_column(Integer)
    limit_down: Mapped[int | None] = mapped_column(Integer)
    total_amount: Mapped[float | None] = mapped_column(Float)
    data_source: Mapped[str | None] = mapped_column(String(32))
    collected_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    data_quality_status: Mapped[str] = mapped_column(String(16), default="OK")

    __table_args__ = (Index("idx_breadth_date", "trading_date"),)
