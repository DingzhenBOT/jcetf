"""P2 market_calendar 测试：北京时间换算 / 交易时段 / 交易日判定。"""
from datetime import date, datetime, timedelta, timezone

import app.market_calendar as cal


def _set_cal(days):
    saved = cal._CALENDAR
    cal._CALENDAR = set(days)
    return saved


def test_beijing_offset_conversion():
    utc = datetime(2024, 1, 1, 2, 0, 0)  # naive UTC
    bj = cal.beijing_now(utc)
    assert bj == datetime(2024, 1, 1, 10, 0, 0)
    assert cal.beijing_to_utc(bj) == utc


def test_trading_date_by_beijing():
    # UTC 23:30 01-01 -> 北京 07:30 01-02
    utc = datetime(2024, 1, 1, 23, 30, 0)
    assert cal.trading_date_for(utc) == date(2024, 1, 2)


def test_is_trading_now_true_in_session():
    saved = _set_cal({"20240101"})
    try:
        # 北京 10:00（UTC 02:00）周一 -> 上午盘中
        assert cal.is_trading_now(datetime(2024, 1, 1, 2, 0, 0)) is True
        # 北京 14:00（UTC 06:00）-> 下午盘中
        assert cal.is_trading_now(datetime(2024, 1, 1, 6, 0, 0)) is True
    finally:
        cal._CALENDAR = saved


def test_is_trading_now_false_outside_session():
    saved = _set_cal({"20240101"})
    try:
        # 北京 15:30（UTC 07:30）-> 收盘后
        assert cal.is_trading_now(datetime(2024, 1, 1, 7, 30, 0)) is False
        # 北京 12:00（UTC 04:00）-> 午休
        assert cal.is_trading_now(datetime(2024, 1, 1, 4, 0, 0)) is False
        # 不在日历内
        cal._CALENDAR = {"20240102"}
        assert cal.is_trading_now(datetime(2024, 1, 1, 2, 0, 0)) is False
    finally:
        cal._CALENDAR = saved


def test_is_trading_day_heuristic_weekend():
    cal._CALENDAR = None  # 回退启发式
    # 2024-01-06 是周六
    assert cal.is_trading_day(date(2024, 1, 6)) is False
    assert cal.is_trading_day(date(2024, 1, 5)) is True  # 周五


def test_next_trading_day_skips_weekend():
    cal._CALENDAR = None
    # 从周六 2024-01-06 起，下一个交易日是周一 2024-01-08
    assert cal.next_trading_day(date(2024, 1, 6)) == date(2024, 1, 8)
