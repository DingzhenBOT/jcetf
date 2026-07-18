"""数据保留 / 清理 / 磁盘守卫（防磁盘撑爆）。

对齐 DESIGN §0 备份策略与用户诉求：4 核 4GB / 60GB 磁盘，必须有定期清理与关键数据保留。
设计要点：
  - 所有清理按「表存在才动」守卫，P1 建表前为 no-op，可独立测试。
  - 盘中快照(SNAPSHOT)最占空间 -> 热保留窗口短；日线(BAR)/意见保留更长（见 settings.housekeeping）。
  - 清理后可选 VACUUM 回收空间。
  - 日志清理是 TimedRotatingFileHandler 之外的兜底安全网（进程长期关闭时也能回收）。
  - 磁盘使用率超阈值即告警（不自动删业务数据，避免误删）。
  - 时间统一 UTC；prune 用 SQLite `datetime('now','-N day')`，要求 P1 的 timestamp 存「naive UTC ISO」。
"""
from __future__ import annotations

import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict

from sqlalchemy import Engine, create_engine, inspect, text

from app.config import Settings
from app.logging_conf import get_logger

logger = get_logger(__name__)
UTC = timezone.utc


def make_engine(settings: Settings) -> Engine:
    """创建 SQLite 引擎（WAL + busy_timeout），与 P1 引擎保持一致。"""
    engine = create_engine(
        settings.sqlite_url,
        connect_args={"timeout": settings.database.busy_timeout_ms / 1000.0},
        future=True,
    )
    with engine.connect() as conn:
        if settings.database.wal_mode:
            conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        conn.exec_driver_sql(f"PRAGMA busy_timeout={settings.database.busy_timeout_ms}")
    return engine


def prune_market_quotes(engine: Engine, snapshot_days: int, bar_days: int) -> Dict[str, int]:
    """清理 market_quote 中过期的 SNAPSHOT / BAR。表不存在则跳过。

    返回各类型删除行数。timestamp 须为 naive UTC ISO，方可被 SQLite datetime() 解析。
    """
    inspector = inspect(engine)
    if "market_quote" not in inspector.get_table_names():
        return {"deleted_snapshot": 0, "deleted_bar": 0, "skipped": 1}

    deleted: Dict[str, int] = {"deleted_snapshot": 0, "deleted_bar": 0}
    with engine.begin() as conn:
        r = conn.execute(
            text("DELETE FROM market_quote WHERE data_kind='SNAPSHOT' AND timestamp < datetime('now', :cut)"),
            {"cut": f"-{snapshot_days} day"},
        )
        deleted["deleted_snapshot"] = r.rowcount
        r = conn.execute(
            text("DELETE FROM market_quote WHERE data_kind='BAR' AND timestamp < datetime('now', :cut)"),
            {"cut": f"-{bar_days} day"},
        )
        deleted["deleted_bar"] = r.rowcount
    logger.info("pruned market_quote", extra=deleted)
    return deleted


def vacuum(engine: Engine) -> None:
    """VACUUM 必须在 autocommit 下执行，回收清理后的空闲页。"""
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql("VACUUM")
    logger.info("vacuum done")


def run_retention(settings: Settings) -> Dict[str, object]:
    """数据保留主流程：清理 + 可选 VACUUM。DB 不存在则跳过。"""
    h = settings.housekeeping
    if h.disabled:
        logger.info("housekeeping disabled; skip retention")
        return {"skipped": True}
    db_path = settings.paths.sqlite_path_abs
    if db_path is None or not db_path.exists():
        logger.info("db not found; skip retention", extra={"db": str(db_path)})
        return {"skipped": True, "reason": "db_not_found"}

    engine = make_engine(settings)
    try:
        result: Dict[str, object] = dict(
            prune_market_quotes(engine, h.snapshot_retention_days, h.bar_retention_days)
        )
        if h.vacuum_after_prune:
            vacuum(engine)
            result["vacuum"] = True
        return result
    finally:
        engine.dispose()


def cleanup_old_logs(log_dir: Path, retention_days: int) -> int:
    """兜底清理过期日志文件（不删当前 app.log）。"""
    log_dir = Path(log_dir)
    if not log_dir.exists():
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    removed = 0
    for f in log_dir.iterdir():
        if not f.is_file() or not f.name.startswith("app.log"):
            continue
        if f.name == "app.log":  # 当前日志永不清
            continue
        mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=UTC)
        if mtime < cutoff:
            try:
                f.unlink()
                removed += 1
            except OSError as e:
                logger.warning("failed to remove old log %s: %s", f, e)
    if removed:
        logger.info("cleaned old logs", extra={"removed": removed, "retention_days": retention_days})
    return removed


def check_disk_usage(path, warn_percent: int) -> Dict[str, float]:
    """返回路径所在文件系统的使用率；超阈值 warn=True。"""
    p = Path(path)
    check_path = p if p.exists() else (p.parent if p.parent.exists() else Path("/"))
    usage = shutil.disk_usage(str(check_path))
    used_pct = usage.used / usage.total * 100.0
    result = {
        "path": str(check_path),
        "total_bytes": float(usage.total),
        "used_bytes": float(usage.used),
        "free_bytes": float(usage.free),
        "used_percent": round(used_pct, 2),
        "warn": bool(used_pct >= warn_percent),
    }
    if result["warn"]:
        logger.warning(
            "disk usage high",
            extra={"used_percent": result["used_percent"], "warn_percent": warn_percent, "path": result["path"]},
        )
    return result
