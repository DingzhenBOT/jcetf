"""仓储层（DESIGN §4 DAG：repository <- collector / data_quality / 各引擎 / api）。

统一负责 market_quote / market_breadth / data_source_status / etf_mapping 的读写。
所有写入走 SQLite 的 INSERT ... ON CONFLICT DO UPDATE 实现幂等（同唯一键覆盖更新，不重复插）。
"""
from __future__ import annotations

from app.repository.mapping_repo import (
    get_active_mappings,
    get_mappings_for_backfill,
    upsert_mapping,
)
from app.repository.quote_repo import (
    get_bar_history,
    get_breadth_on_date,
    get_data_source_status,
    get_last_source_for_symbol_type,
    get_latest_breadth,
    get_latest_quote,
    get_max_bar_timestamp,
    get_sector_quotes,
    record_data_source_status,
    upsert_breadth,
    upsert_market_quotes,
)
from app.repository.signal_repo import (
    get_latest_signal_for_etf,
    get_latest_signals,
    get_opinions_for_etf,
    get_signal_history,
)

__all__ = [
    # quote_repo 写
    "upsert_market_quotes",
    "upsert_breadth",
    "record_data_source_status",
    "get_data_source_status",
    "get_last_source_for_symbol_type",
    # quote_repo 读
    "get_latest_quote",
    "get_bar_history",
    "get_max_bar_timestamp",
    "get_breadth_on_date",
    "get_latest_breadth",
    "get_sector_quotes",
    # mapping_repo
    "get_active_mappings",
    "upsert_mapping",
    "get_mappings_for_backfill",
    # signal_repo 读（P4）
    "get_latest_signals",
    "get_latest_signal_for_etf",
    "get_signal_history",
    "get_opinions_for_etf",
]
