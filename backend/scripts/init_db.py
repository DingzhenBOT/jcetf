"""初始化数据库（P1）：建 8 张核心表 + 索引 + 幂等注入 strategy_version。

用法：
  python3.11 backend/scripts/init_db.py [--config <path>]
  （默认读 ETF_CONFIG_PATH 或 /workspace/config/settings.yaml）
可重复运行：已存在表/版本不会重建/覆盖。
"""
from __future__ import annotations

import argparse
import os

from app.config import Settings, get_settings
from app.db.session import init_db, make_engine
from app.logging_conf import get_logger, setup_logging


def main() -> int:
    parser = argparse.ArgumentParser(description="init etf-monitor sqlite db")
    parser.add_argument("--config", default=os.environ.get("ETF_CONFIG_PATH"))
    args = parser.parse_args()

    settings = get_settings(config_path=args.config)
    setup_logging(settings)
    settings.ensure_dirs()

    engine = make_engine(settings)
    init_db(engine, settings)
    engine.dispose()

    log = get_logger(__name__)
    log.info("db initialized", extra={"db": str(settings.paths.sqlite_path_abs)})
    print(f"DB initialized at {settings.paths.sqlite_path_abs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
