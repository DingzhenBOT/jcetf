"""策略引擎包（P3）：五类评分 -> 综合分 -> 档位 -> Signal（不可覆盖 strategy_version）。"""
from __future__ import annotations

from app.strategy_engine.engine import StrategyEngine, compute_composite, decide_tier
from app.strategy_engine.rules import RULES_V1

__all__ = ["StrategyEngine", "RULES_V1", "compute_composite", "decide_tier"]
