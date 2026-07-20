"""回测表模型（P7 新增，非 P1 八张核心表，DESIGN §5.5）。

`backtest_run`：一次回测任务（状态机 PENDING→RUNNING→DONE/FAILED）+ 参数 + 结果。
`backtest_trade`：回测产生的逐笔交易（仅记录，不持久化真实成交）。

设计约束：
- 时间统一 naive UTC（见 db/base.utcnow）。
- `backtest_run.params_json` 存可调回测参数（initial_capital / in_sample_end 等）；
  strategy_version / start_date / end_date / benchmark 单独成列便于查询与审计。
- `backtest_trade.sample` ∈ {IN, OUT, FULL}：样本内/外标记（R5 分离依据）。
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utcnow


class BacktestRun(Base):
    """一次回测任务（异步，Worker 执行，DESIGN §异步回测）。"""

    __tablename__ = "backtest_run"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # uuid hex
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    # PENDING / RUNNING / DONE / FAILED
    progress: Mapped[int] = mapped_column(Integer, default=0)  # 0-100
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    params_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    benchmark: Mapped[str] = mapped_column(String(32), nullable=False)
    results_json: Mapped[dict | None] = mapped_column(JSON)
    trades_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_by: Mapped[str | None] = mapped_column(String(64))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (
        Index("idx_backtest_status", "status"),
        Index("idx_backtest_created", "created_at"),
    )


class BacktestTrade(Base):
    """回测逐笔交易（仅回测结果，非真实成交，DESIGN §5.5）。"""

    __tablename__ = "backtest_trade"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    backtest_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    etf_code: Mapped[str] = mapped_column(String(32), nullable=False)
    # R5 样本内/外标记：IN / OUT / FULL
    sample: Mapped[str] = mapped_column(String(8), nullable=False, default="FULL")
    entry_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    exit_time: Mapped[datetime | None] = mapped_column(DateTime)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_price: Mapped[float | None] = mapped_column(Float)
    qty: Mapped[float] = mapped_column(Float, nullable=False)
    pnl: Mapped[float | None] = mapped_column(Float)
    pnl_percent: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(String(64))  # 入场信号档位

    __table_args__ = (
        Index("idx_backtest_trade_run", "backtest_run_id"),
        Index("idx_backtest_trade_sample", "backtest_run_id", "sample"),
    )
