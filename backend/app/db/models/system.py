"""系统与运维模型：task_run_log（任务运行记录）+ data_source_status（数据源状态）。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utcnow


class TaskRunLog(Base):
    """定时任务运行记录（幂等/超时/重试/延迟告警的依据，DESIGN §8）。"""

    __tablename__ = "task_run_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_name: Mapped[str] = mapped_column(String(64), nullable=False)
    trigger_type: Mapped[str | None] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # SUCCESS/FAILED/TIMEOUT/SKIPPED
    items_processed: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
    data_delay_seconds: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (Index("idx_task_name_time", "task_name", "started_at"),)


class DataSourceStatus(Base):
    """数据源健康/延迟状态（含 STALE 标记，前端告警，DESIGN §7）。"""

    __tablename__ = "data_source_status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    data_source: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol_type: Mapped[str | None] = mapped_column(String(16))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str | None] = mapped_column(String(16))
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("uq_datasource_status", "data_source", "symbol_type", unique=True),
        Index("idx_datasource", "data_source"),
    )
