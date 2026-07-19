"""风险引擎包（P3）：否决 / 降级（DESIGN §9.1-5，风险不作扣分，仅否决或降级）。"""
from __future__ import annotations

from app.risk_engine.engine import RiskEngine

__all__ = ["RiskEngine"]
