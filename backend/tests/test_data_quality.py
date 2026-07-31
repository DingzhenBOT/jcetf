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


def test_etf_amount_volume_unit_guard_detects_lot_share_mixup():
    now = datetime(2024, 1, 2, 2, 0, 0)
    good = [{
        "symbol_type": "ETF", "volume_unit": "shares", "close": 4.0,
        "volume": 1_000_000.0, "amount": 4_000_000.0, "change_percent": 1.0,
    }]
    bad = [{
        "symbol_type": "ETF", "volume_unit": "shares", "close": 4.0,
        "volume": 10_000.0, "amount": 4_000_000.0, "change_percent": 1.0,
    }]
    assess(good, is_trading_now=False, now=now, cfg=_cfg())
    assess(bad, is_trading_now=False, now=now, cfg=_cfg())
    assert good[0]["data_quality_status"] == "OK"
    assert bad[0]["data_quality_status"] == "ANOMALY"


# --------------------------------------------------------------------------- #
# #67 OHLC 关系/跨度异常校验
# --------------------------------------------------------------------------- #
def _ohlc(o, h, l, c, chg=1.0):
    return {"open": o, "high": h, "low": l, "close": c, "change_percent": chg,
            "main_net_inflow": None}


def test_ohlc_ok_for_normal_bar():
    now = datetime(2024, 1, 2, 2, 0, 0)
    rows = [{"open": 1.0, "high": 1.05, "low": 0.98, "close": 1.02, "change_percent": 2.0}]
    assess(rows, is_trading_now=False, now=now, cfg=_cfg())
    assert rows[0]["data_quality_status"] == "OK"


def test_ohlc_anomaly_high_below_low():
    """512000 翻版：高 0.525 < 低 0.526 -> 高低颠倒，判 ANOMALY。"""
    now = datetime(2024, 1, 2, 2, 0, 0)
    rows = [_ohlc(346.0, 0.525, 0.526, 0.535)]
    assess(rows, is_trading_now=False, now=now, cfg=_cfg())
    assert rows[0]["data_quality_status"] == "ANOMALY"


def test_ohlc_anomaly_open_outside_range():
    """开 346 远超其余价 -> 跨度 346/0.5≈692 > 阈值，判 ANOMALY（512000 同类）。"""
    now = datetime(2024, 1, 2, 2, 0, 0)
    rows = [_ohlc(346.0, 0.55, 0.50, 0.53)]
    assess(rows, is_trading_now=False, now=now, cfg=_cfg())
    assert rows[0]["data_quality_status"] == "ANOMALY"


def test_ohlc_anomaly_span_too_large():
    """四价齐全但跨度 > max_price_span_ratio(4.0) -> 判 ANOMALY。"""
    now = datetime(2024, 1, 2, 2, 0, 0)
    rows = [_ohlc(1.0, 5.0, 0.9, 4.5)]  # span 5.0/0.9 ≈ 5.6
    assess(rows, is_trading_now=False, now=now, cfg=_cfg())
    assert rows[0]["data_quality_status"] == "ANOMALY"


def test_ohlc_ok_at_span_boundary():
    """跨度恰为阈值内（如 1.1）应判 OK，不误伤正常 K 线。"""
    now = datetime(2024, 1, 2, 2, 0, 0)
    rows = [_ohlc(1.0, 1.1, 1.0, 1.1)]  # span 1.1
    assess(rows, is_trading_now=False, now=now, cfg=_cfg())
    assert rows[0]["data_quality_status"] == "OK"


def test_ohlc_partial_no_false_positive():
    """部分价（仅 close）不应误判 ANOMALY。"""
    now = datetime(2024, 1, 2, 2, 0, 0)
    rows = [{"close": 10.0, "change_percent": 1.0, "main_net_inflow": None}]
    assess(rows, is_trading_now=False, now=now, cfg=_cfg())
    assert rows[0]["data_quality_status"] == "OK"
