"""持仓分析路由（P6）。

POST /api/portfolio/analyze -> 提交持仓即时计算，默认不落库（DESIGN § 按需持仓分析）。
服务端强制校验：最多 20 只、不允许重复、cost_price>0、position_percent∈[0,100]、
合计 ≤ 100、仅限 etf_mapping 白名单内的 ETF。仅读库。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_db
from app.api.schemas import PortfolioAnalyzeRequest, PortfolioAnalyzeResponse
from app.db.session import Session
from app.errors import ValidationError
from app.portfolio.analyzer import analyze_portfolio
from app.repository import mapping_repo

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


@router.post("/analyze", response_model=PortfolioAnalyzeResponse)
def analyze(req: PortfolioAnalyzeRequest, session: Session = Depends(get_db)):
    """批量持仓分析（无状态）。返回每项动作/盈亏/建议仓位等。"""
    positions = [p.model_dump() for p in req.positions]

    # 重复 ETF 校验
    codes = [p["etf_code"] for p in positions]
    if len(codes) != len(set(codes)):
        dupes = sorted({c for c in codes if codes.count(c) > 1})
        raise ValidationError(
            f"duplicate etf_code not allowed: {dupes}",
            details={"duplicates": dupes},
        )

    # 仓位合计校验
    total_pct = sum(p["position_percent"] for p in positions)
    if total_pct > 100:
        raise ValidationError(
            f"position_percent sum {total_pct:.2f} exceeds 100",
            details={"sum": total_pct},
        )

    # ETF 白名单校验（仅 etf_mapping 内可分析）
    allowed = {m.etf_code for m in mapping_repo.get_active_mappings(session)}
    bad = [c for c in codes if c not in allowed]
    if bad:
        raise ValidationError(
            f"etf not in whitelist: {bad}",
            details={"not_allowed": bad},
        )

    items = analyze_portfolio(positions, session)
    return PortfolioAnalyzeResponse(items=items)
