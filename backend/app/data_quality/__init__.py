"""数据质量包（DESIGN §3.1 / P2）：checker 逐条评估行情质量状态。"""
from __future__ import annotations

from app.data_quality.checker import assess

__all__ = ["assess"]
