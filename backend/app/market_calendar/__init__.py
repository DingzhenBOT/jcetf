"""交易日历（DESIGN §8 R10）。

所有定时任务先查本模块再决定是否执行；全局单点判断避免各任务各自实现导致不一致。
- 时间统一以 UTC 计算；北京时间 = UTC + 8h。
- 交易日历优先从数据源加载（akshare sina），失败回退到「周一~周五且非显式休市」启发式。
- 交易时段：09:30-11:30 / 13:00-15:00（北京时间）。
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import List, Optional, Set

from app.data_provider.base import BaseDataProvider

# 北京时间相对 UTC 的偏移
BJ = timedelta(hours=8)

# 交易时段（北京时间，含边界）
_MORNING_START = time(9, 30)
_MORNING_END = time(11, 30)
_AFTERNOON_START = time(13, 0)
_AFTERNOON_END = time(15, 0)

# 模块级缓存（worker 单实例，常驻内存即可）
_CALENDAR: Optional[Set[str]] = None
_CALENDAR_PROVIDER_ID: Optional[str] = None


def beijing_now(utc_now: Optional[datetime] = None) -> datetime:
    """返回「北京时间墙钟」的 naive datetime（UTC naive + 8h）。"""
    if utc_now is None:
        utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
    return utc_now + BJ


def beijing_to_utc(bj_dt: datetime) -> datetime:
    """北京时间 -> 存储用的 naive UTC。"""
    return bj_dt - BJ


def trading_date_for(utc_now: Optional[datetime] = None) -> date:
    """按北京时间判定的交易日。"""
    return beijing_now(utc_now).date()


def _load_calendar(provider: BaseDataProvider) -> Set[str]:
    """从数据源加载交易日历（YYYYMMDD 字符串集合）；失败返回空集合。"""
    try:
        days: List[str] = provider.get_trade_calendar()
        return set(days)
    except Exception:  # noqa: BLE001 - 网络/接口异常 -> 走启发式
        return set()


def refresh_calendar(provider: BaseDataProvider) -> None:
    """重新从数据源加载日历并写入缓存。"""
    global _CALENDAR, _CALENDAR_PROVIDER_ID
    _CALENDAR = _load_calendar(provider)
    _CALENDAR_PROVIDER_ID = repr(provider)


def init_calendar(provider: Optional[BaseDataProvider] = None) -> None:
    """进程启动期调用：尝试加载日历；无 provider 或失败则置为 None（走启发式）。"""
    global _CALENDAR
    if provider is not None:
        _CALENDAR = _load_calendar(provider)
    else:
        _CALENDAR = None


def _heuristic_trading_day(d: date) -> bool:
    """无日历数据时的回退：周一~周五视为交易日（不覆盖法定节假日，仅兜底）。"""
    return d.weekday() < 5


def is_trading_day(d: date) -> bool:
    if _CALENDAR is not None:
        return d.strftime("%Y%m%d") in _CALENDAR
    return _heuristic_trading_day(d)


def is_trading_now(utc_now: Optional[datetime] = None) -> bool:
    """当前是否处于交易日 + 交易时段内（北京时间）。"""
    bj = beijing_now(utc_now)
    if not is_trading_day(bj.date()):
        return False
    t = bj.time()
    in_morning = _MORNING_START <= t <= _MORNING_END
    in_afternoon = _AFTERNOON_START <= t <= _AFTERNOON_END
    return in_morning or in_afternoon


def next_trading_day(d: Optional[date] = None) -> date:
    """返回 d（含）之后的下一个交易日。"""
    cur = d or trading_date_for()
    for _ in range(15):  # 最多看两周，避免死循环
        if is_trading_day(cur):
            return cur
        cur = cur + timedelta(days=1)
    return cur
