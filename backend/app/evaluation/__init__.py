"""评估流水线包（P3）：采集后评估 -> 信号 + 意见（幂等）。"""
from __future__ import annotations

from app.evaluation.pipeline import post_collection_evaluate

__all__ = ["post_collection_evaluate"]
