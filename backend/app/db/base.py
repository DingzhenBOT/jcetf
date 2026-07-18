"""SQLAlchemy 声明基类 + 时间工具。

设计约束（DESIGN §5 / P0b）：
  - 所有时间列统一存 **UTC naive** datetime（不带时区），便于 SQLite `datetime('now','-N day')` 比较。
  - 因此用 `utcnow()` 生成 naive UTC，禁止使用 tz-aware 值写入时间列。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    """返回当前 UTC 的 naive datetime（满足 prune 比较约束）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)
