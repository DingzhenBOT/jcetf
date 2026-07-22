"""量价关系技术分析单测（方案B）。

用合成日线 BAR 验证：量价关系矩阵、量能状态分类、各形态识别、强度分边界。
"""
import pandas as pd

from app.indicator_engine.ta_volume_price import (
    VP_STATE_TEXT,
    analyze_volume_price,
    classify_vol_state,
)


def _bars(closes, vols):
    n = len(closes)
    return pd.DataFrame(
        {
            "timestamp": list(range(n)),
            "open": [c * 0.99 for c in closes],
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": vols,
        }
    )


def test_classify_vol_state():
    assert classify_vol_state(2.0) == "放量"
    assert classify_vol_state(1.2) == "温和放量"
    assert classify_vol_state(0.9) == "平量"
    assert classify_vol_state(0.6) == "缩量"
    assert classify_vol_state(0.3) == "极度缩量"
    assert classify_vol_state(None) == "未知"


def test_empty_and_short():
    assert analyze_volume_price(None)["vp_state"] is None
    assert analyze_volume_price(_bars([100], [1000]))["vp_state_text"] == "样本不足"


def test_breakout_volume():
    closes = [100.0] * 20 + [105.0]
    vols = [1000.0] * 20 + [2000.0]
    r = analyze_volume_price(_bars(closes, vols))
    assert r["vp_state"] == "VOL_UP_RISE"
    assert r["vp_state_text"] == VP_STATE_TEXT["VOL_UP_RISE"]
    assert "breakout_volume" in r["vp_patterns"]
    assert r["vp_vol_ratio_state"] == "放量"
    assert r["vp_strength"] is not None and r["vp_strength"] >= 85  # 放量涨+站上MA20+突破


def test_shrink_wash():
    closes = [100.0] * 20 + [99.0]  # 微跌
    vols = [1000.0] * 20 + [500.0]  # 缩量
    r = analyze_volume_price(_bars(closes, vols))
    assert r["vp_state"] == "VOL_DOWN_FALL"
    assert "shrink_wash" in r["vp_patterns"]


def test_divergence_price_high_low_vol():
    # 价创近 20 日新高，但量比 < 1（价升量缩）
    closes = [100.0 + i for i in range(20)] + [119.0]  # 末值 119 为新高
    vols = [1000.0] * 20 + [800.0]  # 末量萎缩
    r = analyze_volume_price(_bars(closes, vols))
    assert "divergence" in r["vp_patterns"]


def test_segment_up():
    # 近 3 日阳线且量能递增
    base = [100.0] * 18
    closes = base + [101.0, 102.0, 103.0]
    vols = [1000.0] * 18 + [1500.0, 2000.0, 3000.0]
    r = analyze_volume_price(_bars(closes, vols))
    assert "segment_up" in r["vp_patterns"]


def test_anomaly_big_move():
    closes = [100.0] * 20 + [106.0]  # 单日 +6%
    vols = [1000.0] * 21
    r = analyze_volume_price(_bars(closes, vols))
    assert "anomaly" in r["vp_patterns"]


def test_vol_down_fall_distribute():
    # 放量下跌 = 出货
    closes = [100.0] * 20 + [95.0]
    vols = [1000.0] * 20 + [2000.0]
    r = analyze_volume_price(_bars(closes, vols))
    assert r["vp_state"] == "VOL_UP_FALL"
    assert r["vp_vol_ratio_state"] == "放量"


def test_strength_bounds():
    # 任意输入强度分都应在 [0,100]
    closes = [100.0] * 21
    vols = [1000.0] * 21
    r = analyze_volume_price(_bars(closes, vols))
    assert 0.0 <= r["vp_strength"] <= 100.0
