"""ETF 映射读写（P3）。

- get_active_mappings：按 as_of 取生效中的映射（valid_from<=as_of 且 valid_to 空或>=as_of）。
- upsert_mapping：按 (etf_code, mapping_version) 幂等写；新 version 产生新行，旧版本不覆盖。
- get_mappings_for_backfill：回填用的「去重 etf_code + 聚合 related_sector_codes」。
"""
from __future__ import annotations

from datetime import date
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.db.models.mapping import EtfMapping


def get_active_mappings(session: Session, as_of: Optional[date] = None) -> List[EtfMapping]:
    """as_of 当日生效的映射（默认无时间约束，仅 is_active）。"""
    stmt = select(EtfMapping).where(EtfMapping.is_active == 1)
    if as_of is not None:
        stmt = stmt.where(EtfMapping.valid_from <= as_of)
        stmt = stmt.where((EtfMapping.valid_to.is_(None)) | (EtfMapping.valid_to >= as_of))
    return list(session.execute(stmt).scalars().all())


def upsert_mapping(
    session: Session,
    *,
    etf_code: str,
    etf_name: Optional[str],
    related_sector_codes: Optional[list],
    related_index_code: Optional[str],
    category: Optional[str],
    listing: Optional[str] = None,
    mapping_version: str,
    valid_from: date,
    valid_to: Optional[date] = None,
    notes: Optional[str] = None,
) -> None:
    """按 (etf_code, mapping_version) 幂等写；同 key 更新非键列，新 version 新增行。"""
    stmt = sqlite_insert(EtfMapping).values(
        etf_code=etf_code,
        etf_name=etf_name,
        related_sector_codes=related_sector_codes,
        related_index_code=related_index_code,
        category=category,
        listing=listing,
        mapping_version=mapping_version,
        valid_from=valid_from,
        valid_to=valid_to,
        notes=notes,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["etf_code", "mapping_version"],
        set_={
            "etf_name": stmt.excluded.etf_name,
            "related_sector_codes": stmt.excluded.related_sector_codes,
            "related_index_code": stmt.excluded.related_index_code,
            "category": stmt.excluded.category,
            "listing": stmt.excluded.listing,
            "valid_to": stmt.excluded.valid_to,
            "notes": stmt.excluded.notes,
        },
    )
    session.execute(stmt)


def get_mappings_for_backfill(session: Session) -> List[EtfMapping]:
    """回填用的映射集合（当前生效；backfill 据此推导 ETF 列表与板块并集）。"""
    return get_active_mappings(session)
