"""APScheduler 入口（etf-worker 进程，单实例）。

对齐 DESIGN §0 / §8：所有定时任务（采集、post_collection_evaluate、回测、备份、清理）都在本进程，
单实例运行，避免重复采集/重复写库。
P0：占位 health_heartbeat + 三个 housekeeping 任务（db_backup / data_retention / log_cleanup）。
业务采集/评估任务在 P2+ 挂载。
优雅关闭：捕获 SIGTERM/SIGINT -> scheduler.shutdown(wait=False)。
"""
from __future__ import annotations

import fcntl
import logging
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from app import market_calendar, retention
from app.collector.collector import Collector
from app.data_provider import build_provider
from app.config import get_settings
from app.db import make_engine, session_scope
from app.evaluation.pipeline import post_collection_evaluate
from app.logging_conf import get_logger, setup_logging
from scripts.db_backup import run_backup

LOCK_FILE_NAME = ".etf_worker.lock"

# worker 单实例：引擎/采集器常驻缓存，避免每个任务重复构造
_ENGINE = None
_COLLECTOR = None


def _engine():
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = make_engine(get_settings())
    return _ENGINE


def _collector() -> Collector:
    global _COLLECTOR
    if _COLLECTOR is None:
        s = get_settings()
        _COLLECTOR = Collector(build_provider(s), s)
    return _COLLECTOR


def acquire_single_instance_lock(lock_path: Path):
    """用 fcntl 文件锁保证单实例；已被占用返回 None。"""
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    except OSError as e:
        logging.getLogger("etf-worker.boot").error("cannot open lock file %s: %s", lock_path, e)
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        os.close(fd)
        return None
    return fd


def release_lock(fd) -> None:
    if fd is None:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    except OSError:
        pass


def run_job(name: str, fn, *args, **kwargs) -> None:
    """统一任务包装：捕获异常并记日志，单个任务失败不影响调度器。"""
    log = get_logger("etf-worker.job")
    try:
        fn(*args, **kwargs)
        log.info("job ok", extra={"job": name})
    except Exception as e:  # noqa: BLE001
        log.error("job failed", exc_info=e, extra={"job": name})


def health_heartbeat() -> None:
    """每 5 分钟心跳：记录时间 + 磁盘使用率（超阈值告警，不自动删数据）。"""
    log = get_logger(__name__)
    s = get_settings()
    disk = retention.check_disk_usage(s.paths.data_dir_abs, s.housekeeping.disk_warn_percent)
    log.info("health_heartbeat tick", extra={"ts": datetime.now(timezone.utc).isoformat(), "disk": disk})


def job_db_backup() -> None:
    run_job("db_backup", run_backup, get_settings())


def job_data_retention() -> None:
    run_job("data_retention", retention.run_retention, get_settings())


def job_log_cleanup() -> None:
    s = get_settings()
    run_job("log_cleanup", retention.cleanup_old_logs, s.paths.log_dir_abs, s.housekeeping.log_retention_days)


# --------------------------------------------------------------------------- #
# P2 采集任务（先查 market_calendar 守卫，非交易时段/非交易日跳过）
# --------------------------------------------------------------------------- #
def job_collect_market() -> None:
    """盘中轻量采集：指数 + ETF + 行业 + 概念。非交易时段直接跳过。"""
    from app.market_calendar import is_trading_now

    if not is_trading_now():
        return
    with session_scope(_engine()) as session:
        run_job("collect_market", _collector().collect_market, session)


def job_collect_breadth() -> None:
    """全市场宽度累计（每日数次：午间 + 收盘）。非交易日跳过。"""
    from app.market_calendar import is_trading_day, trading_date_for

    if not is_trading_day(trading_date_for()):
        return
    with session_scope(_engine()) as session:
        run_job("collect_breadth", _collector().collect_breadth, session)


def job_pre_market() -> None:
    """盘前：刷新交易日历 + 预热采集。非交易日跳过。"""
    from app.market_calendar import is_trading_day, trading_date_for

    try:
        market_calendar.refresh_calendar(_collector().provider)
    except Exception as e:  # noqa: BLE001
        get_logger(__name__).warning("calendar refresh failed", extra={"err": str(e)})
    if not is_trading_day(trading_date_for()):
        return
    with session_scope(_engine()) as session:
        run_job("pre_market_prepare", _collector().collect_market, session)


def job_post_close() -> None:
    """收盘复盘：完整采集（含宽度最终累计）。非交易日跳过。"""
    from app.market_calendar import is_trading_day, trading_date_for

    if not is_trading_day(trading_date_for()):
        return
    with session_scope(_engine()) as session:
        run_job("post_close_review", _collector().collect_all, session)


# --------------------------------------------------------------------------- #
# P3 评估任务（采集后评估 + 历史回填；均先查交易日历守卫）
# --------------------------------------------------------------------------- #
def job_backfill_history() -> None:
    """盘后回填历史 BAR（指数/ETF/板块）。增量（按 max(timestamp)+1）；非交易时段也可跑（历史不限于盘中）。"""
    with session_scope(_engine()) as session:
        run_job("backfill_history", _collector().backfill_history, session)


def job_pre_close_evaluate() -> None:
    """收盘前评估（14:59）：生成 pre_close 阶段意见。非交易日跳过。"""
    from app.market_calendar import is_trading_day, trading_date_for

    if not is_trading_day(trading_date_for()):
        return
    with session_scope(_engine()) as session:
        run_job("pre_close_evaluate", post_collection_evaluate, session, get_settings(), phase="pre_close")


def job_post_close_evaluate() -> None:
    """收盘后评估（15:10）：生成 post_close 复盘意见。非交易日跳过。"""
    from app.market_calendar import is_trading_day, trading_date_for

    if not is_trading_day(trading_date_for()):
        return
    with session_scope(_engine()) as session:
        run_job("post_close_evaluate", post_collection_evaluate, session, get_settings(), phase="post_close")


def job_intraday_evaluate() -> None:
    """盘中每小时观望评估（整点触发）：生成 midday 阶段意见。非交易日跳过。"""
    from app.market_calendar import is_trading_day, trading_date_for

    if not is_trading_day(trading_date_for()):
        return
    with session_scope(_engine()) as session:
        run_job("intraday_evaluate", post_collection_evaluate, session, get_settings(), phase="midday")


# --------------------------------------------------------------------------- #
# P7 回测任务（收盘后 15:40 或手动触发；盘中由 API 端拒重型回测）
# --------------------------------------------------------------------------- #
def job_run_backtest() -> None:
    """取全部 PENDING 回测任务执行（异步，避免与采集竞争 CPU/内存，DESIGN §异步回测）。"""
    from app.backtest_engine.runner import process_pending_backtests

    with session_scope(_engine()) as session:
        run_job("run_backtest", process_pending_backtests, session, get_settings())


def build_scheduler(settings) -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone=settings.scheduler.timezone)
    if not settings.scheduler.enabled:
        return scheduler
    # 心跳
    scheduler.add_job(
        health_heartbeat, "interval", minutes=5,
        id="health_heartbeat", replace_existing=True, max_instances=1, coalesce=True,
    )
    # 每日 02:00 备份（sqlite3.backup + gzip + 本地保留 N 天）
    scheduler.add_job(
        job_db_backup, CronTrigger(hour=2, minute=0),
        id="db_backup", replace_existing=True, max_instances=1, coalesce=True,
    )
    # 每日 02:05 日志兜底清理
    scheduler.add_job(
        job_log_cleanup, CronTrigger(hour=2, minute=5),
        id="log_cleanup", replace_existing=True, max_instances=1, coalesce=True,
    )
    # 每日 02:10 数据保留（清理过期快照 + VACUUM）
    scheduler.add_job(
        job_data_retention, CronTrigger(hour=2, minute=10),
        id="data_retention", replace_existing=True, max_instances=1, coalesce=True,
    )
    # ---- P2 采集任务（先查 market_calendar 守卫） ----
    # 盘前准备 08:50：刷新日历 + 预热采集
    scheduler.add_job(
        job_pre_market, CronTrigger(hour=8, minute=50),
        id="pre_market_prepare", replace_existing=True, max_instances=1, coalesce=True,
    )
    # 盘中采集（每 intraday_interval_seconds；内部 is_trading_now 守卫）
    scheduler.add_job(
        job_collect_market, "interval", seconds=settings.scheduler.intraday_interval_seconds,
        id="intraday_collect", replace_existing=True, max_instances=1, coalesce=True,
    )
    # 午间宽度累计 11:35
    scheduler.add_job(
        job_collect_breadth, CronTrigger(hour=11, minute=35),
        id="midday_breadth", replace_existing=True, max_instances=1, coalesce=True,
    )
    # 收盘复盘 15:10（含宽度最终累计）
    scheduler.add_job(
        job_post_close, CronTrigger(hour=15, minute=10),
        id="post_close_review", replace_existing=True, max_instances=1, coalesce=True,
    )
    # ---- P3 评估任务 ----
    # 盘中每小时观望评估（交易时段整点 10/11/13/14，midday 阶段意见）
    scheduler.add_job(
        job_intraday_evaluate, CronTrigger(hour="10,11,13,14", minute=0),
        id="intraday_evaluate", replace_existing=True, max_instances=1, coalesce=True,
    )
    # 历史 BAR 回填 16:30（增量；em-only 板块历史失败非致命）
    scheduler.add_job(
        job_backfill_history, CronTrigger(hour=16, minute=30),
        id="backfill_history", replace_existing=True, max_instances=1, coalesce=True,
    )
    # 收盘前评估 14:50（pre_close 阶段意见；收盘前 10 分钟，方便客户操作）
    scheduler.add_job(
        job_pre_close_evaluate, CronTrigger(hour=14, minute=50),
        id="pre_close_evaluate", replace_existing=True, max_instances=1, coalesce=True,
    )
    # 收盘后评估 15:10（post_close 复盘意见）
    scheduler.add_job(
        job_post_close_evaluate, CronTrigger(hour=15, minute=10),
        id="post_close_evaluate", replace_existing=True, max_instances=1, coalesce=True,
    )
    # ---- P7 回测任务（收盘后 15:40 取 PENDING 执行；盘中由 API 端拒重型回测） ----
    scheduler.add_job(
        job_run_backtest, CronTrigger(hour=15, minute=40),
        id="run_backtest", replace_existing=True, max_instances=1, coalesce=True,
    )
    return scheduler


def main() -> int:
    try:
        settings = get_settings()
    except Exception as e:  # noqa: BLE001 - fail-fast
        logging.getLogger("etf-worker.boot").error("config load failed: %s", e)
        return 1

    setup_logging(settings)
    settings.ensure_dirs()
    log = get_logger(__name__)

    lock_path = settings.paths.data_dir_abs / LOCK_FILE_NAME
    lock_fd = acquire_single_instance_lock(lock_path)
    if lock_fd is None:
        log.error("another etf-worker instance is running; exit", extra={"lock": str(lock_path)})
        return 1

    scheduler = build_scheduler(settings)
    # 启动期尝试加载交易日历（网络不可达则回退启发式，不影响调度）
    try:
        market_calendar.init_calendar(_collector().provider)
    except Exception as e:  # noqa: BLE001
        log.warning("calendar init failed; heuristic fallback", extra={"err": str(e)})
    jobs = [j.id for j in scheduler.get_jobs()]
    log.info(
        "etf-worker started; jobs registered (%d): %s",
        len(jobs), jobs,
        extra={
            "timezone": settings.scheduler.timezone,
            "enabled": settings.scheduler.enabled,
            "jobs_registered": jobs,
        },
    )

    def _handle_signal(signum, _frame):
        log.info("received signal", extra={"signal": signum})
        scheduler.shutdown(wait=False)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        try:
            if scheduler.running:
                scheduler.shutdown(wait=False)
        except Exception:  # noqa: BLE001
            pass
        release_lock(lock_fd)
        log.info("etf-worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
