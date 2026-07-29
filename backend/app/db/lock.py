"""跨进程 SQLite 写锁（fcntl 顾问锁，#111）。

问题背景
--------
系统设计为「worker 单实例 = 唯一写者」（DESIGN §0）。但 CVM 上运维会手动跑
`python3.11 -m scripts.run_evaluate --backfill`，它另起一个**独立进程**写同一个
SQLite 库。`run_evaluate --backfill` 把整个回填放进一个事务（collector.backfill_history
单次 session_scope），即手动进程在数分钟内一直独占 SQLite 写锁。SQLite(WAL) 只允许一个
写者：worker 的盘中分时采集每 1 分钟写一次，等 busy_timeout(原 5s) 拿不到锁即
`sqlite3.OperationalError: database is locked`；旧「先清后采」代码又把它放大成数据丢失。

解法
----
用一个共享的 fcntl 顾问锁文件 `db_writer.lock`，让所有写库路径（worker 各 job、手动
run_evaluate）串行化写操作：
- worker 侧 `blocking=False`：拿不到锁（被手动 run_evaluate 占用）就**跳过本轮**，下个
  周期重试，绝不报 database is locked，也绝不因并发写而损坏数据（叠加 #110 先采后清防护）。
- 手动 run_evaluate 侧 `blocking=True`：阻塞等待 worker 写完当前批次（通常亚秒~数秒）后
  独占地写；期间 worker 自动让行。

fcntl 顾问锁在进程退出 / fd 关闭时由内核自动释放，手动进程崩溃也不会永久死锁。

注意：etf-api 常规查询走只读引擎(query_only=ON)，不参与写竞争；仅回测提交那一行走可写
引擎，且稀有 + 盘中已拦截重型回测，依靠 busy_timeout 兜底（见 deps.build_write_engine）。
"""
from __future__ import annotations

import fcntl
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.config import Settings


def _lock_path(settings: Settings) -> Path:
    # 锁文件与 SQLite 库文件**同目录**，确保所有写该库进程指向同一把锁。
    # 优先用 sqlite_path_abs.parent（库文件真实所在目录）；缺失时退化为 data_dir_abs。
    if settings.paths.sqlite_path_abs is not None:
        base = settings.paths.sqlite_path_abs.parent
    elif settings.paths.data_dir_abs is not None:
        base = settings.paths.data_dir_abs
    else:
        base = Path(".")
    return base / "db_writer.lock"


@contextmanager
def db_writer_lock(
    settings: Settings,
    blocking: bool = True,
    timeout: float = 120.0,
) -> Iterator[bool]:
    """跨进程写库互斥。

    blocking=True：阻塞等待获取（手动运维用），超时抛 TimeoutError（调用方应捕获并友好退出）。
    blocking=False：非阻塞立即返回；yield True 表示拿到锁，False 表示被占用（调用方应跳过写）。
    """
    path = _lock_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = open(path, "w")  # noqa: SIM115 - 须持有一支 fd 维持锁，finally 中关闭
    try:
        if blocking:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except (OSError, BlockingIOError):
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"db_writer_lock 超时({timeout}s)未获取，可能另一 run_evaluate 正在运行")
                    time.sleep(0.2)
            acquired = True
        else:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except (OSError, BlockingIOError):
                acquired = False
        try:
            yield acquired
        finally:
            if acquired:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
    finally:
        fd.close()
