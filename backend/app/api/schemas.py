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
    one_liner: Optional[str] = None  # 确定性人话摘要（key_metrics_text），供关注榜/详情页前置展示
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
    listing: Optional[str] = None  # '场内' / '场外'
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


class IndexHistoryPoint(BaseModel):
    date: str
    close: float
    volume: float
    amount: float
    change_percent: Optional[float] = None


class IndexHistoryOut(BaseModel):
    code: str
    name: str
    points: List[IndexHistoryPoint]
    read: str
    signals: List[str] = []


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


# ---- P7：日线回测（异步，Worker 执行；API 仅建任务/查进度/查结果） ----
class BacktestRunRequest(BaseModel):
    etf_code: str = Field(..., min_length=1, max_length=32, description="回测标的 ETF 代码")
    start_date: str = Field(..., description="回测开始交易日 YYYY-MM-DD")
    end_date: str = Field(..., description="回测结束交易日 YYYY-MM-DD")
    initial_capital: float = Field(100000.0, gt=0, le=100_000_000, description="初始资金 > 0")
    benchmark: Optional[str] = Field(None, description="基准 ETF 代码（缺省用 settings.backtest.baseline_etf=510300）")
    strategy_version: Optional[str] = Field(None, description="策略版本（缺省用当前冻结版本；必须已注册）")
    in_sample_end: Optional[str] = Field(None, description="样本内/外分界日 YYYY-MM-DD（不含；缺省 70/30）")


class BacktestTradeOut(BaseModel):
    etf_code: str
    sample: str
    entry_time: str
    exit_time: Optional[str] = None
    entry_price: float
    exit_price: Optional[float] = None
    qty: float
    pnl: Optional[float] = None
    pnl_percent: Optional[float] = None
    reason: Optional[str] = None


class BacktestMetricsOut(BaseModel):
    total_return_pct: Optional[float] = None
    annualized_return_pct: Optional[float] = None
    max_drawdown_pct: Optional[float] = None
    sharpe: Optional[float] = None
    trades_count: int = 0
    win_rate: Optional[float] = None
    avg_pnl_pct: Optional[float] = None


class BacktestSampleOut(BaseModel):
    start: Optional[str] = None
    end: Optional[str] = None
    metrics: BacktestMetricsOut
    equity_curve: List[Dict[str, Any]]


class BacktestResultOut(BaseModel):
    in_sample: BacktestSampleOut
    out_of_sample: BacktestSampleOut
    full: BacktestSampleOut
    benchmark: Dict[str, Any]
    params: Dict[str, Any]
    data_availability: Optional[Dict[str, Any]] = None
    notes: List[str] = []


class BacktestRunOut(BaseModel):
    id: str
    strategy_version: str
    status: str
    progress: int
    start_date: str
    end_date: str
    benchmark: str
    params: Dict[str, Any]
    trades_count: int
    created_at: str
    created_by: Optional[str] = None
    finished_at: Optional[str] = None
    results: Optional[BacktestResultOut] = None
    error_message: Optional[str] = None


class BacktestRunsList(BaseModel):
    items: List[BacktestRunOut]
    total: int
