"""P4 响应模型（Pydantic）。

- 时间字段统一为 ISO 字符串（naive UTC），前端按北京时间展示（DESIGN §0）。
- JSON 字典字段（supporting_metrics / risk_flags 等）用 Dict[str, Any]。
- 档位中文映射在 serializers 层完成，这里只承载结构。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.portfolio.analyzer import MAX_POSITIONS


class SignalOut(BaseModel):
    signal_id: str
    strategy_version: str
    generated_at: str
    trading_date: str
    target_etf: str
    signal_type: str
    signal_type_text: str
    score: Optional[float] = None
    confidence: Optional[float] = None
    market_regime: Optional[str] = None
    suggested_action: Optional[str] = None
    suggested_position_range: Optional[List[float]] = None
    position_text: str
    supporting_metrics: Optional[Dict[str, Any]] = None
    risk_flags: Optional[Dict[str, Any]] = None
    triggered_rules: Optional[List[str]] = None
    failed_rules: Optional[List[str]] = None
    invalidation_conditions: Optional[Dict[str, Any]] = None
    review_time: Optional[str] = None


class OpinionOut(BaseModel):
    opinion_id: str
    signal_id: Optional[str] = None
    generated_at: str
    trading_date: str
    phase: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None
    input_summary: Optional[Dict[str, Any]] = None
    template_version: Optional[str] = None


class EtfListItem(BaseModel):
    etf_code: str
    etf_name: Optional[str] = None
    category: Optional[str] = None
    related_sector_codes: Optional[List[str]] = None
    related_index_code: Optional[str] = None
    latest_signal: Optional[SignalOut] = None


class SignalHistoryPage(BaseModel):
    items: List[SignalOut]
    total: int
    limit: int
    offset: int


class OpinionsForEtf(BaseModel):
    etf_code: str
    items: List[OpinionOut]


class BreadthOut(BaseModel):
    trading_date: Optional[str] = None
    total_rise: Optional[int] = None
    total_fall: Optional[int] = None
    total_flat: Optional[int] = None
    limit_up: Optional[int] = None
    limit_down: Optional[int] = None
    total_amount: Optional[float] = None
    advance_ratio: Optional[float] = None
    data_source: Optional[str] = None


class IndexSnapshotOut(BaseModel):
    code: str
    name: str
    close: Optional[float] = None
    change_percent: Optional[float] = None
    source: Optional[str] = None


class MarketOverviewOut(BaseModel):
    as_of: Optional[str] = None
    indices: List[IndexSnapshotOut]
    breadth: Optional[BreadthOut] = None
    signal_risk: Dict[str, Any]


# ---- P6：按需持仓分析（无状态，默认不落库） ----
class PortfolioPosition(BaseModel):
    etf_code: str = Field(..., min_length=1, max_length=32)
    cost_price: float = Field(..., gt=0, description="成本单价 > 0")
    position_percent: float = Field(..., ge=0, le=100, description="单项仓位百分比 [0,100]")
    quantity: Optional[float] = Field(None, gt=0, description="持仓数量（可选；有则算盈亏金额）")


class PortfolioAnalyzeRequest(BaseModel):
    positions: List[PortfolioPosition] = Field(
        ..., min_length=1, max_length=MAX_POSITIONS, description=f"最多 {MAX_POSITIONS} 只 ETF，不允许重复"
    )


class PortfolioAnalyzeItem(BaseModel):
    etf_code: str
    action: str
    reason: str
    risk: str
    return_percent: Optional[float] = None
    pnl_amount: Optional[float] = None
    suggested_position_text: Optional[str] = None
    suggested_position_range: Optional[List[float]] = None
    invalidation_conditions: List[str] = []
    review_time: Optional[str] = None


class PortfolioAnalyzeResponse(BaseModel):
    items: List[PortfolioAnalyzeItem]
