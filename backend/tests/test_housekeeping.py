"""数据保留 / 清理 / 备份测试（P0 扩展）。

核心验证：P1 建表前清理为 no-op；备份生成有效 gzip；日志兜底清理；磁盘检查。
"""
import gzip
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import retention
from app.config import Settings, get_settings
from scripts import db_backup


def _tmp_settings(tmp_path: Path) -> Settings:
    s = get_settings(force_reload=True)
    s.paths.sqlite_path_abs = tmp_path / "etf_monitor.db"
    s.paths.backup_dir_abs = tmp_path / "backups"
    s.paths.log_dir_abs = tmp_path / "logs"
    return s


def test_disk_check():
    res = retention.check_disk_usage("/workspace", 85)
    assert "used_percent" in res and isinstance(res["used_percent"], float)
    assert res["warn"] in (True, False)


def test_prune_noop_when_db_missing(tmp_path):
    s = _tmp_settings(tmp_path)  # db 文件不存在
    out = retention.run_retention(s)
    assert out.get("skipped") is True


def test_prune_noop_when_table_missing(tmp_path):
    s = _tmp_settings(tmp_path)
    # 建一个空 sqlite（无 market_quote 表）
    sqlite3.connect(str(s.paths.sqlite_path_abs)).close()
    out = retention.run_retention(s)
    assert out.get("deleted_snapshot", 0) == 0
    assert out.get("deleted_bar", 0) == 0


def test_cleanup_old_logs(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    now = time.time()
    (log_dir / "app.log").write_text("current")           # 当前日志，永不清
    old = log_dir / "app.log.2020-01-01"
    old.write_text("old")
    os.utime(old, (now, now - 100 * 86400))               # 100 天前
    future = log_dir / "app.log.2099-01-01"
    future.write_text("future")
    os.utime(future, (now, now))                          # 今天

    removed = retention.cleanup_old_logs(log_dir, retention_days=10)
    assert removed == 1
    assert (log_dir / "app.log").exists()
    assert not old.exists()
    assert future.exists()


def test_backup_creates_valid_gzip(tmp_path):
    s = _tmp_settings(tmp_path)
    # 造一个真实 sqlite
    con = sqlite3.connect(str(s.paths.sqlite_path_abs))
    con.execute("CREATE TABLE t(x INTEGER)")
    con.execute("INSERT INTO t VALUES (1)")
    con.commit()
    con.close()

    gz = db_backup.run_backup(s)
    assert gz is not None and gz.exists()
    # 校验是有效 gzip 且内容含原表
    with gzip.open(gz, "rb") as f:
        data = f.read()
    assert b"CREATE TABLE t" in data
    # 未压缩中间文件应已删除
    assert not (s.paths.backup_dir_abs / "etf_monitor_20200101.db").exists()


def test_backup_retain_local_prunes_old(tmp_path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    now = time.time()
    fresh = backup_dir / "etf_monitor_20260101.db.gz"
    fresh.write_bytes(b"x")
    os.utime(fresh, (now, now))
    old = backup_dir / "etf_monitor_20200101.db.gz"
    old.write_bytes(b"x")
    os.utime(old, (now, now - 100 * 86400))
    removed = db_backup._retain_local(backup_dir, retention_days=30)
    assert removed == ["etf_monitor_20200101.db.gz"]
    assert fresh.exists()
    assert not old.exists()
