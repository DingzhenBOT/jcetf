"""AkShare 适配器（多源可插拔 + 自动降级）。

- preferred 优先，失败/空则按 fallback 顺序尝试，首个成功即返回并记录实际来源。
- 各能力按 source 映射到具体 akshare 函数（已用 P-1/P-1b 真实网络验证）。
- em 在沙箱被防火墙拦截 -> 自动降级 sina/ths/tx；生产（国内网络）优先 em。
- 板块历史/板块历史资金流仅 em 提供（生产验证）；沙箱会全部失败并抛 DataSourceError。
"""
from __future__ import annotations

from typing import Dict, List, Tuple

import akshare as ak
import pandas as pd

from app.config import Settings
from app.data_provider.base import BaseDataProvider
from app.errors import DataSourceError


class AkShareAdapter(BaseDataProvider):
    def __init__(self, settings: Settings):
        self.settings = settings
        self.preferred = settings.data_source.preferred
        self.fallback = list(settings.data_source.fallback)
        self.retry = settings.data_source.retry_attempts

    # ---- 内部：按来源顺序降级 ----
    def _ordered_sources(self) -> List[str]:
        out: List[str] = []
        seen = set()
        for s in [self.preferred] + self.fallback:
            if s not in seen:
                seen.add(s)
                out.append(s)
        return out

    def _call(self, capability: str, source_map: Dict[str, Tuple[str, dict]]) -> Tuple[pd.DataFrame, str]:
        last_err = "no matching source"
        for src in self._ordered_sources():
            spec = source_map.get(src)
            if not spec:
                continue
            func_name, kwargs = spec
            for attempt in range(self.retry + 1):
                try:
                    func = getattr(ak, func_name)
                    df = func(**kwargs)
                    if df is None or (hasattr(df, "empty") and df.empty):
                        last_err = f"{src} returned empty"
                        break  # 空结果多试无益，换源
                    return df, src
                except Exception as e:  # noqa: BLE001
                    last_err = f"{src}: {type(e).__name__}: {e}"
                    continue
        raise DataSourceError(f"{capability} failed on all sources: {last_err}")

    # ---- 各能力实现 ----
    # 来源 -> (函数名, kwargs)
    _INDEX_SPOT = {
        "em": ("stock_zh_index_spot_em", {}),
        "sina": ("stock_zh_index_spot_sina", {}),
    }
    _ETF_SPOT = {
        "em": ("fund_etf_spot_em", {}),
        "sina": ("fund_etf_category_sina", {"symbol": "ETF基金"}),
    }
    _SECTOR_INDUSTRY = {
        "em": ("stock_board_industry_name_em", {}),
        "ths": ("stock_fund_flow_industry", {}),
    }
    _SECTOR_CONCEPT = {
        "em": ("stock_board_concept_name_em", {}),
        "ths": ("stock_fund_flow_concept", {}),
    }
    _TRADE_CALENDAR = {"sina": ("tool_trade_date_hist_sina", {})}
    _BREADTH_RAW = {"sina": ("stock_zh_a_spot", {})}
    _ETF_HIST = {
        "em": ("fund_etf_hist_em", {"period": "daily", "adjust": "qfq"}),
        "sina": ("fund_etf_hist_sina", {"period": "daily", "adjust": "qfq"}),
    }
    _INDEX_HIST = {
        "em": ("stock_zh_index_daily_em", {}),
        "tx": ("stock_zh_index_daily_tx", {}),
    }
    # 仅 em（生产验证；沙箱会失败）
    _SECTOR_HIST = {"em": ("stock_board_industry_hist_em", {"period": "daily", "adjust": "qfq"})}
    _SECTOR_FLOW_HIST = {"em": ("stock_sector_fund_flow_hist", {"period": "daily"})}

    def get_trade_calendar(self) -> list:
        df, _ = self._call("trade_calendar", self._TRADE_CALENDAR)
        col = df.columns[0]
        return [str(x) for x in df[col].tolist()]

    def get_index_snapshot(self) -> pd.DataFrame:
        df, src = self._call("index_snapshot", self._INDEX_SPOT)
        df.attrs["__source"] = src
        return df

    def get_sector_ranking(self, sector_type: str) -> pd.DataFrame:
        src_map = self._SECTOR_INDUSTRY if sector_type == "INDUSTRY" else self._SECTOR_CONCEPT
        df, src = self._call(f"sector_ranking:{sector_type}", src_map)
        df.attrs["__source"] = src
        return df

    def get_etf_snapshot(self) -> pd.DataFrame:
        df, src = self._call("etf_snapshot", self._ETF_SPOT)
        df.attrs["__source"] = src
        return df

    def get_etf_history(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        df, _ = self._call("etf_history", {k: (f, {**kw, "symbol": symbol, "start_date": start, "end_date": end}) for k, (f, kw) in self._ETF_HIST.items()})
        return df

    def get_index_history(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        df, _ = self._call("index_history", {k: (f, {**kw, "symbol": symbol, "start_date": start, "end_date": end}) for k, (f, kw) in self._INDEX_HIST.items()})
        return df

    def get_sector_history(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        df, _ = self._call("sector_history", {k: (f, {**kw, "symbol": symbol, "start_date": start, "end_date": end}) for k, (f, kw) in self._SECTOR_HIST.items()})
        return df

    def get_sector_fund_flow_history(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        df, _ = self._call("sector_fund_flow_history", {k: (f, {**kw, "symbol": symbol, "start_date": start, "end_date": end}) for k, (f, kw) in self._SECTOR_FLOW_HIST.items()})
        return df

    def get_market_breadth_raw(self) -> pd.DataFrame:
        df, src = self._call("market_breadth_raw", self._BREADTH_RAW)
        df.attrs["__source"] = src
        return df
