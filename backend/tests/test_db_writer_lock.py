"""db_writer_lock 跨进程互斥（#111）单测。

核心机制：fcntl 顾问锁（与 worker 单实例锁同模式）。本测试用 multiprocessing 验证
「一个进程持锁期间，另一进程非阻塞获取被拒、阻塞获取会等到释放后才拿到」。
"""
from __future__ import annotations

import multiprocessing
import time

from app.config import get_settings
from app.db.lock import db_writer_lock


def _child_hold(q: "multiprocessing.Queue") -> None:
    """子进程：非阻塞获取写锁并保持约 0.8s，向队列回报是否拿到。"""
    s = get_settings()
    with db_writer_lock(s, blocking=False) as acquired:
        q.put(acquired)
        time.sleep(0.8)


def _setup(tmp_path):
    s = get_settings(force_reload=True)
    # 与 conftest 一致：把库指到临时目录，锁文件随之落在同目录（隔离、不污染真实数据）
    s.paths.sqlite_path_abs = tmp_path / "etf_monitor.db"
    s.ensure_dirs()
    return s


def test_cross_process_exclusion(tmp_path):
    _setup(tmp_path)
    q: "multiprocessing.Queue" = multiprocessing.Queue()
    p = multiprocessing.Process(target=_child_hold, args=(q,))
    p.start()
    # 等子进程确认已持锁
    assert q.get(timeout=10) is True

    # 子进程持锁期间：父进程非阻塞获取应被拒
    with db_writer_lock(get_settings(), blocking=False) as acquired:
        assert acquired is False

    p.join(timeout=10)

    # 子进程释放后：父进程应能拿到
    with db_writer_lock(get_settings(), blocking=False) as acquired:
        assert acquired is True


def test_blocking_waits_for_release(tmp_path):
    _setup(tmp_path)
    q: "multiprocessing.Queue" = multiprocessing.Queue()
    p = multiprocessing.Process(target=_child_hold, args=(q,))
    p.start()
    assert q.get(timeout=10) is True

    # 父进程阻塞获取：应在子进程 0.8s 释放后才拿到（不超时）
    t0 = time.monotonic()
    with db_writer_lock(get_settings(), blocking=True, timeout=10) as acquired:
        assert acquired is True
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.5  # 确实等了一会儿（证明不是瞬间抢到）
    p.join(timeout=10)
