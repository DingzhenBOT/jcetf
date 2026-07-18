"""数据源适配器抽象层（DESIGN §3.1）。

业务代码只依赖 BaseDataProvider，不直接 import AkShare；具体实现（AkShare / Mock）可插拔。
多源降级在 AkShareAdapter 内实现（preferred -> fallback），采集层不感知来源。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class BaseDataProvider(ABC):
    @abstractmethod
    def get_trade_calendar(self) -> list[str]:
        """返回交易日列表（YYYYMMDD 字符串）。"""

    @abstractmethod
    def get_index_snapshot(self) -> pd.DataFrame:
        """宽基/主要指数 SNAPSHOT。"""

    @abstractmethod
    def get_sector_ranking(self, sector_type: str) -> pd.DataFrame:
        """板块排行+资金流。sector_type ∈ {INDUSTRY, CONCEPT}。"""

    @abstractmethod
    def get_etf_snapshot(self) -> pd.DataFrame:
        """全部 ETF SNAPSHOT。"""

    @abstractmethod
    def get_etf_history(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        """ETF 历史日线 BAR（前复权可配）。start/end: YYYYMMDD。"""

    @abstractmethod
    def get_index_history(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        """指数历史日线 BAR（含 amount）。"""

    @abstractmethod
    def get_sector_history(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        """板块历史日线 BAR。"""

    @abstractmethod
    def get_sector_fund_flow_history(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        """板块历史资金流。"""

    @abstractmethod
    def get_market_breadth_raw(self) -> pd.DataFrame:
        """全市场快照（stock_zh_a_spot），采集层据此计算涨跌家数。"""
