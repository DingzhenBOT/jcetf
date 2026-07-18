"""SQLite 备份脚本（防磁盘损坏；见 DESIGN §0 / §8）。

- 用 `sqlite3.connect().backup()`（等同 CLI `.backup`，但不依赖 sqlite3 命令是否安装）。
- 备份后 gzip 压缩；本地保留最近 N 天（housekeeping.backup_retention_days）。
- 异地周备由 backup_remote_enabled 控制（P8 落地；当前为占位 hook）。
- 既可作为 CLI 定时运行，也可被 worker 的 db_backup 任务 import 调用。

用法：
  python3.11 backend/scripts/db_backup.py [--config <path>]
  （config 默认读 ETF_CONFIG_PATH 或 /workspace/config/settings.yaml）
"""
from __future__ import annotations

import argparse
import gzip
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from app.config import Settings, get_settings
from app.logging_conf import get_logger

logger = get_logger(__name__)


def _upload_remote(gz_path: Path, settings: Settings) -> None:
    """异地周备占位（P8 落地：rclone / 对象存储 CLI）。

    默认不启用；启用但未实现时明确告警，不静默失败。
    """
    logger.warning(
        "backup_remote_enabled but remote upload not implemented (P8); kept local only",
        extra={"file": str(gz_path)},
    )


def _retain_local(backup_dir: Path, retention_days: int) -> List[str]:
    """删除超过保留天数的本地备份（按文件名日期排序，保留最新 N 个）。"""
    backups = sorted(backup_dir.glob("etf_monitor_*.db.gz"), key=lambda p: p.name)
    # 由于每天一个，按数量等价于按天；更稳妥按 mtime 判断
    cutoff = datetime.now(timezone.utc).timestamp() - retention_days * 86400
    removed: List[str] = []
    for f in backups:
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                removed.append(f.name)
        except OSError as e:
            logger.warning("failed to remove old backup %s: %s", f, e)
    if removed:
        logger.info("pruned old backups", extra={"removed": removed})
    return removed


def run_backup(settings: Settings) -> Optional[Path]:
    """执行一次备份，返回压缩后的备份文件路径；无 DB 则返回 None。"""
    h = settings.housekeeping
    db_path = settings.paths.sqlite_path_abs
    if db_path is None or not db_path.exists():
        logger.info("db not found; skip backup", extra={"db": str(db_path)})
        return None

    backup_dir = settings.paths.backup_dir_abs
    assert backup_dir is not None
    backup_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d")
    raw_path = backup_dir / f"etf_monitor_{ts}.db"
    gz_path = backup_dir / f"etf_monitor_{ts}.db.gz"

    # 用 sqlite3 备份 API，保证一致性（WAL 下也安全）
    src = sqlite3.connect(str(db_path))
    try:
        if gz_path.exists():
            # 同日重复运行：覆盖（先删旧压缩包）
            gz_path.unlink()
        # 先落到未压缩文件，再 gzip，避免半截压缩包
        dst = sqlite3.connect(str(raw_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    with open(raw_path, "rb") as f_in, gzip.open(gz_path, "wb") as f_out:
        f_out.writelines(f_in)
    raw_path.unlink()  # 只保留压缩包，省空间

    logger.info("backup complete", extra={"file": str(gz_path), "size_bytes": gz_path.stat().st_size})
    _retain_local(backup_dir, h.backup_retention_days)
    if h.backup_remote_enabled:
        _upload_remote(gz_path, settings)
    return gz_path


def main() -> int:
    parser = argparse.ArgumentParser(description="SQLite backup for etf-monitor")
    parser.add_argument("--config", default=os.environ.get("ETF_CONFIG_PATH"), help="path to settings.yaml")
    args = parser.parse_args()
    settings = get_settings(config_path=args.config)
    from app.logging_conf import setup_logging

    setup_logging(settings)
    settings.ensure_dirs()
    try:
        run_backup(settings)
        return 0
    except Exception as e:  # noqa: BLE001
        logger.error("backup failed", exc_info=e)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
