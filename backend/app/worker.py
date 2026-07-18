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

from app import retention
from app.config import get_settings
from app.logging_conf import get_logger, setup_logging
from scripts.db_backup import run_backup

LOCK_FILE_NAME = ".etf_worker.lock"


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
    log.info(
        "etf-worker started",
        extra={"timezone": settings.scheduler.timezone, "enabled": settings.scheduler.enabled},
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
