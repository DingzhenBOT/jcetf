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
- 板块历史资金流仅 em 提供（ths 仅当日快照无历史），腾讯云降级（D4）；
  新版 akshare（≥1.18）的 stock_sector_fund_flow_hist 仅接受板块名称（非 BK 代码）且返回全量历史，
  故 get_sector_fund_flow_history 先按 BK 解析东财板块名再调用，并按 [start,end] 裁剪。
"""
from __future__ import annotations

import inspect
from datetime import date
from functools import lru_cache
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


def _filter_kwargs(func, kwargs: dict) -> dict:
    """按目标 akshare 函数的真实签名过滤 kwargs，忽略版本升级后不再接受的参数。

    akshare 多版本间函数签名漂移（如 stock_sector_fund_flow_hist 旧版接受
    period/start/end，新版仅接受 symbol），硬性传参会抛 TypeError。此过滤器让适配器对版本
    漂移容错：仅透传函数真实接受的参数；若函数签名含 **kwargs 则全部透传。
    """
    try:
        sig = inspect.signature(func)
    except (ValueError, TypeError):
        return dict(kwargs)
    params = sig.parameters
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return dict(kwargs)
    accepted = {
        name
        for name, p in params.items()
        if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
    return {k: v for k, v in kwargs.items() if k in accepted}


def _filter_df_by_date_range(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    """按 [start, end]（YYYYMMDD）裁剪含「日期」/「date」列的 DataFrame。

    部分历史接口（如 stock_sector_fund_flow_hist）返回全量历史、不接受日期参数，
    需在获取后按区间裁剪。无日期列或解析失败则原样返回。
    """
    if df is None or getattr(df, "empty", True):
        return df
    col = "日期" if "日期" in df.columns else ("date" if "date" in df.columns else None)
    if col is None:
        return df
    s = pd.to_datetime(start, errors="coerce")
    e = pd.to_datetime(end, errors="coerce")
    if s is None or e is None:
        return df
    dts = pd.to_datetime(df[col], errors="coerce")
    mask = (dts >= s) & (dts <= e)
    return df[mask].reset_index(drop=True)


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
                    df = func(**_filter_kwargs(func, kwargs))
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
        kind = (kind or "").lower()
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
    # 板块历史/资金流不再用静态 source map：
    # - get_sector_history 内联构造（em 用 BK 代码；ths 经 _bk_to_ths 解析板块名）。
    # - get_sector_fund_flow_history 需按 BK 解析东财板块名称后调用（新版 akshare 仅接受名称，见该方法）。

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

    def get_open_fund_nav_history(
        self, symbol: str, start: Optional[str] = None, end: Optional[str] = None
    ) -> pd.DataFrame:
        """场外开放式基金单位净值历史（东财天天基金 pingzhongdata JS，CVM 可达）。

        与 RST 拦截的 push2 不同主机（fund.eastmoney.com），腾讯云/生产网一般可达。
        返回 DataFrame[date(python date), nav(float), change_percent(float|None)]（英文列，便于归一化）。
        空/异常直接抛 DataSourceError（由 Collector._collect_bar 捕获记 FAILED 降级，不向上抛）。
        """
        try:
            df = ak.fund_open_fund_info_em(symbol=symbol, indicator="单位净值走势", period="成立来")
        except Exception as e:  # noqa: BLE001
            raise DataSourceError(f"open_fund_nav em {symbol}: {type(e).__name__}: {e}")
        if df is None or getattr(df, "empty", True):
            raise DataSourceError(f"open_fund_nav em {symbol} returned empty")
        df = df.copy()
        # akshare 返回中文列：净值日期 / 单位净值 / 日增长率
        df["date"] = pd.to_datetime(df["净值日期"], errors="coerce").dt.date
        df["nav"] = pd.to_numeric(df["单位净值"], errors="coerce")
        df["change_percent"] = pd.to_numeric(df.get("日增长率"), errors="coerce")
        df = df[["date", "nav", "change_percent"]].dropna(subset=["date", "nav"])
        if df.empty:
            raise DataSourceError(f"open_fund_nav em {symbol}: no parseable NAV rows")
        out = df[["date", "nav", "change_percent"]].copy()
        out.attrs["__source"] = "em"
        return out

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
                    # 新版 akshare 的 period 取值为 '日k'（旧版 'daily'）；_filter_kwargs 已对版本漂移容错
                    {"symbol": symbol, "period": "日k", "adjust": "qfq", "start_date": start, "end_date": end},
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
        """板块历史资金流（主力/超大单净流入）。

        - 新版 akshare（≥1.18）的 stock_sector_fund_flow_hist / stock_concept_fund_flow_hist
          仅接受「板块名称」（非 BK 代码），且无日期参数（返回全量历史）。
        - 故先按 BK 代码解析东财板块名称（行业/概念分别查），再调用对应函数，仅传 symbol；
          返回后按 [start, end] 裁剪日期。
        - 东财不可达（如腾讯云 RST 拦截）时解析失败 -> 跳过 em 源 -> DataSourceError 优雅降级。
        """
        src_map: Dict[str, Tuple[str, dict]] = {}
        for src in self._ordered_sources():
            if src != "em":
                continue
            resolved = self._bk_to_em_fund_flow_name(symbol)
            if resolved is None:
                continue
            func_name, name = resolved
            src_map[src] = (func_name, {"symbol": name})
        if not src_map:
            raise DataSourceError(f"sector_fund_flow_history: no applicable source for BK {symbol}")
        df, src = self._call("sector_fund_flow_history", src_map)
        df = _filter_df_by_date_range(df, start, end)
        df.attrs["__source"] = src
        return df

    @staticmethod
    @lru_cache(maxsize=1)
    def _em_board_name_maps() -> Tuple[pd.DataFrame, pd.DataFrame]:
        """东财行业/概念板块 代码->名称 映射（公开 API，缓存一次；不可达则抛异常由调用方降级）。"""
        ind = ak.stock_board_industry_name_em()
        con = ak.stock_board_concept_name_em()
        return ind, con

    def _bk_to_em_fund_flow_name(self, bk: str) -> Optional[Tuple[str, str]]:
        """BK 代码 -> (资金流函数名, 东财板块名称)；行业/概念分别查，无则返回 None。"""
        try:
            ind, con = self._em_board_name_maps()
        except Exception:  # noqa: BLE001
            return None

        def lookup(df: pd.DataFrame) -> Optional[str]:
            if df is None or getattr(df, "empty", True):
                return None
            if "板块代码" not in df.columns or "板块名称" not in df.columns:
                return None
            sub = df[df["板块代码"] == bk]
            if len(sub):
                return str(sub.iloc[0]["板块名称"])
            return None

        name = lookup(ind)
        if name is not None:
            return ("stock_sector_fund_flow_hist", name)
        name = lookup(con)
        if name is not None:
            return ("stock_concept_fund_flow_hist", name)
        return None

    def get_market_breadth_raw(self) -> pd.DataFrame:
        df, src = self._call("market_breadth_raw", self._BREADTH_RAW)
        df.attrs["__source"] = src
        return df


# 系统美股代码（腾讯财经 qt.gtimg.cn，与首页「美股大盘」展示一致）→ akshare 美股指数代码。
# akshare.index_us_stock_sina 以 sina 源返回完整日线 OHLC（CVM 可达，优于被 JS 墙的 stooq
# 与腾讯 fqkline 对美股仅返回单根 day 的异常行为）。
US_INDEX_AKSHARE_SYMBOL: Dict[str, str] = {
    "usDJI": ".DJI",
    "usIXIC": ".IXIC",
    "usINX": ".INX",
}


def get_us_index_history(symbol: str, start: date, end: date) -> pd.DataFrame:
    """美股指数日线（akshare index_us_stock_sina，sina 源，CVM 可达）。

    symbol 为 akshare 代码（如 '.DJI'/'.IXIC'/'.INX'）；返回 DataFrame
    [date(python date), open, high, low, close, volume]，仅保留 [start, end] 区间内行。
    任意失败直接抛出（由 Collector._collect_bar 捕获并记 FAILED 降级，不向上抛）。
    """
    df = ak.index_us_stock_sina(symbol=symbol)
    if df is None or getattr(df, "empty", True):
        raise ValueError(f"akshare index_us_stock_sina({symbol}) returned empty")
    # akshare 偶发只读 numpy 数组，复制避免后续赋值 read-only 报错
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df[(df["date"] >= start) & (df["date"] <= end)]
    if df.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    out = df[["date", "open", "high", "low", "close", "volume"]].copy()
    out.attrs["__source"] = "sina_us"
    return out
