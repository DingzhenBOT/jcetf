"""API 依赖注入（P4）。

- build_read_engine：为 etf-api 进程创建**只读** SQLite 引擎（WAL + busy_timeout
  沿用 make_engine；额外 PRAGMA query_only=ON 兜底，杜绝误写）。etf-api 是独立进程，
  与 etf-worker 共享同一 SQLite 文件（WAL 允许并发读）。
- get_db：FastAPI 依赖，从 app.state.db_factory 取 session；测试用
  app.dependency_overrides[get_db] 切到临时库。
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from fastapi import Request
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.session import make_engine, make_session_factory
from app.errors import UnavailableError


def build_read_engine(settings: Settings) -> Engine:
    """创建只读引擎：WAL + busy_timeout（来自 make_engine）+ query_only 兜底。"""
    eng = make_engine(settings)
    if settings.database.wal_mode:

        @event.listens_for(eng, "connect")
        def _read_only(dbapi_con, _record):  # noqa: ANN001 - DBAPI 连接，无类型
            cur = dbapi_con.cursor()
            cur.execute("PRAGMA query_only=ON")
            cur.close()

    return eng


def build_write_engine(settings: Settings) -> Engine:
    """可写引擎：**仅回测任务生命周期**（建 PENDING / 读进度/结果）使用。

    默认查询端点仍走只读引擎（query_only=ON，杜绝误写，DESIGN §0）。回测提交是
    *有意*的写（非误写），故用独立 writer；与 worker 写同一 SQLite(WAL) 文件，靠
    busy_timeout 串行化，冲突窗口极小（提交任务稀有，worker 跑在收盘后）。
    """
    return make_engine(settings)


def build_session_factory(engine: Engine) -> sessionmaker:
    return make_session_factory(engine)


def get_db(request: Request) -> Iterator[Session]:
    """FastAPI 依赖：从 app.state.db_factory 取一个读 session。

    测试用 app.dependency_overrides[get_db] 切到临时库。
    """
    factory = getattr(request.app.state, "db_factory", None)
    if factory is None:
        raise UnavailableError("database not ready")
    session = factory()
    try:
        yield session
    finally:
        session.close()


def get_backtest_db(request: Request) -> Iterator[Session]:
    """回测路由专用可写 session（app.state.backtest_db_factory，由 lifespan 创建）。"""
    factory = getattr(request.app.state, "backtest_db_factory", None)
    if factory is None:
        raise UnavailableError("database not ready")
    session = factory()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def read_session(engine: Engine) -> Iterator[Session]:
    """非请求上下文（测试 / 脚本）用的读 session 作用域。"""
    factory = make_session_factory(engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()
