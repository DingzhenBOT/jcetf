"""映射与策略模型：etf_mapping（版本化）+ strategy_version（不可覆盖）。"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utcnow


class EtfMapping(Base):
    """ETF -> 关联板块/指数 映射。修改生成新 mapping_version，旧映射不覆盖；

    回测按 valid_from/valid_to 取当时生效映射，避免用今天映射回测历史。
    """

    __tablename__ = "etf_mapping"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    etf_code: Mapped[str] = mapped_column(String(32), nullable=False)
    etf_name: Mapped[str | None] = mapped_column(String(128))
    related_sector_codes: Mapped[list | None] = mapped_column(JSON)  # JSON 数组
    related_index_code: Mapped[str | None] = mapped_column(String(32))
    category: Mapped[str | None] = mapped_column(String(32))
    listing: Mapped[str | None] = mapped_column(String(8))  # '场内' / '场外'
    is_active: Mapped[int] = mapped_column(Integer, default=1)
    mapping_version: Mapped[str] = mapped_column(String(32), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("uq_etf_mapping_ver", "etf_code", "mapping_version", unique=True),
        Index("idx_mapping_active", "is_active"),
        Index("idx_mapping_code", "etf_code"),
    )


class StrategyVersion(Base):
    """策略不可覆盖版本。strategy_hash = SHA256(规则JSON + 参数JSON)。

    内容变化必然产生新版本；插入前查重，旧版本禁止改写（DESIGN §9.3）。
    """

    __tablename__ = "strategy_version"

    version: Mapped[str] = mapped_column(String(64), primary_key=True)  # v1.0.0-a83f29
    strategy_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str | None] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)
    params_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    rules_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (Index("idx_strategy_hash", "strategy_hash"),)
