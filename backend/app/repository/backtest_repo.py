"""回测任务仓储（P7，写库）。

- create_run：建 PENDING 任务（POST /api/backtest/run 调用，不执行）。
- get_run / list_runs：查询（GET 端点 / Worker 取 PENDING）。
- save_trades：批量写 backtest_trade。
- set_progress / finish_run：状态机推进（runner 调用）。

全部 SELECT/INSERT/UPDATE，无读端耦合；与只读查询仓储隔离（DESIGN §4 DAG）。
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.db.models.backtest import BacktestRun, BacktestTrade


def create_run(
    session: Session,
    *,
    strategy_version: str,
    start_date: date,
    end_date: date,
    benchmark: str,
    params: Dict,
    created_by: Optional[str] = None,
) -> BacktestRun:
    """建一条 PENDING 回测任务，返回 ORM 行（调用方负责 commit）。"""
    run = BacktestRun(
        id=uuid.uuid4().hex,
        strategy_version=strategy_version,
        status="PENDING",
        progress=0,
        start_date=start_date,
        end_date=end_date,
        params_json=params,
        benchmark=benchmark,
        results_json=None,
        trades_count=0,
        created_at=utcnow(),
        created_by=created_by,
        finished_at=None,
    )
    session.add(run)
    return run


def get_run(session: Session, run_id: str) -> Optional[BacktestRun]:
    return session.get(BacktestRun, run_id)


def list_runs(
    session: Session, limit: int = 50, offset: int = 0
) -> Tuple[List[BacktestRun], int]:
    """回测任务列表（按 created_at desc）。limit 夹紧 [1,200]。"""
    limit = max(1, min(200, int(limit)))
    offset = max(0, int(offset))
    total = session.execute(select(func.count()).select_from(BacktestRun)).scalar_one()
    rows = (
        session.execute(
            select(BacktestRun)
            .order_by(BacktestRun.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return list(rows), total


def save_trades(session: Session, run_id: str, trades: List[Dict]) -> None:
    """批量写回测逐笔交易（trades 为 _compute_backtest 输出dict列表）。"""
    if not trades:
        return
    objs = [
        BacktestTrade(
            backtest_run_id=run_id,
            etf_code=t["etf_code"],
            sample=t.get("sample", "FULL"),
            entry_time=datetime.fromisoformat(t["entry_time"]),
            exit_time=datetime.fromisoformat(t["exit_time"]) if t.get("exit_time") else None,
            entry_price=t["entry_price"],
            exit_price=t.get("exit_price"),
            qty=t["qty"],
            pnl=t.get("pnl"),
            pnl_percent=t.get("pnl_percent"),
            reason=t.get("reason"),
        )
        for t in trades
    ]
    session.bulk_save_objects(objs)


def set_progress(session: Session, run: BacktestRun, progress: int) -> None:
    run.progress = max(0, min(100, int(progress)))
