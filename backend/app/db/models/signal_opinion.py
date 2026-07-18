"""信号与意见模型：signal（公共信号）+ opinion（盘中/复盘意见）。"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, Float, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utcnow


class Signal(Base):
    """确定性规则引擎产出的公共信号（无持仓时的建议档位）。

    关联不可覆盖 strategy_version；所有字段由规则确定性填充。
    """

    __tablename__ = "signal"

    signal_id: Mapped[str] = mapped_column(String(64), primary_key=True)  # uuid
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    trading_date: Mapped[date] = mapped_column(Date, nullable=False)
    target_etf: Mapped[str] = mapped_column(String(32), nullable=False)
    signal_type: Mapped[str] = mapped_column(String(32), nullable=False)  # 9.4 档位
    score: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    market_regime: Mapped[str | None] = mapped_column(String(16))
    triggered_rules: Mapped[dict | None] = mapped_column(JSON)
    failed_rules: Mapped[dict | None] = mapped_column(JSON)
    supporting_metrics: Mapped[dict | None] = mapped_column(JSON)
    risk_flags: Mapped[dict | None] = mapped_column(JSON)
    invalidation_conditions: Mapped[dict | None] = mapped_column(JSON)
    suggested_action: Mapped[str | None] = mapped_column(String(64))
    suggested_position_range: Mapped[dict | None] = mapped_column(JSON)
    review_time: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (
        Index("idx_signal_etf_time", "target_etf", "generated_at"),
        Index("idx_signal_strategy", "strategy_version"),
        Index("idx_signal_trade", "trading_date"),
    )


class Opinion(Base):
    """模板化生成的盘中/复盘意见（LLM 仅润色，不判断，DESIGN §0）。"""

    __tablename__ = "opinion"

    opinion_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    signal_id: Mapped[str | None] = mapped_column(String(64))
    generated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    trading_date: Mapped[date] = mapped_column(Date, nullable=False)
    phase: Mapped[str | None] = mapped_column(String(32))  # pre_market/midday/pre_close/post_close
    title: Mapped[str | None] = mapped_column(String(256))
    content: Mapped[str | None] = mapped_column(Text)
    input_summary: Mapped[dict | None] = mapped_column(JSON)
    template_version: Mapped[str | None] = mapped_column(String(32), default="template-v1")
    model_version: Mapped[str | None] = mapped_column(String(32))  # 预留（真实 LLM 时填）

    __table_args__ = (
        Index("idx_opinion_time", "generated_at"),
        Index("idx_opinion_trade", "trading_date"),
    )
