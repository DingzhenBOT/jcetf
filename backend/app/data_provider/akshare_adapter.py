"""AkShare 适配器（多源可插拔 + 自动降级）。

- preferred 优先，失败/空则按 fallback 顺序尝试，首个成功即返回并记录实际来源。
- 各能力按 source 映射到具体 akshare 函数（已用 P-1/P-1b 真实网络验证）。
- em 在沙箱/部分生产网络被防火墙拦截 -> 自动降级 sina/ths/tx；生产（国内网络）优先 em。
- 指数/ETF 历史已回落到新浪（stock_zh_index_daily / fund_etf_hist_sina）；新浪仅接受 symbol
  且需 sh/sz 前缀（系统存数字代码），由 _to_sina_symbol 转换，且新浪函数不接受 start/end
  参数，故历史接口的 kwargs 按源分别构造（见 _history_source_map）。
- 板块历史：腾讯云东财被 RST 拦截，唯一可用源为同花顺（ths）。
  em 行业历史（stock_board_industry_hist_em）走 BK 代码，生产/本地网可用；
  ths 行业/概念历史经 _BK_TO_THS 把 BK 代码解析为同花顺板块名（行业板覆盖半导体/证券/银行/
  白酒/光伏设备，概念板覆盖军工/新能源汽车/5G）。医药/消费在 THS 无单一聚合板 -> 映射 None，
  本地/生产网回退 em，腾讯云降级（D4）。
- 板块历史资金流仅 em 提供（ths 仅当日快照无历史），腾讯云降级（D4）。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import akshare as ak
import pandas as pd
import requests

from app.config import Settings
from app.data_provider.base import BaseDataProvider
from app.errors import DataSourceError


# --------------------------------------------------------------------------- #
# 东财请求头补丁
# 东财 kline 接口（push2his.eastmoney.com/api/qt/stock/kline/get）不带 Referer
# 会返回空数据：腾讯云网络层可达，但裸请求被应用层拒绝（0 行 DataFrame）。
# 仅对 eastmoney URL 注入 Referer/UA，不影响新浪/同花顺等源；幂等。
# --------------------------------------------------------------------------- #
_EM_REFERER = "https://quote.eastmoney.com/"
_EM_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_patched = False


def _em_headers_for(url: str) -> Optional[dict]:
    """对 eastmoney URL 返回需注入的请求头；非东财返回 None。"""
    if "eastmoney.com" in str(url):
        return {"Referer": _EM_REFERER, "User-Agent": _EM_USER_AGENT}
    return None


def install_em_headers_patch() -> None:
    """猴子补丁 requests.Session.request：仅对 eastmoney URL 注入 Referer/UA（幂等）。

    沙箱/部分网络下东财被墙时此补丁无害（请求仍会失败并降级到新浪）。
    """
    global _patched
    if _patched:
        return
    _orig = requests.Session.request

    def _request_with_em_headers(self, method, url, *args, **kwargs):
        hdr = _em_headers_for(url)
        if hdr:
            headers = dict(kwargs.get("headers") or {})
            headers.update(hdr)
            kwargs["headers"] = headers
        return _orig(self, method, url, *args, **kwargs)

    requests.Session.request = _request_with_em_headers
    _patched = True


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

    def _history_symbol(self, kind: str, src: str, raw_symbol: str) -> str:
        """历史接口每个源期望的 symbol 格式：
        - sina/tx：一律 sh/sz 前缀（fund_etf_hist_sina / stock_zh_index_daily(_tx) 只认前缀）。
        - em 指数：stock_zh_index_daily_em 内部不查市场，必须 sh/sz 前缀（裸码静默返回空 DataFrame）。
        - em ETF：fund_etf_hist_em 内部 get_market_id 自查市场，传裸码即可。
        """
        if src in ("sina", "tx"):
            return self._to_sina_symbol(raw_symbol, kind)
        if kind == "index":
            return self._to_sina_symbol(raw_symbol, "index")
        return raw_symbol

    def _history_source_map(self, base_map, kind: str, raw_symbol: str, start: str, end: str) -> Dict[str, Tuple[str, dict]]:
        """历史 BAR 逐源 kwargs：em 传 symbol+start/end；sina/tx 仅传 symbol（不接受起止参数）。

        修复旧实现给所有源统一注入 start_date/end_date 导致新浪/腾讯函数 TypeError 的问题；
        并修正 em 指数需 sh/sz 前缀（裸码会静默返回空，从未真正触网）。
        """
        out: Dict[str, Tuple[str, dict]] = {}
        for src, (func, kw) in base_map.items():
            if src in ("sina", "tx"):
                out[src] = (func, {**kw, "symbol": self._history_symbol(kind, src, raw_symbol)})
            else:
                out[src] = (func, {**kw, "symbol": self._history_symbol(kind, src, raw_symbol), "start_date": start, "end_date": end})
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
    # 板块历史：em（BK 代码，生产/本地网可用）；ths 由 _bk_to_ths 动态解析板块名（腾讯云可用）。
    _SECTOR_HIST = {"em": ("stock_board_industry_hist_em", {"period": "daily", "adjust": "qfq"})}
    _SECTOR_FLOW_HIST = {"em": ("stock_sector_fund_flow_hist", {"period": "daily"})}

    # BK 板块代码 -> 同花顺对应板（type: industry/concept, name: THS 板块名）。
    # 腾讯云东财被 RST 拦截，行业/概念历史唯一可用源为同花顺。
    #   THS 行业板 1:1 覆盖：半导体 / 证券(券商) / 银行 / 白酒 / 光伏设备
    #   THS 概念板 1:1 覆盖：军工 / 新能源汽车 / 5G
    #   医药、消费在 THS 无单一聚合板 -> None（生产网走 em，腾讯云优雅降级 D4）。
    # 名称经 _get_stock_board_industry_name_ths / _get_stock_board_concept_name_ths 实测存在。
    _BK_TO_THS: Dict[str, Optional[Tuple[str, str]]] = {
        "BK0465": None,                       # 医药：THS 无单一聚合板
        "BK0481": ("concept", "军工"),
        "BK0900": ("concept", "新能源汽车"),
        "BK1035": ("industry", "光伏设备"),
        "BK1036": ("industry", "半导体"),
        "BK0999": ("concept", "5G"),
        "BK0473": ("industry", "证券"),       # 券商 ≈ 证券
        "BK0475": ("industry", "银行"),
        "BK0438": None,                       # 消费：THS 无单一聚合板
        "BK0471": ("industry", "白酒"),
    }

    def _bk_to_ths(self, bk_code: str) -> Optional[Tuple[str, str]]:
        """BK 代码 -> (ths_type, ths_name)；无对应板返回 None（调用方应优雅跳过）。"""
        return self._BK_TO_THS.get(bk_code)

    def get_trade_calendar(self) -> list:
        df, _ = self._call("trade_calendar", self._TRADE_CALENDAR)
        col = df.columns[0]
        return [str(x) for x in df[col].tolist()]

    def get_index_snapshot(self) -> pd.DataFrame:
        df, src = self._call("index_snapshot", self._INDEX_SPOT)
        df.attrs["__source"] = src
        return df

    # ---- 指数快照多源补齐（em 批次可能缺失深市指数 399001/399006） ----
    def index_spot_sources(self) -> List[str]:
        """index_snapshot 可按源单独调用的有序源列表（preferred 优先）。"""
        return [s for s in self._ordered_sources() if s in self._INDEX_SPOT]

    def get_index_snapshot_from(self, src: str) -> pd.DataFrame:
        """调用指定源的指数快照（供 collect_index_snapshot 补齐 em 缺失代码）。"""
        spec = self._INDEX_SPOT.get(src)
        if not spec:
            raise DataSourceError(f"index_snapshot unsupported source: {src}")
        func_name, kwargs = spec
        func = getattr(ak, func_name)
        df = func(**kwargs)
        if df is None or (hasattr(df, "empty") and df.empty):
            raise DataSourceError(f"index_snapshot {src} returned empty")
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

    def get_intraday_minute(self, symbol_type: str, code: str) -> pd.DataFrame:
        """盘中 1 分钟分时（sina stock_zh_a_minute）。

        - 腾讯云 em 被墙，分时固定走 sina（ETF/指数均支持 sh/sz 前缀代码）。
        - 返回列：day, open, high, low, close, volume（day 为 naive 本地时间）。
        """
        symbol = self._to_sina_symbol(code, symbol_type)
        try:
            df = ak.stock_zh_a_minute(symbol=symbol, period="1", adjust="")
        except Exception as e:  # noqa: BLE001
            raise DataSourceError(f"intraday_minute sina {symbol}: {type(e).__name__}: {e}")
        if df is None or (hasattr(df, "empty") and df.empty):
            raise DataSourceError(f"intraday_minute sina {symbol} returned empty")
        df.attrs["__source"] = "sina"
        return df

    def get_sector_history(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        """板块历史 BAR。symbol 为 BK 代码（如 BK1036）。

        - em：stock_board_industry_hist_em(symbol=BK代码)（生产/本地网可用）。
        - ths：经 _bk_to_ths 解析为同花顺行业/概念板名后调用对应函数（腾讯云可用）。
          解析为 None 的板块（医药/消费）跳过 ths 源；若同时无 em 可用则抛 DataSourceError 降级。
        """
        src_map: Dict[str, Tuple[str, dict]] = {}
        for src in self._ordered_sources():
            if src == "em":
                src_map[src] = (
                    "stock_board_industry_hist_em",
                    {"symbol": symbol, "period": "daily", "adjust": "qfq", "start_date": start, "end_date": end},
                )
            elif src == "ths":
                ths = self._bk_to_ths(symbol)
                if ths is None:
                    continue  # 该板块在 THS 无单一聚合板，跳过 ths 源
                ths_type, ths_name = ths
                func = "stock_board_industry_index_ths" if ths_type == "industry" else "stock_board_concept_index_ths"
                src_map[src] = (func, {"symbol": ths_name, "start_date": start, "end_date": end})
        if not src_map:
            raise DataSourceError(f"sector_history: no applicable source for BK {symbol}")
        df, _ = self._call("sector_history", src_map)
        return df

    def get_sector_fund_flow_history(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        df, _ = self._call("sector_fund_flow_history", {k: (f, {**kw, "symbol": symbol, "start_date": start, "end_date": end}) for k, (f, kw) in self._SECTOR_FLOW_HIST.items()})
        return df

    def get_market_breadth_raw(self) -> pd.DataFrame:
        df, src = self._call("market_breadth_raw", self._BREADTH_RAW)
        df.attrs["__source"] = src
        return df
