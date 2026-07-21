"""列归一化（DESIGN §3.1 / P2）。

把 AkShare 返回的「中文列名 DataFrame」映射到 market_quote / market_breadth 的字典形态。
设计要点（对齐 P-1/P-1b 真实探针结论）：
- 源无时间戳列（指数/ETF 快照）-> source_timestamp=None, timestamp=collected_at（UTC）。
- 源有时间戳（stock_zh_a_spot 的「时间戳」）-> 解析为北京时间再转 UTC。
- 缺失字段一律写 None；不臆造；由 data_quality 标记 STALE/MISSING/ANOMALY。
- 板块来源异构：em 用「板块代码/最新价/涨跌幅/上涨家数/下跌家数」；
  ths 用「行业/行业指数/行业-涨跌幅/净额/公司家数」。统一取首个可用列。
- 资金持续性仅同数据源同口径（metric_source=source），切源由 collector 标 source_switched。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from app.market_calendar import beijing_to_utc, trading_date_for

# 资金流等指标口径版本；口径变更需升版并在此同步
METRIC_DEF_VERSION = "v1"


# --------------------------------------------------------------------------- #
# 基础转换
# --------------------------------------------------------------------------- #
def _f(v: Any) -> Optional[float]:
    """转 float；空/非数字/NaN -> None。处理 AkShare 常见的 '—' / '' / 'nan'。"""
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        if s in ("", "-", "—", "None", "none", "nan", "NaN"):
            return None
        try:
            f = float(s.replace(",", ""))
        except ValueError:
            return None
    else:
        try:
            f = float(v)
        except (ValueError, TypeError):
            return None
    if f != f:  # NaN
        return None
    return f


def _code(v: Any) -> Optional[str]:
    if v is None:
        return None
    try:
        if v != v:  # NaN（含 numpy.float64）
            return None
    except TypeError:
        pass
    s = str(v).strip()
    return s or None


def _parse_bj_time(v: Any) -> Optional[datetime]:
    """解析「YYYY-MM-DD HH:MM:SS」为北京时间 -> naive UTC；失败返回 None。"""
    if v is None or (isinstance(v, float) and v != v):
        return None
    s = str(v).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            bj = datetime.strptime(s, fmt)
            return beijing_to_utc(bj)
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------- #
# 快照（指数 / ETF / 板块）
# --------------------------------------------------------------------------- #
def _base_row(source: str, symbol_type: str, symbol: str, collected_at: datetime) -> Dict[str, Any]:
    return {
        "data_source": source,
        "symbol_type": symbol_type,
        "symbol": symbol,
        "data_kind": "SNAPSHOT",
        "timeframe": "snapshot",
        "trading_date": trading_date_for(collected_at),
        "timestamp": collected_at,  # 源无时间戳时 = 采集时刻
        "open": None,
        "high": None,
        "low": None,
        "close": None,
        "previous_close": None,
        "volume": None,
        "amount": None,
        "change_percent": None,
        "turnover_rate": None,
        "main_net_inflow": None,
        "large_order_inflow": None,
        "rise_count": None,
        "fall_count": None,
        "limit_up_count": None,
        "limit_down_count": None,
        "collected_at": collected_at,
        "source_timestamp": None,
        "metric_source": source,
        "metric_definition_version": METRIC_DEF_VERSION,
        "source_switched": 0,
        "data_quality_status": "OK",
    }


def normalize_index_snapshot(df: pd.DataFrame, source: str, collected_at: datetime) -> List[Dict[str, Any]]:
    """宽基/主要指数快照：代码/名称/最新价/涨跌额/涨跌幅/昨收/今开/最高/最低/成交量/成交额。"""
    rows: List[Dict[str, Any]] = []
    for _, r in df.iterrows():
        code = _code(r.get("代码"))
        if not code:
            continue
        row = _base_row(source, "INDEX", code, collected_at)
        row.update(
            open=_f(r.get("今开")),
            high=_f(r.get("最高")),
            low=_f(r.get("最低")),
            close=_f(r.get("最新价")),
            previous_close=_f(r.get("昨收")),
            volume=_f(r.get("成交量")),
            amount=_f(r.get("成交额")),
            change_percent=_f(r.get("涨跌幅")),
        )
        rows.append(row)
    return rows


def normalize_etf_snapshot(df: pd.DataFrame, source: str, collected_at: datetime) -> List[Dict[str, Any]]:
    """ETF 快照：fund_etf_spot_em / fund_etf_category_sina（含 换手率 列时取之）。"""
    rows: List[Dict[str, Any]] = []
    for _, r in df.iterrows():
        code = _code(r.get("代码"))
        if not code:
            continue
        row = _base_row(source, "ETF", code, collected_at)
        row.update(
            open=_f(r.get("今开")),
            high=_f(r.get("最高")),
            low=_f(r.get("最低")),
            close=_f(r.get("最新价")),
            previous_close=_f(r.get("昨收")),
            volume=_f(r.get("成交量")),
            amount=_f(r.get("成交额")),
            change_percent=_f(r.get("涨跌幅")),
            turnover_rate=_f(r.get("换手率")),
        )
        rows.append(row)
    return rows


def normalize_sector_ranking(
    df: pd.DataFrame, source: str, sector_type: str, collected_at: datetime
) -> List[Dict[str, Any]]:
    """板块排行+资金流（异构来源）。

    em  `stock_board_industry_name_em`：板块代码/最新价/涨跌幅/换手率/上涨家数/下跌家数
    ths `stock_fund_flow_industry/concept`：行业(名称)/行业指数/行业-涨跌幅/净额/公司家数
    统一取首个可用列；缺失写 None。
    """
    rows: List[Dict[str, Any]] = []
    for _, r in df.iterrows():
        # symbol：em 用板块代码；ths 用行业/概念名称（无代码）
        code = (
            _code(r.get("板块代码"))
            or _code(r.get("行业"))
            or _code(r.get("概念"))
            or _code(r.get("代码"))
        )
        if not code:
            continue
        # close：优先「最新价」（em），其次「行业指数 / 当前价」（ths 板块指数）
        close = _f(r.get("最新价"))
        if close is None:
            close = _f(r.get("行业指数")) or _f(r.get("当前价"))
        change = _f(r.get("涨跌幅")) or _f(r.get("行业-涨跌幅"))
        row = _base_row(source, sector_type, code, collected_at)
        row.update(
            close=close,
            change_percent=change,
            turnover_rate=_f(r.get("换手率")),
            main_net_inflow=_f(r.get("净额")),
            rise_count=_f(r.get("上涨家数")),
            fall_count=_f(r.get("下跌家数")),
        )
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- #
# 全市场宽度（每日累计；无历史 API，DESIGN §3.1）
# --------------------------------------------------------------------------- #
def normalize_breadth(df: pd.DataFrame, source: str, collected_at: datetime) -> Dict[str, Any]:
    """从全市场快照（stock_zh_a_spot）计算涨跌家数/涨跌停/总成交额。

    涨停/跌停阈值取 9.5%（覆盖主板 ±10%，ST ±5% 不单独处理，已知近似限制）。
    时间戳取首个非空「时间戳」解析为 UTC；缺失则用 collected_at。
    """
    change_col = "涨跌幅" if "涨跌幅" in df.columns else None
    if change_col:
        chg = pd.to_numeric(df[change_col], errors="coerce").fillna(0.0)
        total_rise = int((chg > 0).sum())
        total_fall = int((chg < 0).sum())
        total_flat = int((chg == 0).sum())
        limit_up = int((chg >= 9.5).sum())
        limit_down = int((chg <= -9.5).sum())
    else:
        total_rise = total_fall = total_flat = limit_up = limit_down = None

    amount_col = "成交额" if "成交额" in df.columns else None
    total_amount = (
        float(pd.to_numeric(df[amount_col], errors="coerce").sum()) if amount_col else None
    )

    # 源时间戳（北京）-> UTC
    src_ts: Optional[datetime] = None
    if "时间戳" in df.columns:
        for v in df["时间戳"]:
            src_ts = _parse_bj_time(v)
            if src_ts is not None:
                break
    ts = src_ts or collected_at

    return {
        "trading_date": trading_date_for(collected_at),
        "timestamp": ts,
        "total_rise": total_rise,
        "total_fall": total_fall,
        "total_flat": total_flat,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "total_amount": total_amount,
        "data_source": source,
        "collected_at": collected_at,
        "data_quality_status": "OK",
    }


# --------------------------------------------------------------------------- #
# 历史 BAR（ETF / 指数 / 板块趋势 / 板块资金流，data_kind="BAR", timeframe="1d"）
# --------------------------------------------------------------------------- #
def _parse_date(v: Any) -> Optional[date]:
    """解析日期列（akshare 多为 'YYYY-MM-DD' 或 Timestamp）为 date；失败返回 None。"""
    if v is None or (isinstance(v, float) and v != v):
        return None
    try:
        ts = pd.to_datetime(v, errors="coerce")
    except Exception:  # noqa: BLE001
        return None
    if ts is None or ts != ts:  # NaT
        return None
    return ts.date()


def _bar_row(
    source: str, symbol_type: str, symbol: str, bar_date: date, collected_at: datetime
) -> Dict[str, Any]:
    """BAR 基础形态。timestamp = 该交易日的 UTC naive 午夜（确定性；trading_date = 该日）。

    历史数据不校验时间新鲜度 -> data_quality_status 固定 "OK"。
    metric_source = source：资金持续性必须「同数据源同口径」（见 sector_engine）。
    """
    return {
        "data_source": source,
        "symbol_type": symbol_type,
        "symbol": symbol,
        "data_kind": "BAR",
        "timeframe": "1d",
        "trading_date": bar_date,
        "timestamp": datetime(bar_date.year, bar_date.month, bar_date.day),
        "open": None,
        "high": None,
        "low": None,
        "close": None,
        "previous_close": None,
        "volume": None,
        "amount": None,
        "change_percent": None,
        "turnover_rate": None,
        "main_net_inflow": None,
        "large_order_inflow": None,
        "rise_count": None,
        "fall_count": None,
        "limit_up_count": None,
        "limit_down_count": None,
        "collected_at": collected_at,
        "source_timestamp": None,
        "metric_source": source,
        "metric_definition_version": METRIC_DEF_VERSION,
        "source_switched": 0,
        "data_quality_status": "OK",
    }


def _col(r, *names):
    """取首个非空列名对应的值（兼容同一字段的中英文列名，如 开盘/open、日期/date）。"""
    for n in names:
        v = r.get(n)
        if v is not None:
            return v
    return None


def normalize_etf_bar(
    df: pd.DataFrame, source: str, symbol: str, collected_at: datetime
) -> List[Dict[str, Any]]:
    """ETF 日线 BAR：兼容 em 中文列（日期/开盘/收盘...）与新浪英文列（date/open/close...）。"""
    rows: List[Dict[str, Any]] = []
    for _, r in df.iterrows():
        d = _parse_date(_col(r, "日期", "date"))
        if d is None:
            continue
        row = _bar_row(source, "ETF", symbol, d, collected_at)
        row.update(
            open=_f(_col(r, "开盘", "open")),
            high=_f(_col(r, "最高", "high")),
            low=_f(_col(r, "最低", "low")),
            close=_f(_col(r, "收盘", "close")),
            volume=_f(_col(r, "成交量", "volume")),
            amount=_f(_col(r, "成交额", "amount")),
            change_percent=_f(_col(r, "涨跌幅", "change_percent", "change")),
            turnover_rate=_f(_col(r, "换手率", "turnover_rate")),
        )
        rows.append(row)
    return rows


def normalize_index_bar(
    df: pd.DataFrame, source: str, symbol: str, collected_at: datetime
) -> List[Dict[str, Any]]:
    """指数日线 BAR（stock_zh_index_daily_em / _tx / sina）：date,open,high,low,close,volume（无 amount/change）。"""
    rows: List[Dict[str, Any]] = []
    for _, r in df.iterrows():
        d = _parse_date(_col(r, "date", "日期"))
        if d is None:
            continue
        row = _bar_row(source, "INDEX", symbol, d, collected_at)
        row.update(
            open=_f(_col(r, "open", "开盘")),
            high=_f(_col(r, "high", "最高")),
            low=_f(_col(r, "low", "最低")),
            close=_f(_col(r, "close", "收盘")),
            volume=_f(_col(r, "volume", "成交量")),
            amount=_f(_col(r, "amount", "成交额")),
            change_percent=_f(_col(r, "change", "涨跌幅")),
        )
        rows.append(row)
    return rows


def normalize_sector_bar(
    df: pd.DataFrame, source: str, symbol: str, collected_at: datetime
) -> List[Dict[str, Any]]:
    """板块趋势日线 BAR。兼容两种来源列名：

    - em（stock_board_industry_hist_em）：日期,开盘,收盘,最高,最低,成交量,成交额,涨跌幅,换手率
    - ths（stock_board_industry/concept_index_ths）：日期,开盘价,最高价,最低价,收盘价,成交量,成交额
      （无涨跌幅/换手率 -> change_percent/turnover_rate 为 None）

    symbol_type 统一存 "SECTOR"（行业/概念历史 BAR 共用，按代码查询；与快照 INDUSTRY/CONCEPT 区分于 data_kind）。
    """
    rows: List[Dict[str, Any]] = []
    for _, r in df.iterrows():
        d = _parse_date(r.get("日期")) or _parse_date(r.get("date"))
        if d is None:
            continue
        row = _bar_row(source, "SECTOR", symbol, d, collected_at)
        row.update(
            open=_f(_col(r, "开盘", "开盘价")),
            high=_f(_col(r, "最高", "最高价")),
            low=_f(_col(r, "最低", "最低价")),
            close=_f(_col(r, "收盘", "收盘价")),
            volume=_f(_col(r, "成交量")),
            amount=_f(_col(r, "成交额")),
            change_percent=_f(_col(r, "涨跌幅")),
            turnover_rate=_f(_col(r, "换手率")),
        )
        rows.append(row)
    return rows


def normalize_sector_fund_flow_bar(
    df: pd.DataFrame, source: str, symbol: str, collected_at: datetime
) -> List[Dict[str, Any]]:
    """板块历史资金流（stock_sector_fund_flow_hist）：日期,主力净流入-净额,主力净流入-净占比,超大单净流入-净额,…。

    仅取主力净额/超大单净额（用于资金持续性）；symbol_type 同存 "SECTOR"。
    """
    rows: List[Dict[str, Any]] = []
    for _, r in df.iterrows():
        d = _parse_date(r.get("日期")) or _parse_date(r.get("date"))
        if d is None:
            continue
        row = _bar_row(source, "SECTOR", symbol, d, collected_at)
        row.update(
            main_net_inflow=_f(r.get("主力净流入-净额")),
            large_order_inflow=_f(r.get("超大单净流入-净额")),
            amount=_f(r.get("成交额")),
            close=_f(r.get("收盘")),
            change_percent=_f(r.get("涨跌幅")),
        )
        rows.append(row)
    return rows
