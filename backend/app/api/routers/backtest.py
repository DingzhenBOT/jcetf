"""回测路由（P7，DESIGN §异步回测）。

- POST /api/backtest/run  ：仅建 PENDING 任务，立即返回 id（**不同步执行**，Worker 收盘后跑）。
                          盘中默认拒重型回测（settings.backtest.intraday_heavy_disabled
                          + is_trading_now）-> 409。
- GET  /api/backtest/{id} ：查进度（status/progress）+ 完成后返回指标/交易/净值曲线。
- GET  /api/backtest/runs ：回测任务列表。

鉴权在 Nginx（DESIGN §0），本进程无鉴权层。
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_backtest_db
from app.api.schemas import (
    BacktestRunOut,
    BacktestRunRequest,
    BacktestRunsList,
    BacktestTradeOut,
)
from app.config import get_settings
from app.db.models.backtest import BacktestRun
from app.db.session import Session
from app.errors import ValidationError
from app.market_calendar import is_trading_now
from app.repository import backtest_repo, mapping_repo

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


def _run_to_dict(run: BacktestRun) -> BacktestRunOut:
    results = None
    if run.results_json:
        rj = run.results_json
        results = {
            "in_sample": rj["in_sample"],
            "out_of_sample": rj["out_of_sample"],
            "full": rj["full"],
            "benchmark": rj.get("benchmark", {}),
            "params": rj.get("params", {}),
            "data_availability": rj.get("data_availability"),
            "notes": rj.get("notes", []),
        }
    return BacktestRunOut(
        id=run.id,
        strategy_version=run.strategy_version,
        status=run.status,
        progress=run.progress,
        start_date=run.start_date.isoformat(),
        end_date=run.end_date.isoformat(),
        benchmark=run.benchmark,
        params=run.params_json or {},
        trades_count=run.trades_count,
        created_at=run.created_at.isoformat() if run.created_at else "",
        created_by=run.created_by,
        finished_at=run.finished_at.isoformat() if run.finished_at else None,
        results=results,
        error_message=getattr(run, "error_message", None),
    )


@router.post("/run", response_model=BacktestRunOut, status_code=202)
def create_backtest(req: BacktestRunRequest, session: Session = Depends(get_backtest_db)):
    """建回测任务（PENDING）。盘中拒重型回测 -> 409。不同步执行。"""
    settings = get_settings()

    # 日期解析
    try:
        sd = date.fromisoformat(req.start_date)
        ed = date.fromisoformat(req.end_date)
    except ValueError:
        raise ValidationError(f"invalid date: start_date={req.start_date!r} end_date={req.end_date!r} (expected YYYY-MM-DD)")
    if sd >= ed:
        raise ValidationError(f"start_date must be < end_date (got {sd} >= {ed})")

    # 标的必须在 etf_mapping 白名单（与持仓分析一致）
    allowed = {m.etf_code for m in mapping_repo.get_active_mappings(session)}
    if req.etf_code not in allowed:
        raise ValidationError(
            f"etf_code not in whitelist: {req.etf_code!r}",
            details={"not_allowed": [req.etf_code]},
        )

    # 策略版本白名单：缺省用当前冻结版本；指定则必须已注册
    version = req.strategy_version
    if version is None:
        from app.strategy_versioning import current_strategy_version

        version, _ = current_strategy_version(settings)
    from app.db.models.mapping import StrategyVersion

    if session.get(StrategyVersion, version) is None:
        raise ValidationError(
            f"strategy_version not registered: {version!r} (白名单约束，不可现场编造)",
            details={"strategy_version": version},
        )

    benchmark = req.benchmark or settings.backtest.baseline_etf
    in_sample_end = req.in_sample_end
    if in_sample_end is not None:
        try:
            date.fromisoformat(in_sample_end)
        except ValueError:
            raise ValidationError(f"invalid in_sample_end: {in_sample_end!r} (expected YYYY-MM-DD)")

    # 盘中拒重型回测（避免与采集竞争 CPU/内存，DESIGN §异步回测）
    if settings.backtest.intraday_heavy_disabled and is_trading_now():
        from app.errors import ConflictError

        raise ConflictError(
            "heavy backtest blocked during trading hours; will run after close (15:40) via Worker",
            code="BACKTEST_INTRADAY_BLOCKED",
        )

    params = {
        "etf_code": req.etf_code,
        "initial_capital": req.initial_capital,
        "in_sample_end": in_sample_end,
    }
    run = backtest_repo.create_run(
        session,
        strategy_version=version,
        start_date=sd,
        end_date=ed,
        benchmark=benchmark,
        params=params,
        created_by="api",
    )
    session.commit()
    session.refresh(run)
    return _run_to_dict(run)


@router.get("/runs", response_model=BacktestRunsList)
def list_backtests(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_backtest_db),
):
    """回测任务列表（降序）。必须在 /{run_id} 之前注册，避免被路径参数捕获。"""
    rows, total = backtest_repo.list_runs(session, limit=limit, offset=offset)
    return BacktestRunsList(items=[_run_to_dict(r) for r in rows], total=total)


@router.get("/{run_id}", response_model=BacktestRunOut)
def get_backtest(run_id: str, session: Session = Depends(get_backtest_db)):
    """查回测进度与结果。不存在 -> 404。"""
    from app.errors import NotFoundError

    run = backtest_repo.get_run(session, run_id)
    if run is None:
        raise NotFoundError(f"backtest run not found: {run_id}")
    return _run_to_dict(run)
