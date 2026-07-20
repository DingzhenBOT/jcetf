"""回测执行器（P7，状态机 + Worker 入口）。

`run_backtest(session, run, settings)`：把一条 PENDING 任务推进到 DONE/FAILED。
  PENDING -> RUNNING（标记）-> 计算 -> 写交易 + results_json -> DONE（progress=100）
  任何异常 -> FAILED（记录 error_message），不污染其他任务。

`process_pending_backtests(session, settings)`：Worker `run_backtest` 任务调用，扫描全部
PENDING 逐条执行（DESIGN §异步回测：API 仅建任务，Worker 收盘后跑）。

约束：策略参数来自 run.strategy_version（不可覆盖白名单）；数据不足 / 版本不存在 -> FAILED。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.backtest_engine.backtester import _compute_backtest
from app.config import Settings
from app.db.base import utcnow
from app.db.models.backtest import BacktestRun
from app.db.models.mapping import StrategyVersion
from app.repository import backtest_repo


def _parse_params(run: BacktestRun) -> Dict[str, Any]:
    """从 run 行抽取回测参数（专用列 + params_json）。"""
    p = run.params_json or {}
    return {
        "etf_code": p["etf_code"],
        "start_date": run.start_date,
        "end_date": run.end_date,
        "initial_capital": float(p.get("initial_capital", 100000.0)),
        "benchmark": run.benchmark,
        "strategy_version": run.strategy_version,
        "in_sample_end": date.fromisoformat(p["in_sample_end"]) if p.get("in_sample_end") else None,
    }


def run_backtest(session: Session, run: BacktestRun, settings: Settings) -> None:
    """执行单条回测任务（状态机推进）。异常时标记 FAILED 并上抛由调用方决定回滚。"""
    if run.status not in ("PENDING", "RUNNING"):
        return  # 幂等：非待执行状态直接跳过

    # 白名单校验：strategy_version 必须已注册（不可现场编造）
    version_row = session.get(StrategyVersion, run.strategy_version)
    if version_row is None:
        run.status = "FAILED"
        run.error_message = f"strategy_version not found: {run.strategy_version} (白名单约束，不可现场编造)"
        run.finished_at = utcnow()
        session.commit()
        return

    params = _parse_params(run)
    run.status = "RUNNING"
    run.progress = 0
    session.commit()

    try:
        results, trades = _compute_backtest(
            session,
            etf_code=params["etf_code"],
            start_date=params["start_date"],
            end_date=params["end_date"],
            initial_capital=params["initial_capital"],
            benchmark=params["benchmark"],
            strategy_version=params["strategy_version"],
            in_sample_end=params["in_sample_end"],
            settings=settings,
            run=run,  # 周期提交进度
        )
        backtest_repo.save_trades(session, run.id, trades)
        run.results_json = results
        run.trades_count = len(trades)
        run.progress = 100
        run.status = "DONE"
        run.finished_at = utcnow()
        session.commit()
    except Exception as e:  # noqa: BLE001 - 标记失败，不中断其他任务
        session.rollback()
        run.status = "FAILED"
        run.error_message = f"{type(e).__name__}: {e}"
        run.finished_at = utcnow()
        run.progress = 0
        session.commit()
        raise


def process_pending_backtests(session: Session, settings: Settings) -> int:
    """扫描并执行全部 PENDING 回测任务，返回成功执行条数。单个失败不影响其他。"""
    from sqlalchemy import select

    pending = (
        session.execute(
            select(BacktestRun).where(BacktestRun.status == "PENDING")
        )
        .scalars()
        .all()
    )
    done = 0
    for run in pending:
        try:
            run_backtest(session, run, settings)
            if run.status == "DONE":
                done += 1
        except Exception:  # noqa: BLE001 - 单任务失败已落 FAILED，继续下一个
            continue
    return done
