"""API 路由聚合（P4）。"""
from __future__ import annotations

from app.api.routers.etfs import router as etfs_router
from app.api.routers.market import router as market_router
from app.api.routers.opinions import router as opinions_router
from app.api.routers.signals import router as signals_router

__all__ = [
    "signals_router",
    "etfs_router",
    "opinions_router",
    "market_router",
]
