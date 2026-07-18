"""P2 data_quality 测试：OK / MISSING / ANOMALY / STALE / DELAY 判定。"""
from datetime import datetime, timedelta, timezone

from app.config import DataQualityConfig
from app.data_quality.checker import assess


def _cfg(**kw) -> DataQualityConfig:
    return DataQualityConfig(**kw)


def test_ok_when_fresh_and_valid():
    now = datetime(2024, 1, 2, 2, 0, 0)
    rows = [{"close": 10.0, "change_percent": 1.0, "source_timestamp": None, "timestamp": now}]
    assess(rows, is_trading_now=True, now=now, cfg=_cfg())
    assert rows[0]["data_quality_status"] == "OK"


def test_missing_when_all_key_fields_null():
    now = datetime(2024, 1, 2, 2, 0, 0)
    rows = [{"close": None, "change_percent": None, "main_net_inflow": None}]
    assess(rows, is_trading_now=True, now=now, cfg=_cfg())
    assert rows[0]["data_quality_status"] == "MISSING"


def test_anomaly_on_nonpositive_price():
    now = datetime(2024, 1, 2, 2, 0, 0)
    rows = [{"close": 0.0, "change_percent": 1.0}]
    assess(rows, is_trading_now=True, now=now, cfg=_cfg())
    assert rows[0]["data_quality_status"] == "ANOMALY"


def test_anomaly_on_change_percent_over_limit():
    now = datetime(2024, 1, 2, 2, 0, 0)
    rows = [{"close": 10.0, "change_percent": 15.0}]
    assess(rows, is_trading_now=True, now=now, cfg=_cfg())
    assert rows[0]["data_quality_status"] == "ANOMALY"


def test_stale_and_delay_only_when_trading_and_old_source():
    now = datetime(2024, 1, 2, 2, 0, 0)
    old = now - timedelta(seconds=2000)  # > stale (1800)
    rows = [{"close": 10.0, "change_percent": 1.0, "source_timestamp": old, "timestamp": now}]
    assess(rows, is_trading_now=True, now=now, cfg=_cfg())
    assert rows[0]["data_quality_status"] == "STALE"

    delay = now - timedelta(seconds=300)  # > delay (120) but < stale
    rows2 = [{"close": 10.0, "change_percent": 1.0, "source_timestamp": delay, "timestamp": now}]
    assess(rows2, is_trading_now=True, now=now, cfg=_cfg())
    assert rows2[0]["data_quality_status"] == "DELAY"


def test_not_stale_when_market_closed():
    now = datetime(2024, 1, 2, 2, 0, 0)
    old = now - timedelta(seconds=4000)  # 很旧，但收盘后不惩罚
    rows = [{"close": 10.0, "change_percent": 1.0, "source_timestamp": old, "timestamp": now}]
    assess(rows, is_trading_now=False, now=now, cfg=_cfg())
    assert rows[0]["data_quality_status"] == "OK"
