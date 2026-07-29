"""美股对 A股影响分析（#109）。

跨市场传导口径：美股（道琼斯/纳斯达克/标普500）隔夜收盘涨跌 → A股次日（沪深300 等宽基）反应。

> 为什么是「美股隔夜 → A股次日」？
> 美股交易时段（美东）收盘时，北京时间通常已是次日凌晨；A股次日早盘即对该美股波动做出反应。
> 在数据上，美股本地交易日 d 对应的 A股反应日，正是「严格晚于 d 的最近一个 A股交易日」。
> 故对每个美股交易日 d，取其后最近 A股交易日的 A股收益率，作为「美股→A股」传导配对。

指标：
- 近期窗口（默认最近 20 个配对）/ 长期窗口（默认最近 60 个配对）的 Pearson 相关系数。
- β：以 A股次日收益对美股收益回归的斜率（cov(us, a) / var(us)），刻画 A股对美股波动的弹性。
- 近期传导明细（最近 15 对）：美股日、美股 %、A股反应日、A股次日 %。

数据约束：美股日线来自 US_INDEX BAR（akshare sina 源回填），A股宽基来自 INDEX BAR。
任一侧日线不足时优雅降级（available=False + note），接口本身不抛 500。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from app.config import get_settings
from app.market_calendar import trading_date_for
from app.repository import quote_repo

# 美股代码 → 中文名（与 settings.strategy.us_index_codes / 首页「美股大盘」一致）。
US_INDEX_NAME: Dict[str, str] = {
    "usDJI": "道琼斯",
    "usIXIC": "纳斯达克",
    "usINX": "标普500",
}

# 配对/窗口参数
_MIN_PAIRS = 8          # 少于此配对数不计算相关性（统计无意义）
_RECENT_N = 20          # 近期窗口配对个数
_LONG_N = 60            # 长期窗口配对个数
_MAX_DAYS = 400         # 回溯自然日（≈270 交易日，足以支撑 60 配对窗口）
_RECENT_TABLE = 15      # 近期传导明细条数


def _daily_returns(rows: List[Any]) -> Dict[date, float]:
    """升序 MarketQuote BAR 列表 → {trading_date: 日收益率}。

    用相邻交易日收盘算收益；缺口（相邻两根非连续）会自然断档，不跨缺口拼接。
    """
    ret: Dict[date, float] = {}
    prev_close: Optional[float] = None
    for r in rows:
        c = r.close
        if c is None or prev_close in (None, 0):
            prev_close = c
            continue
        ret[r.trading_date] = c / prev_close - 1.0
        prev_close = c
    return ret


def _pair_us_next_a(
    us_ret: Dict[date, float], a_ret: Dict[date, float]
) -> List[Tuple[date, float, date, float]]:
    """对每根美股交易日 d，取「严格晚于 d 的最近 A股交易日」收益，构成传导配对。

    返回 [(us_date, us_r, a_date, a_r), ...]，按美股日升序。
    """
    a_dates = sorted(a_ret.keys())
    if not a_dates:
        return []
    pairs: List[Tuple[date, float, date, float]] = []
    for ud in sorted(us_ret.keys()):
        later = [ad for ad in a_dates if ad > ud]
        if not later:
            continue
        ad = later[0]
        pairs.append((ud, us_ret[ud], ad, a_ret[ad]))
    return pairs


def _safe_corr(x: np.ndarray, y: np.ndarray) -> Optional[float]:
    if len(x) < 2:
        return None
    if np.std(x) == 0 or np.std(y) == 0:
        return None
    c = np.corrcoef(x, y)[0, 1]
    return None if np.isnan(c) else float(c)


def _corr_beta(pairs: List[Tuple[date, float, date, float]]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """返回 (近期相关, 长期相关, β)。配对不足返回 (None, None, None)。"""
    if len(pairs) < _MIN_PAIRS:
        return None, None, None
    us = np.array([p[1] for p in pairs], dtype=float)
    a = np.array([p[3] for p in pairs], dtype=float)

    # 长期窗口
    lu, la = us[-_LONG_N:], a[-_LONG_N:]
    corr_long = _safe_corr(lu, la)
    beta: Optional[float] = None
    if np.std(lu) > 0:
        beta = float(np.cov(lu, la)[0, 1] / np.var(lu))
        if np.isnan(beta):
            beta = None

    # 近期窗口
    ru, ra = us[-_RECENT_N:], a[-_RECENT_N:]
    corr_recent = _safe_corr(ru, ra)
    return corr_recent, corr_long, beta


def _round(v: Optional[float], n: int = 2) -> Optional[float]:
    return None if v is None else round(v, n)


def compute_us_impact(
    session: Any,
    *,
    benchmark_codes: Optional[List[str]] = None,
    primary_benchmark: Optional[str] = None,
) -> Dict[str, Any]:
    """计算美股三大指数对 A股宽基（默认沪深300）的近期影响。

    返回结构（均不抛异常，缺数据降级）：
    {
      "generated_at": "YYYY-MM-DD",
      "primary_benchmark": "000300",
      "primary_benchmark_name": "沪深300",
      "items": [ {code, name, available, current_change_percent,
                  correlation_recent, correlation_long, beta, pair_count,
                  recent: [{us_date, us_pct, ashare_date, ashare_pct}, ...],
                  note} , ... ]
    }
    """
    s = get_settings()
    bench_codes = benchmark_codes or list(s.strategy.broad_index_codes)
    primary = primary_benchmark or (bench_codes[0] if bench_codes else "000300")

    end = trading_date_for()
    start = end - timedelta(days=_MAX_DAYS)

    # A股宽基日收益（多基准都取，primary 用于 headline）
    bench_ret: Dict[str, Dict[date, float]] = {}
    for b in bench_codes:
        rows = quote_repo.get_bar_history(session, "INDEX", b, start, end, timeframe="1d", data_kind="BAR")
        bench_ret[b] = _daily_returns(rows)
    primary_a = bench_ret.get(primary, {})

    INDEX_LABELS = {
        "000300": "沪深300", "000001": "上证综指", "399001": "深证成指", "399006": "创业板指",
    }

    items: List[Dict[str, Any]] = []
    for code in list(s.strategy.us_index_codes):
        name = US_INDEX_NAME.get(code, code)
        us_rows = quote_repo.get_bar_history(session, "US_INDEX", code, start, end, timeframe="1d", data_kind="BAR")
        us_ret = _daily_returns(us_rows)

        # 当前涨跌（最新 SNAPSHOT，若有）
        cur = quote_repo.get_latest_quote(
            session, "US_INDEX", code, data_kind="SNAPSHOT", timeframe="snapshot"
        )
        current_change = getattr(cur, "change_percent", None) if cur else None

        if len(us_ret) < 30 or len(primary_a) < 30:
            items.append({
                "code": code, "name": name, "available": False,
                "current_change_percent": current_change,
                "correlation_recent": None, "correlation_long": None, "beta": None,
                "pair_count": 0, "recent": [],
                "note": "美股/A股宽基日线不足（观察期数据不足），待每日 16:30 回填累积后自动可用。",
            })
            continue

        pairs = _pair_us_next_a(us_ret, primary_a)
        # 丢弃「A股反应日 = 最近 A股日」的配对：该日大概率未收盘，用盘中价当收益会失真
        max_a = max(primary_a.keys())
        pairs = [p for p in pairs if p[2] != max_a]

        corr_recent, corr_long, beta = _corr_beta(pairs)
        recent = [
            {
                "us_date": p[0].isoformat(),
                "us_pct": round(p[1] * 100, 2),
                "ashare_date": p[2].isoformat(),
                "ashare_pct": round(p[3] * 100, 2),
            }
            for p in pairs[-_RECENT_TABLE:]
        ]
        items.append({
            "code": code, "name": name, "available": True,
            "current_change_percent": current_change,
            "correlation_recent": _round(corr_recent),
            "correlation_long": _round(corr_long),
            "beta": _round(beta),
            "pair_count": len(pairs),
            "recent": recent,
            "note": None,
        })

    return {
        "generated_at": end.isoformat(),
        "primary_benchmark": primary,
        "primary_benchmark_name": INDEX_LABELS.get(primary, primary),
        "items": items,
    }
