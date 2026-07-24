"""集中化配置加载（P0）。

设计要点（对齐 fullstack-dev「配置集中、启动即校验、fail-fast」与 DESIGN §0）：
  - 主配置来自 config/settings.yaml（可入库）。
  - 环境变量对少量「环境相关 / 敏感」项做覆盖（优先级高于 YAML）。
  - 用 Pydantic 做类型校验；缺文件 / 非法 YAML / 类型错误 / 非法组合 -> 显式 ConfigError，
    进程启动即失败（fail-fast），不会带着错误配置悄悄运行。
  - 相对路径以 config 文件所在目录为基准解析，与启动 CWD 解耦，保证可移植。
  - 全局单例 get_settings()；测试用 force_reload / config_path 重载。
"""
from __future__ import annotations

import os
import typing as _t
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError

from app.errors import ConfigError

# --------------------------------------------------------------------------- #
# 1. 配置模型（类型在配置层转换，使用处无需再 cast）
# --------------------------------------------------------------------------- #


class AppConfig(BaseModel):
    name: str = "etf-monitor"
    environment: str = Field(default="dev")  # dev / prod
    debug: bool = False


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    workers: int = 1


class PathsConfig(BaseModel):
    data_dir: str = "../data"
    log_dir: str = "../data/logs"
    sqlite_file: str = "etf_monitor.db"
    backup_dir: str = "../data/backups"
    # 以下为加载后填充的绝对路径（不出现在 YAML 中）
    data_dir_abs: _t.Optional[Path] = None
    log_dir_abs: _t.Optional[Path] = None
    sqlite_path_abs: _t.Optional[Path] = None
    backup_dir_abs: _t.Optional[Path] = None


class LoggingConfig(BaseModel):
    level: str = "INFO"
    json_console: bool = False
    rotation_when: str = "midnight"
    rotation_backup_count: int = 14
    max_bytes: int = 0


class DatabaseConfig(BaseModel):
    echo: bool = False
    wal_mode: bool = True
    busy_timeout_ms: int = 5000
    pool_size: int = 1


class DataSourceConfig(BaseModel):
    mode: str = "real"  # real / mock
    preferred: str = "em"
    fallback: _t.List[str] = Field(default_factory=lambda: ["sina", "ths", "tx"])
    switch_reset_window: bool = True
    request_timeout_seconds: int = 20
    retry_attempts: int = 2
    retry_backoff_seconds: int = 5


class DataQualityConfig(BaseModel):
    """数据质量阈值（DESIGN §3.1 / P2）。

    - 仅在交易时段(is_trading_now)内严格校验时间新鲜度，收盘后不惩罚陈旧。
    - delay < stale：DELAY 是轻度延迟告警，STALE 是明显过期（策略应降置信/标 stale）。
    - A股日涨跌幅限制约 ±10%，用 max_abs_change_percent 做异常护栏（含 ST/新股缓冲）。
    """

    delay_seconds_threshold: int = 120
    stale_seconds_threshold: int = 1800
    max_abs_change_percent: float = 11.0
    min_price: float = 0.01


class SchedulerConfig(BaseModel):
    timezone: str = "Asia/Shanghai"
    enabled: bool = True
    intraday_interval_seconds: int = 180
    intraday_minute_interval_seconds: int = 60  # 盘中分时(1分钟)采集间隔
    pre_close_interval_seconds: int = 60


class CorsConfig(BaseModel):
    allowed_origins: _t.List[str] = Field(default_factory=list)
    allowed_methods: _t.List[str] = Field(
        default_factory=lambda: ["GET", "POST", "OPTIONS"]
    )
    allow_credentials: bool = False


class SecurityConfig(BaseModel):
    enable_headers: bool = True
    content_security_policy: str = "default-src 'none'; frame-ancestors 'none'"


class StrategyConfig(BaseModel):
    version: str = "v1.0.0"
    # 用于 market_regime 计算的宽基指数代码（北向不可达时也可用于 ETF 相对强弱基准）
    broad_index_codes: _t.List[str] = Field(
        default_factory=lambda: ["000300", "000001", "399001"]
    )
    composite_weights: _t.Dict[str, float] = Field(
        default_factory=lambda: {
            "market": 0.25,
            "sector_trend": 0.25,
            "fund_flow": 0.25,
            "etf_rs": 0.25,
        }
    )
    thresholds: _t.Dict[str, _t.Any] = Field(
        default_factory=lambda: {
            "join_observe": 60,
            "small_position": 75,
            "opportunity_enhance": 85,
            "rsi_overheat": 80,
        }
    )
    risk_filter: _t.Dict[str, _t.Any] = Field(
        default_factory=lambda: {
            "deny_market_bear_with_missing_data": True,
            "downgrade_on_chase_high": True,
        }
    )


class BackfillConfig(BaseModel):
    """历史 BAR 回填（P3）。

    - lookback_days：首次回填回看天数（增量后按 max(timestamp)+1 续拉）。
    - broad_index_codes：market_regime 所需的宽基指数集合（与 strategy.broad_index_codes 默认一致）。
    - major_sector_codes：额外强制回填的板块代码（种子未覆盖时）。
    """

    lookback_days: int = 250
    broad_index_codes: _t.List[str] = Field(
        default_factory=lambda: ["000300", "000001", "399001"]
    )
    major_sector_codes: _t.List[str] = Field(default_factory=list)


class BacktestConfig(BaseModel):
    baseline_etf: str = "510300"
    commission_per_thousand: float = 0.15
    slippage_bps: int = 2
    intraday_heavy_disabled: bool = True


class PortfolioConfig(BaseModel):
    max_positions: int = 20
    max_total_percent: int = 100


class HousekeepingConfig(BaseModel):
    log_retention_days: int = 14
    snapshot_retention_days: int = 90
    bar_retention_days: int = 730
    intraday_retention_days: int = 5  # 分时(1m)序列仅保留近 N 个交易日，防库膨胀
    opinion_retention_days: int = 730
    backup_retention_days: int = 7
    backup_remote_enabled: bool = False
    vacuum_after_prune: bool = True
    disk_warn_percent: int = 85
    disabled: bool = False


class Settings(BaseModel):
    app: AppConfig = AppConfig()
    server: ServerConfig = ServerConfig()
    paths: PathsConfig = PathsConfig()
    logging: LoggingConfig = LoggingConfig()
    database: DatabaseConfig = DatabaseConfig()
    data_source: DataSourceConfig = DataSourceConfig()
    data_quality: DataQualityConfig = DataQualityConfig()
    scheduler: SchedulerConfig = SchedulerConfig()
    cors: CorsConfig = CorsConfig()
    security: SecurityConfig = SecurityConfig()
    strategy: StrategyConfig = StrategyConfig()
    backfill: BackfillConfig = BackfillConfig()
    backtest: BacktestConfig = BacktestConfig()
    portfolio: PortfolioConfig = PortfolioConfig()
    housekeeping: HousekeepingConfig = HousekeepingConfig()

    # ----- 便捷方法 / 属性 ----- #
    @property
    def is_production(self) -> bool:
        return self.app.environment == "prod"

    @property
    def sqlite_url(self) -> str:
        """SQLAlchemy 连接串（WAL 由引擎 PRAGMA 设置，见 P1）。"""
        return f"sqlite:///{self.paths.sqlite_path_abs}"

    def ensure_dirs(self) -> None:
        """确保数据与日志目录存在（启动期调用一次）。"""
        for p in (self.paths.data_dir_abs, self.paths.log_dir_abs, self.paths.backup_dir_abs):
            if p is not None:
                p.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
# 2. 环境变量覆盖（白名单，避免散落 os.environ）
# --------------------------------------------------------------------------- #

_CONFIG_PATH_ENV = "ETF_CONFIG_PATH"


def _to_bool(v: str) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")


def _set_nested(d: dict, keys: _t.Tuple[str, ...], value: _t.Any) -> None:
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value


# (env 名, 嵌套 dict 路径, 类型转换)
_ENV_OVERRIDES: _t.List[_t.Tuple[str, _t.Tuple[str, ...], _t.Callable[[str], _t.Any]]] = [
    ("ETF_ENV", ("app", "environment"), str),
    ("ETF_DEBUG", ("app", "debug"), _to_bool),
    ("ETF_API_HOST", ("server", "host"), str),
    ("ETF_API_PORT", ("server", "port"), int),
    ("ETF_DATA_DIR", ("paths", "data_dir"), str),
    ("ETF_LOG_DIR", ("paths", "log_dir"), str),
    ("ETF_SQLITE_FILE", ("paths", "sqlite_file"), str),
    ("ETF_LOG_LEVEL", ("logging", "level"), str),
    ("ETF_DATA_SOURCE_MODE", ("data_source", "mode"), str),
    ("ETF_DATA_SOURCE_PREFERRED", ("data_source", "preferred"), str),
    ("ETF_SCHEDULER_TZ", ("scheduler", "timezone"), str),
    ("ETF_SCHEDULER_ENABLED", ("scheduler", "enabled"), _to_bool),
]


def _apply_env_overrides(raw: dict) -> dict:
    for env_name, path, cast in _ENV_OVERRIDES:
        if env_name in os.environ:
            try:
                _set_nested(raw, path, cast(os.environ[env_name]))
            except (ValueError, TypeError) as e:
                raise ConfigError(
                    f"invalid env override {env_name}={os.environ[env_name]!r}: {e}"
                )
    return raw


# --------------------------------------------------------------------------- #
# 3. 加载 + 校验（fail-fast）
# --------------------------------------------------------------------------- #


def _default_config_path() -> Path:
    # backend/app/config.py -> 上溯三级到 /workspace/config/settings.yaml
    return Path(__file__).resolve().parent.parent.parent / "config" / "settings.yaml"


def _resolve(p: str, base: Path) -> Path:
    pp = Path(p)
    return pp if pp.is_absolute() else (base / pp).resolve()


def load_settings(config_path: _t.Optional[str] = None) -> Settings:
    path = Path(config_path) if config_path else Path(os.environ.get(_CONFIG_PATH_ENV, _default_config_path()))
    path = path.resolve()

    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"invalid YAML in {path}: {e}")

    raw = _apply_env_overrides(raw)

    try:
        settings = Settings(**raw)
    except ValidationError as e:
        raise ConfigError(f"config validation failed: {e}")

    # 解析路径（相对 config 文件目录）
    cfg_dir = path.parent
    settings.paths.data_dir_abs = _resolve(settings.paths.data_dir, cfg_dir)
    settings.paths.log_dir_abs = _resolve(settings.paths.log_dir, cfg_dir)
    settings.paths.backup_dir_abs = _resolve(settings.paths.backup_dir, cfg_dir)
    settings.paths.sqlite_path_abs = settings.paths.data_dir_abs / settings.paths.sqlite_file

    _validate_combinations(settings)
    return settings


def _validate_combinations(s: Settings) -> None:
    """非法组合在启动期即失败，避免带病运行。"""
    if s.is_production and s.server.host != "127.0.0.1":
        raise ConfigError("prod must bind 127.0.0.1 (Nginx 反代); honor DESIGN §0 端口隔离")
    if s.is_production and s.data_source.mode == "mock":
        raise ConfigError("mock data source forbidden in prod (DESIGN §0 数据源故障不降级 Mock)")
    if s.data_source.mode not in ("real", "mock"):
        raise ConfigError(f"data_source.mode must be real|mock, got {s.data_source.mode!r}")
    if s.app.environment not in ("dev", "prod"):
        raise ConfigError(f"app.environment must be dev|prod, got {s.app.environment!r}")


# --------------------------------------------------------------------------- #
# 4. 单例访问
# --------------------------------------------------------------------------- #

_CACHE: _t.Dict[str, Settings] = {}


def get_settings(force_reload: bool = False, config_path: _t.Optional[str] = None) -> Settings:
    key = config_path or os.environ.get(_CONFIG_PATH_ENV, "default")
    if force_reload or key not in _CACHE:
        _CACHE[key] = load_settings(config_path)
    return _CACHE[key]


def clear_cache() -> None:
    """测试用：清空缓存以便重载配置。"""
    _CACHE.clear()
