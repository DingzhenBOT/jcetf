"""模型注册入口：import 本包即把所有表挂到 Base.metadata。"""
from app.db.models.market import MarketBreadth, MarketQuote
from app.db.models.mapping import EtfMapping, StrategyVersion
from app.db.models.signal_opinion import Opinion, Signal
from app.db.models.system import DataSourceStatus, TaskRunLog

__all__ = [
    "MarketQuote",
    "MarketBreadth",
    "EtfMapping",
    "StrategyVersion",
    "Signal",
    "Opinion",
    "TaskRunLog",
    "DataSourceStatus",
]
