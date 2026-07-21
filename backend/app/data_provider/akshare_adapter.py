"""AkShare 适配器（多源可插拔 + 自动降级）。

- preferred 优先，失败/空则按 fallback 顺序尝试，首个成功即返回并记录实际来源。
- 各能力按 source 映射到具体 akshare 函数（已用 P-1/P-1b 真实网络验证）。
- em 在沙箱/部分生产网络被防火墙拦截 -> 自动降级 sina/ths/tx；生产（国内网络）优先 em。
- 指数/ETF 历史已回落到新浪（stock_zh_index_daily / fund_etf_hist_sina）；新浪仅接受 symbol
  且需 sh/sz 前缀（系统存数字代码），由 _to_sina_symbol 转换，且新浪函数不接受 start/end
  参数，故历史接口的 kwargs 按源分别构造（见 _history_source_map）。
- 板块历史/板块历史资金流仅 em 提供；新浪无替代，沙箱会全部失败并优雅降级（D4）。
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

    # ---- 内部：数字代码 -> 新浪 sh/sz 前缀 ----
    def _to_sina_symbol(self, code: str, kind: str) -> str:
        """系统存数字代码（如 000300 / 510300），新浪接口需 sh/sz 前缀。

        - 指数：0/6/9 -> sh（含 000300/000001/000016/000688/000905 等上交所指数），3 -> sz（399001/399006）。
        - ETF：5 -> sh（上交所 51xxxx/56xxxx/58xxxx），1/0 -> sz（深交所 159xxx 及场外联接）。
        - 已带 sh/sz 前缀则原样返回。
        注：0 前缀指数统一归 sh（本项目追踪的 0 前缀指数均为上交所）；若将来引入深市 0 前缀指数需在此扩展。
        """
        code = str(code).strip().lower()
        if code[:2] in ("sh", "sz"):
            return code
        head = code[0] if code else ""
        if kind == "index":
            prefix = "sh" if head in ("0", "6", "9") else "sz"
        else:  # etf
            prefix = "sh" if head == "5" else "sz"
        return prefix + code

    def _history_source_map(self, base_map, kind: str, raw_symbol: str, start: str, end: str) -> Dict[str, Tuple[str, dict]]:
        """历史 BAR 逐源 kwargs：em 传 symbol+start/end；sina/tx 仅传 symbol（不接受起止参数，且需 sh/sz 前缀）。

        修复旧实现给所有源统一注入 start_date/end_date 导致新浪/腾讯函数 TypeError 的问题。
        """
        out: Dict[str, Tuple[str, dict]] = {}
        for src, (func, kw) in base_map.items():
            if src in ("sina", "tx"):
                out[src] = (func, {**kw, "symbol": self._to_sina_symbol(raw_symbol, kind)})
            else:
                out[src] = (func, {**kw, "symbol": raw_symbol, "start_date": start, "end_date": end})
        return out

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
    # ETF 历史：em 接受 period/adjust/start/end；新浪 fund_etf_hist_sina 仅接受 symbol（sh/sz 前缀）。
    # 旧签名 {"period":"daily","adjust":"qfq"} 在新版 akshare 已无效 -> 改为仅 symbol，由 _history_source_map 注入。
    _ETF_HIST = {
        "em": ("fund_etf_hist_em", {"period": "daily", "adjust": "qfq"}),
        "sina": ("fund_etf_hist_sina", {}),
    }
    # 指数历史：em/tx 仅接受 symbol（sh/sz 前缀）；新浪 stock_zh_index_daily 仅接受 symbol。
    # 三者均不接受 start/end 参数，故历史 kwargs 按源分别构造。
    _INDEX_HIST = {
        "em": ("stock_zh_index_daily_em", {}),
        "sina": ("stock_zh_index_daily", {}),
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
        df, src = self._call("etf_history", self._history_source_map(self._ETF_HIST, "etf", symbol, start, end))
        df.attrs["__source"] = src
        return df

    def get_index_history(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        df, src = self._call("index_history", self._history_source_map(self._INDEX_HIST, "index", symbol, start, end))
        df.attrs["__source"] = src
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
