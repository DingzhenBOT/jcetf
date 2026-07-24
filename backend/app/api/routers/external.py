"""外部 skill 数据源路由（Phase C / P2·P3·P5）。

- GET /api/external/sectors/movement  板块异动（腾讯自选股）
- GET /api/external/news               当日新闻（东财全球资讯）
- GET /api/external/offexchange        场外基金（盈米；未配置时优雅降级）
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query

from app.api.schemas import BaseModel
from app.services import external_data

router = APIRouter(prefix="/api/external", tags=["external"])


# ---- 复用轻量 schema（避免与核心 schema 耦合） ---- #
class SectorItem(BaseModel):
    name: str
    changePct: Optional[float] = None
    turnoverRate: Optional[float] = None
    changePct5d: Optional[float] = None
    changePct20d: Optional[float] = None
    leadStock: Optional[str] = None


class FundFlowItem(BaseModel):
    name: str
    changePct: Optional[float] = None
    mainNetInflow: Optional[float] = None
    mainNetInflow5d: Optional[float] = None
    upDownRatio: Optional[str] = None


class SectorMovementOut(BaseModel):
    available: bool = True
    source: Optional[str] = None
    industry: List[Dict[str, Any]] = []
    concept: List[Dict[str, Any]] = []
    fund_flow: List[Dict[str, Any]] = []


class NewsItem(BaseModel):
    time: str = ""
    title: str = ""
    summary: str = ""


class NewsOut(BaseModel):
    available: bool = True
    source: Optional[str] = None
    items: List[NewsItem] = []


class OffExchangeFund(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    type: Optional[str] = None
    change_percent: Optional[float] = None
    nav: Optional[float] = None


class OffExchangeOut(BaseModel):
    available: bool = True
    source: Optional[str] = None
    reason: Optional[str] = None
    items: List[OffExchangeFund] = []


@router.get("/sectors/movement", response_model=SectorMovementOut)
def sectors_movement() -> SectorMovementOut:
    try:
        data = external_data.collect_sector_movement()
    except Exception as e:  # npx 失败/超时 -> 降级
        return SectorMovementOut(available=False, source="腾讯自选股 westock-data", industry=[], concept=[], fund_flow=[])
    return SectorMovementOut(
        available=data.get("available", True),
        source=data.get("source"),
        industry=data.get("industry", []),
        concept=data.get("concept", []),
        fund_flow=data.get("fund_flow", []),
    )


@router.get("/news", response_model=NewsOut)
def news(limit: int = Query(30, ge=1, le=100)) -> NewsOut:
    data = external_data.collect_news(limit=limit)
    return NewsOut(
        available=data.get("available", False),
        source=data.get("source"),
        items=[NewsItem(**it) for it in data.get("items", [])],
    )


@router.get("/offexchange", response_model=OffExchangeOut)
def offexchange(
    keyword: str = Query("ETF", min_length=1),
    limit: int = Query(10, ge=1, le=50),
) -> OffExchangeOut:
    data = external_data.collect_offexchange_funds(keyword=keyword, limit=limit)
    return OffExchangeOut(
        available=data.get("available", False),
        source=data.get("source"),
        reason=data.get("reason"),
        items=[OffExchangeFund(**it) for it in data.get("items", [])],
    )
