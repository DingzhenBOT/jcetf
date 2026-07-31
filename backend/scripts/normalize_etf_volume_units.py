"""把历史 ETF 成交量幂等迁移到 shares（份/股）口径。

默认只报告，不写数据库；显式传 ``--apply`` 才会提交：

  python backend/scripts/normalize_etf_volume_units.py --config config/settings.yaml
  python backend/scripts/normalize_etf_volume_units.py --config config/settings.yaml --apply

规则来自生产数据的价量核对：em/gtimg 的 ETF volume/cum_volume 为手，乘 100；
sina 已经是份，只标记单位。INDEX/SECTOR 不参与迁移。volume_unit 是幂等哨兵，
因此脚本重复执行不会二次放大。
"""
from __future__ import annotations

import argparse
import os
from typing import Any, Dict

from sqlalchemy import text

from app.collector.normalize import ETF_VOLUME_DEF_VERSION, ETF_VOLUME_UNIT
from app.config import get_settings
from app.db.session import ensure_schema_columns, make_engine, session_scope


_PENDING_FILTER = "symbol_type = 'ETF' AND volume_unit IS NULL"
_LOT_FILTER = (
    _PENDING_FILTER
    + " AND (lower(data_source) LIKE 'em%' OR lower(data_source) LIKE 'gtimg%')"
)
_SHARE_FILTER = _PENDING_FILTER + " AND lower(data_source) LIKE 'sina%'"


def _count(session, where: str) -> int:
    return int(session.execute(text(f"SELECT count(*) FROM market_quote WHERE {where}")).scalar_one())


def migration_summary(session) -> Dict[str, int]:
    """返回迁移候选数量，不改变数据。"""
    pending = _count(session, _PENDING_FILTER)
    lot_rows = _count(session, _LOT_FILTER)
    share_rows = _count(session, _SHARE_FILTER)
    return {
        "pending_etf_rows": pending,
        "lot_rows_x100": lot_rows,
        "share_rows_mark_only": share_rows,
        "unknown_source_rows_untouched": pending - lot_rows - share_rows,
    }


def apply_migration(session) -> Dict[str, Any]:
    """执行一次迁移并提交；以 volume_unit IS NULL 保证幂等。"""
    before = migration_summary(session)
    lot_result = session.execute(
        text(
            "UPDATE market_quote SET "
            "volume = CASE WHEN volume IS NULL THEN NULL ELSE volume * 100.0 END, "
            "cum_volume = CASE WHEN cum_volume IS NULL THEN NULL ELSE cum_volume * 100.0 END, "
            "volume_unit = :unit, metric_definition_version = :version "
            f"WHERE {_LOT_FILTER}"
        ),
        {"unit": ETF_VOLUME_UNIT, "version": ETF_VOLUME_DEF_VERSION},
    )
    share_result = session.execute(
        text(
            "UPDATE market_quote SET volume_unit = :unit, metric_definition_version = :version "
            f"WHERE {_SHARE_FILTER}"
        ),
        {"unit": ETF_VOLUME_UNIT, "version": ETF_VOLUME_DEF_VERSION},
    )
    session.commit()
    return {
        **before,
        "updated_lot_rows": int(lot_result.rowcount or 0),
        "updated_share_rows": int(share_result.rowcount or 0),
        "remaining_pending_etf_rows": migration_summary(session)["pending_etf_rows"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="normalize ETF volume to shares")
    parser.add_argument("--config", default=os.environ.get("ETF_CONFIG_PATH"))
    parser.add_argument("--apply", action="store_true", help="commit changes (default: dry-run)")
    args = parser.parse_args()

    settings = get_settings(config_path=args.config)
    engine = make_engine(settings)
    try:
        ensure_schema_columns(engine)
        with session_scope(engine) as session:
            result = apply_migration(session) if args.apply else migration_summary(session)
        print(("APPLIED" if args.apply else "DRY-RUN"), result)
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
