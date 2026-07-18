"""仓储层（DESIGN §4 DAG：repository <- collector / data_quality / 各引擎 / api）。

统一负责 market_quote / market_breadth / data_source_status 的写入。
所有写入走 SQLite 的 INSERT ... ON CONFLICT DO UPDATE 实现幂等（同唯一键覆盖更新，不重复插）。
"""
from __future__ import annotations
