"""P2 normalize 测试：中文列 -> market_quote / market_breadth 字典映射。"""
from datetime import datetime, timezone

import pandas as pd

from app.collector import normalize


def _now() -> datetime:
    return datetime(2024, 1, 2, 2, 0, 0, tzinfo=timezone.utc).replace(tzinfo=None)  # 北京 10:00


def test_normalize_index_snapshot_maps_columns():
    df = pd.DataFrame([{
        "代码": "000001", "名称": "上证指数", "最新价": 3200.5, "涨跌额": 10.2,
        "涨跌幅": 0.32, "昨收": 3190.3, "今开": 3195.0, "最高": 3210.0,
        "最低": 3188.0, "成交量": 123456, "成交额": 1.5e9,
    }])
    df.attrs["__source"] = "sina"
    rows = normalize.normalize_index_snapshot(df, "sina", _now())
    assert len(rows) == 1
    r = rows[0]
    assert r["symbol_type"] == "INDEX" and r["symbol"] == "000001"
    assert r["data_kind"] == "SNAPSHOT" and r["timeframe"] == "snapshot"
    assert r["close"] == 3200.5 and r["previous_close"] == 3190.3
    assert r["change_percent"] == 0.32 and r["amount"] == 1.5e9
    assert r["main_net_inflow"] is None  # 指数快照无净额
    assert r["metric_source"] == "sina" and r["source_switched"] == 0


def test_normalize_sector_em_vs_ths():
    # em 板块（板块代码 + 上涨家数/下跌家数，无净额）
    em = pd.DataFrame([{
        "板块代码": "BK0475", "最新价": 1200.0, "涨跌幅": 1.2,
        "换手率": 2.5, "上涨家数": 30, "下跌家数": 10,
    }])
    em.attrs["__source"] = "em"
    re = normalize.normalize_sector_ranking(em, "em", "INDUSTRY", _now())[0]
    assert re["symbol"] == "BK0475"
    assert re["close"] == 1200.0 and re["rise_count"] == 30 and re["fall_count"] == 10
    assert re["main_net_inflow"] is None

    # ths 板块（行业名称 + 行业指数 + 净额，无代码）
    ths = pd.DataFrame([{
        "行业": "半导体", "行业指数": 1300.3, "行业-涨跌幅": 1.5,
        "流入资金": 1e8, "流出资金": 8e7, "净额": 2e7, "公司家数": 50,
    }])
    ths.attrs["__source"] = "ths"
    rt = normalize.normalize_sector_ranking(ths, "ths", "INDUSTRY", _now())[0]
    assert rt["symbol"] == "半导体"  # ths 无代码，用名称
    assert rt["close"] == 1300.3 and rt["change_percent"] == 1.5
    assert rt["main_net_inflow"] == 2e7


def test_normalize_handles_missing_columns_as_none():
    df = pd.DataFrame([{"代码": "510300"}])  # 仅代码，其余缺失
    df.attrs["__source"] = "sina"
    r = normalize.normalize_etf_snapshot(df, "sina", _now())[0]
    assert r["close"] is None and r["change_percent"] is None
    assert r["volume"] is None and r["amount"] is None


def test_normalize_skips_rows_without_symbol():
    df = pd.DataFrame([{"最新价": 1.0}, {"代码": "X", "最新价": 2.0}])
    df.attrs["__source"] = "sina"
    rows = normalize.normalize_index_snapshot(df, "sina", _now())
    assert len(rows) == 1 and rows[0]["symbol"] == "X"


def test_normalize_breadth_counts_and_parses_timestamp():
    df = pd.DataFrame([
        {"代码": "1", "涨跌幅": 2.0, "成交额": 1e6, "时间戳": "2024-01-02 10:00:00"},
        {"代码": "2", "涨跌幅": -3.0, "成交额": 2e6, "时间戳": "2024-01-02 10:00:00"},
        {"代码": "3", "涨跌幅": 0.0, "成交额": 5e5, "时间戳": "2024-01-02 10:00:00"},
        {"代码": "4", "涨跌幅": 10.0, "成交额": 3e6, "时间戳": "2024-01-02 10:00:00"},
        {"代码": "5", "涨跌幅": -10.0, "成交额": 1e6, "时间戳": "2024-01-02 10:00:00"},
    ])
    df.attrs["__source"] = "sina"
    b = normalize.normalize_breadth(df, "sina", _now())
    assert b["total_rise"] == 2 and b["total_fall"] == 2 and b["total_flat"] == 1
    assert b["limit_up"] == 1 and b["limit_down"] == 1
    assert b["total_amount"] == 7.5e6
    # 时间戳解析：北京 10:00 -> UTC 02:00
    assert b["timestamp"].hour == 2
