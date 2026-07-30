"""引擎 / 会话 / 初始化（P1）。

- SQLite + WAL + busy_timeout（单写者；worker 单实例保证写入不冲突，DESIGN §0）。
- 时间列均为 naive UTC（见 db/base.utcnow）。
- init_db 建表 + 索引 + 幂等注入 strategy_version（不可覆盖基线）。
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.base import Base


def make_engine(settings: Settings, echo: bool | None = None) -> Engine:
    """创建 SQLite 引擎，连接级 PRAGMA 设 WAL + busy_timeout。"""
    eng = create_engine(
        settings.sqlite_url,
        echo=settings.database.echo if echo is None else echo,
        future=True,
        connect_args={"timeout": settings.database.busy_timeout_ms / 1000.0},
        pool_pre_ping=False,
    )
    if settings.database.wal_mode:
        # 每次检出连接都设 PRAGMA（SQLite 连接级状态）
        @event.listens_for(eng, "connect")
        def _pragma_on_connect(dbapi_con, _record):
            cur = dbapi_con.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute(f"PRAGMA busy_timeout={settings.database.busy_timeout_ms}")
            cur.close()

    return eng


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """事务作用域：正常提交，异常回滚。"""
    factory = make_session_factory(engine)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def ping_db(settings: Settings) -> bool:
    """就绪探针：DB 文件存在且可连接。供 /ready 使用。"""
    if not settings.paths.sqlite_path_abs or not settings.paths.sqlite_path_abs.exists():
        return False
    try:
        eng = make_engine(settings)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
        return True
    except Exception:
        return False


def init_db(engine: Engine, settings: Settings | None = None) -> None:
    """建表 + 索引（按 Base.metadata），并按需幂等注入 strategy_version。"""
    from app.db import models  # noqa: F401 注册所有模型元数据

    Base.metadata.create_all(engine)
    # SQLite 下 create_all 不会给已存在的表补列；新增列用幂等 ALTER 补充
    ensure_schema_columns(engine)
    if settings is not None:
        _seed_strategy_version(engine, settings)


def ensure_schema_columns(engine: Engine) -> None:
    """对已存在表幂等补充新增列（SQLite 不支持 ALTER 自动加列）。

    供 init_db 与 API 启动时调用：保证读取路径所需列存在，不依赖 worker 先跑一轮。
    仅当列缺失时才真正写；表尚未创建（如 worker 未运行）时跳过，不报错、不影响启动。
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    def add_column_if_missing(table: str, col: str, sqltype: str) -> bool:
        if table not in existing_tables:
            return False
        cols = {c["name"] for c in inspector.get_columns(table)}
        if col in cols:
            return False
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {sqltype}"))
        return True

    # etf_mapping.listing（P4 场内/场外区分）
    add_column_if_missing("etf_mapping", "listing", "VARCHAR(8)")

    # signal.phase（#67 续：标注信号由哪个阶段评估生成 pre_market/midday/pre_close/post_close）
    if add_column_if_missing("signal", "phase", "VARCHAR(32)"):
        with engine.begin() as conn:
            # 存量信号：从同 signal_id 的最新意见回填 phase（盘中/收盘后区分落地前的数据）
            conn.execute(
                text(
                    "UPDATE signal SET phase = ("
                    "SELECT o.phase FROM opinion o "
                    "WHERE o.signal_id = signal.signal_id "
                    "ORDER BY o.generated_at DESC LIMIT 1"
                    ") WHERE phase IS NULL"
                )
            )

    # opinion.basis_text（复盘「分析依据」专业叙述，前端「查看依据」渲染）
    add_column_if_missing("opinion", "basis_text", "TEXT")

    # market_quote.cum_volume（C22：1m BAR 存当日累计成交量，增量 = 当前cum - 上一根BAR的cum，
    # 规避快照采集 180s 与 1m 采样 60s 频率错配导致的成交量漏计/重复）
    add_column_if_missing("market_quote", "cum_volume", "FLOAT")


def _seed_strategy_version(engine: Engine, settings: Settings) -> None:
    from app.db.models.mapping import StrategyVersion
    from app.strategy_versioning import current_strategy_version

    version, strategy_hash = current_strategy_version(settings)
    with session_scope(engine) as s:
        existing = s.get(StrategyVersion, version)
        if existing is not None:
            return  # 已存在，禁止覆盖（不可覆盖版本）
        s.add(
            StrategyVersion(
                version=version,
                strategy_hash=strategy_hash,
                name="baseline",
                description="auto-seeded at init_db (P1)",
                params_json={
                    "composite_weights": settings.strategy.composite_weights,
                    "thresholds": settings.strategy.thresholds,
                    "risk_filter": settings.strategy.risk_filter,
                },
                rules_json={},  # P3 填充实际规则 -> 产生新版本
            )
        )
