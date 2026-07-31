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
from app.data_provider import akshare_adapter
from app.config import get_settings
from app.db import make_engine, session_scope
from app.db.lock import db_writer_lock
from app.evaluation.pipeline import post_collection_evaluate
from app.logging_conf import get_logger, setup_logging
from app.data_provider import gtimg_client
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
        # gtimg 为 CVM 不封 IP 的可靠实时源（C2），注入为附加实时快照采集器；
        # 仅在 collect_market 末尾触发，失败静默降级，不影响 em/sina 主采集。
        _COLLECTOR = Collector(
            build_provider(s), s,
            gtimg_fetcher=gtimg_client.fetch_realtime,
            us_index_fetcher=gtimg_client.fetch_us_indices,
            gtimg_intraday_fetcher=gtimg_client.fetch_intraday_minute,
            us_index_history_fetcher=akshare_adapter.get_us_index_history,
        )
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


def run_write_job(name: str, fn, engine, *args, **kwargs) -> None:
    """写库任务包装（#111）：先尝试获取跨进程写锁(非阻塞)。

    被手动 `run_evaluate --backfill` 占用写锁时跳过本轮、下个周期重试，绝不报
    database is locked，也绝不因并发写而损坏数据。读库类任务不要用本包装。
    """
    log = get_logger("etf-worker.job")
    try:
        with db_writer_lock(get_settings(), blocking=False) as acquired:
            if not acquired:
                log.warning(
                    "write job skipped: db_writer_lock busy (manual run_evaluate running?)",
                    extra={"job": name},
                )
                return
            with session_scope(engine) as session:
                run_job(name, fn, session, *args, **kwargs)
    except Exception as e:  # noqa: BLE001 - 锁文件异常等兜底，不影响调度器
        log.error("write job wrapper failed", exc_info=e, extra={"job": name})


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
        get_logger("etf-worker.job").debug("collect_market skipped: not trading now")
        return
    run_write_job("collect_market", _collector().collect_market, _engine())


def job_collect_intraday_minute() -> None:
    """盘中 1 分钟分时采集（sina stock_zh_a_minute）。非交易时段跳过。"""
    from app.market_calendar import is_trading_now

    if not is_trading_now():
        get_logger("etf-worker.job").debug("intraday_minute skipped: not trading now")
        return
    run_write_job("collect_intraday_minute", _collector().collect_intraday_minute, _engine())


def job_collect_sector_westock() -> None:
    """板块异动（腾讯自选股 westock-data）低频采集：落库 SECTOR BAR 供引擎信号。

    非交易时段跳过。npx 较慢，间隔 settings.scheduler.sector_westock_interval_seconds（默认 900s）。
    """
    from app.market_calendar import is_trading_now, trading_date_for

    if not is_trading_now():
        get_logger("etf-worker.job").debug("sector westock skipped: not trading now")
        return
    c = _collector()
    # sector_codes 来自读查询，不在写锁内；写操作整体置于写锁下（被手动 run_evaluate 占用则跳过本轮）
    with db_writer_lock(get_settings(), blocking=False) as acquired:
        if not acquired:
            get_logger("etf-worker.job").warning(
                "sector_westock skipped: db_writer_lock busy (manual run_evaluate running?)",
                extra={"job": "sector_westock"},
            )
            return
        with session_scope(_engine()) as session:
            sector_codes = c._sector_codes(session, trading_date_for())
            run_job("sector_westock", c.collect_sector_from_westock, session, sector_codes, trading_date_for())


def job_collect_breadth() -> None:
    """全市场宽度累计（每日数次：午间 + 收盘）。非交易日跳过。"""
    from app.market_calendar import is_trading_day, trading_date_for

    if not is_trading_day(trading_date_for()):
        return
    run_write_job("collect_breadth", _collector().collect_breadth, _engine())


def job_pre_market() -> None:
    """盘前：刷新交易日历 + 预热采集。非交易日跳过。"""
    from app.market_calendar import is_trading_day, trading_date_for

    try:
        market_calendar.refresh_calendar(_collector().provider)
    except Exception as e:  # noqa: BLE001
        get_logger(__name__).warning("calendar refresh failed", extra={"err": str(e)})
    if not is_trading_day(trading_date_for()):
        return
    run_write_job("pre_market_prepare", _collector().collect_market, _engine())


def _post_close_pipeline(session, settings, *, collect_snapshot: bool = True):
    """同一写锁/Session 内顺序执行收盘数据链，再生成意见。"""
    result = {}
    if collect_snapshot:
        result["collect"] = _collector().collect_all(session)
    result["backfill"] = _collector().backfill_history(session)
    result["evaluate"] = post_collection_evaluate(session, settings, phase="post_close")
    return result


def job_post_close() -> None:
    """15:15 收盘主流水线：最终快照 -> 刷新日线 -> 评估；非交易日跳过。"""
    from app.market_calendar import is_trading_day, trading_date_for

    if not is_trading_day(trading_date_for()):
        get_logger("etf-worker.job").debug("post_close skipped: not trading day")
        return
    run_write_job("post_close_pipeline", _post_close_pipeline, _engine(), get_settings())


# --------------------------------------------------------------------------- #
# P3 评估任务（采集后评估 + 历史回填；均先查交易日历守卫）
# --------------------------------------------------------------------------- #
def job_backfill_history() -> None:
    """手动/兼容入口：刷新历史 BAR 的最后一个已存交易日起的数据。"""
    run_write_job("backfill_history", _collector().backfill_history, _engine())


def job_pre_close_evaluate() -> None:
    """收盘前评估（14:50）：生成 pre_close 阶段意见。非交易日跳过。"""
    from app.market_calendar import is_trading_day, trading_date_for

    if not is_trading_day(trading_date_for()):
        return
    run_write_job("pre_close_evaluate", post_collection_evaluate, _engine(), get_settings(), phase="pre_close")


def job_post_close_finalize() -> None:
    """16:30 二次定稿：刷新数据源晚到/修订的日线，再覆盖同日 post_close 意见。"""
    from app.market_calendar import is_trading_day, trading_date_for

    if not is_trading_day(trading_date_for()):
        return
    run_write_job(
        "post_close_finalize",
        _post_close_pipeline,
        _engine(),
        get_settings(),
        collect_snapshot=False,
    )


def job_intraday_signal() -> None:
    """盘中实时信号（每 5 分钟）：生成 live 阶段意见（即「最新信号」盘中即时判断）。非交易时段跳过。"""
    from app.market_calendar import is_trading_now

    if not is_trading_now():
        get_logger("etf-worker.job").debug("intraday_signal skipped: not trading now")
        return
    run_write_job("intraday_signal", post_collection_evaluate, _engine(), get_settings(), phase="live")


def job_lunch_opinion() -> None:
    """午盘意见（11:40）：午休后生成 lunch 阶段意见（上午分时+量能+板块）。非交易日跳过。"""
    from app.market_calendar import is_trading_day, trading_date_for

    if not is_trading_day(trading_date_for()):
        return
    run_write_job("lunch_opinion", post_collection_evaluate, _engine(), get_settings(), phase="lunch")


# --------------------------------------------------------------------------- #
# P7 回测任务（收盘后 15:40 或手动触发；盘中由 API 端拒重型回测）
# --------------------------------------------------------------------------- #
def job_run_backtest() -> None:
    """取全部 PENDING 回测任务执行（异步，避免与采集竞争 CPU/内存，DESIGN §异步回测）。"""
    from app.backtest_engine.runner import process_pending_backtests

    run_write_job("run_backtest", process_pending_backtests, _engine(), get_settings())


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
    # 盘中分时采集（每 intraday_minute_interval_seconds；is_trading_now 守卫）
    scheduler.add_job(
        job_collect_intraday_minute, "interval", seconds=settings.scheduler.intraday_minute_interval_seconds,
        id="intraday_minute_collect", replace_existing=True, max_instances=1, coalesce=True,
    )
    # 板块异动（腾讯自选股 westock-data，npx 较慢）低频采集（每 sector_westock_interval_seconds；is_trading_now 守卫）
    scheduler.add_job(
        job_collect_sector_westock, "interval", seconds=settings.scheduler.sector_westock_interval_seconds,
        id="sector_westock_collect", replace_existing=True, max_instances=1, coalesce=True,
    )
    # 午间宽度累计 11:35
    scheduler.add_job(
        job_collect_breadth, CronTrigger(hour=11, minute=35),
        id="midday_breadth", replace_existing=True, max_instances=1, coalesce=True,
    )
    # 收盘主流水线 15:15：同一写锁内严格按 最终快照 -> 日线刷新 -> 评估 顺序执行。
    scheduler.add_job(
        job_post_close, CronTrigger(hour=15, minute=15),
        id="post_close_pipeline", replace_existing=True, max_instances=1, coalesce=True,
    )
    # ---- P3 评估任务 ----
    # 盘中实时信号（每 5 分钟，is_trading_now 守卫 -> live 阶段 = 「最新信号」盘中即时判断，C23）
    scheduler.add_job(
        job_intraday_signal, "interval", seconds=settings.scheduler.intraday_signal_interval_seconds,
        id="intraday_signal", replace_existing=True, max_instances=1, coalesce=True,
    )
    # 午盘意见（11:40，午休后 -> lunch 阶段，可留历史，C23）
    scheduler.add_job(
        job_lunch_opinion, CronTrigger(hour=11, minute=40),
        id="lunch_opinion", replace_existing=True, max_instances=1, coalesce=True,
    )
    # 16:30 二次定稿：刷新晚到/修订日线后重新评估。
    scheduler.add_job(
        job_post_close_finalize, CronTrigger(hour=16, minute=30),
        id="post_close_finalize", replace_existing=True, max_instances=1, coalesce=True,
    )
    # 收盘前评估 14:50（pre_close 阶段意见；收盘前 10 分钟，方便客户操作）
    scheduler.add_job(
        job_pre_close_evaluate, CronTrigger(hour=14, minute=50),
        id="pre_close_evaluate", replace_existing=True, max_instances=1, coalesce=True,
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
        # 记日志：日历是否加载、覆盖到哪天——若 last_day 早于今天，说明日历陈旧，
        # is_trading_day 会自动回退启发式（周一~周五），但仍值得关注。
        last_day = market_calendar.calendar_last_day()
        if last_day:
            log.info("trade calendar loaded; last covered day = %s", last_day)
        else:
            log.warning("trade calendar NOT loaded; using heuristic fallback (Mon-Fri)")
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
