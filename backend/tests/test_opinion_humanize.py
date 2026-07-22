"""意见/指数人话化测试（template-v2 + index_read）。

断言：
- key_metrics_text 不再输出「关键指标：」式堆砌，改为阈值化因果叙述。
- humanize_index_read 涨跌两分支叙述合理、含结构化标签、无术语堆砌。
"""
from app.opinion_engine.index_read import humanize_index_read
from app.opinion_engine.templates import key_metrics_text


def _full_metrics():
    return {
        "etf_rsi14": 50,
        "etf_rs_20d": 1.02,
        "sector_score": 50,
        "fund_flow_score": 50,
        "advance_ratio": 0.5,
    }


def test_key_metrics_no_term_stuffing():
    txt = key_metrics_text(_full_metrics())
    assert "关键指标：" not in txt
    # 阈值化人话片段
    assert "中性区间" in txt
    assert "与大盘基本同步" in txt
    assert "多空基本均衡" in txt
    assert txt.endswith("。")


def test_key_metrics_empty_falls_back():
    txt = key_metrics_text({})
    assert "数据不足" in txt


def test_key_metrics_rsi_extremes():
    assert "超买" in key_metrics_text({"etf_rsi14": 78})
    assert "超卖" in key_metrics_text({"etf_rsi14": 25})


def _points_trend(direction: str, n: int = 25):
    """构造单调涨跌 + 量能同向（几何）的 INDEX BAR 序列（dict 形态）。"""
    pts = []
    close = 3400.0
    vol = 1_000_000
    for _ in range(n):
        if direction == "down":
            close *= 0.99
            vol = int(vol * 0.95)
        else:
            close *= 1.01
            vol = int(vol * 1.05)
        pts.append({
            "trading_date": None,
            "close": round(close, 2),
            "volume": max(vol, 100_000),
            "amount": vol * 10.0,
            "change_percent": -1.0 if direction == "down" else 1.0,
        })
    return pts


def test_index_read_downtrend():
    res = humanize_index_read("000001", "上证综指", _points_trend("down"))
    assert "累计下跌" in res["read"]
    assert "跌破20日线" in res["signals"]
    assert "缩量" in res["signals"]
    assert "关键指标" not in res["read"]


def test_index_read_uptrend():
    res = humanize_index_read("000300", "沪深300", _points_trend("up"))
    assert "累计上涨" in res["read"]
    assert "站上20日线" in res["signals"]
    assert "放量" in res["signals"]


def test_index_read_insufficient_data():
    res = humanize_index_read("000001", "上证综指", [])
    assert res["signals"] == []
    assert "数据" in res["read"]
