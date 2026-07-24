"""P2 normalize 测试：中文列 -> market_quote / market_breadth 字典映射。"""
from datetime import date, datetime, timezone

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


def test_normalize_etf_bar_accepts_sina_english_columns():
    # 新浪 fund_etf_hist_sina 返回英文列（date/open/high/low/close/volume/amount...）
    df = pd.DataFrame([
        {"date": "2024-01-02", "open": 3.7, "high": 3.81, "low": 3.77, "close": 3.8,
         "volume": 2, "amount": 1e8},
        {"date": "2024-01-03", "open": 3.79, "high": 3.91, "low": 3.78, "close": 3.9,
         "volume": 2, "amount": 1e8},
    ])
    df.attrs["__source"] = "sina"
    rows = normalize.normalize_etf_bar(df, "sina", "510300", _now())
    assert len(rows) == 2
    assert rows[0]["close"] == 3.8 and rows[0]["open"] == 3.7 and rows[0]["high"] == 3.81
    assert rows[0]["volume"] == 2 and rows[0]["amount"] == 1e8
    assert rows[0]["symbol_type"] == "ETF" and rows[0]["data_kind"] == "BAR"


def test_normalize_etf_bar_accepts_em_chinese_columns():
    # em fund_etf_hist_em 返回中文列
    df = pd.DataFrame([
        {"日期": "2024-01-02", "开盘": 3.7, "收盘": 3.8, "最高": 3.81, "最低": 3.77,
         "成交量": 2, "成交额": 1e8, "涨跌幅": 0.5, "换手率": 1.2},
    ])
    df.attrs["__source"] = "em"
    rows = normalize.normalize_etf_bar(df, "em", "510300", _now())
    assert len(rows) == 1
    assert rows[0]["close"] == 3.8 and rows[0]["change_percent"] == 0.5
    assert rows[0]["turnover_rate"] == 1.2


def test_normalize_index_bar_accepts_sina_english_columns():
    df = pd.DataFrame([
        {"date": "2024-01-02", "open": 3190, "high": 3210, "low": 3188, "close": 3200, "volume": 1},
        {"date": "2024-01-03", "open": 3200, "high": 3220, "low": 3198, "close": 3215, "volume": 1},
    ])
    df.attrs["__source"] = "sina"
    rows = normalize.normalize_index_bar(df, "sina", "000300", _now())
    assert len(rows) == 2
    assert rows[0]["close"] == 3200 and rows[0]["high"] == 3210
    assert rows[0]["symbol_type"] == "INDEX"


def test_normalize_sector_bar_accepts_ths_columns_with_price_suffix():
    # THS 行业/概念历史返回「开盘价/最高价/最低价/收盘价」（带"价"），且无涨跌幅/换手率
    df = pd.DataFrame([
        {"日期": "2024-01-02", "开盘价": 100.0, "最高价": 102.0, "最低价": 99.0,
         "收盘价": 101.5, "成交量": 5000, "成交额": 1.2e9},
    ])
    df.attrs["__source"] = "ths"
    rows = normalize.normalize_sector_bar(df, "ths", "BK1036", _now())
    assert len(rows) == 1
    r = rows[0]
    assert r["symbol_type"] == "SECTOR" and r["symbol"] == "BK1036"
    assert r["data_kind"] == "BAR" and r["timeframe"] == "1d"
    assert r["open"] == 100.0 and r["close"] == 101.5
    assert r["high"] == 102.0 and r["low"] == 99.0
    assert r["volume"] == 5000 and r["amount"] == 1.2e9
    # THS 无涨跌幅/换手率 -> None（不臆造）
    assert r["change_percent"] is None and r["turnover_rate"] is None


def test_normalize_sector_bar_accepts_em_columns():
    # em 行业历史返回「开盘/收盘/最高/最低」（不带"价"）
    df = pd.DataFrame([
        {"日期": "2024-01-02", "开盘": 100.0, "最高": 102.0, "最低": 99.0,
         "收盘": 101.5, "成交量": 5000, "成交额": 1.2e9, "涨跌幅": 1.5, "换手率": 0.8},
    ])
    df.attrs["__source"] = "em"
    rows = normalize.normalize_sector_bar(df, "em", "BK1036", _now())
    assert len(rows) == 1
    r = rows[0]
    assert r["open"] == 100.0 and r["close"] == 101.5
    assert r["change_percent"] == 1.5 and r["turnover_rate"] == 0.8


def test_normalize_index_snapshot_derives_change_percent_when_missing():
    # sina 实时指数接口无"涨跌幅"列时，用 最新价/昨收 反算
    df = pd.DataFrame([{
        "代码": "000001", "名称": "上证指数", "最新价": 3200.5,
        "昨收": 3190.3, "今开": 3195.0, "最高": 3210.0,
        "最低": 3188.0, "成交量": 123456, "成交额": 1.5e9,
    }])
    df.attrs["__source"] = "sina"
    r = normalize.normalize_index_snapshot(df, "sina", _now())[0]
    assert r["close"] == 3200.5 and r["previous_close"] == 3190.3
    assert abs(r["change_percent"] - (3200.5 - 3190.3) / 3190.3 * 100) < 1e-3


def test_normalize_index_bar_derives_change_percent_from_prev_close():
    # 日线无"涨跌幅"列时，用前一天收盘反算当日涨跌幅
    df = pd.DataFrame([
        {"date": "2024-01-02", "open": 3190, "high": 3210, "low": 3188, "close": 3200, "volume": 1},
        {"date": "2024-01-03", "open": 3200, "high": 3220, "low": 3198, "close": 3215, "volume": 1},
        {"date": "2024-01-04", "open": 3215, "high": 3230, "low": 3210, "close": 3190, "volume": 1},
    ])
    df.attrs["__source"] = "sina"
    rows = normalize.normalize_index_bar(df, "sina", "000300", _now())
    assert len(rows) == 3
    # 第一天：无昨收 -> None
    assert rows[0]["change_percent"] is None
    # 第二天：(3215-3200)/3200*100
    assert abs(rows[1]["change_percent"] - (3215 - 3200) / 3200 * 100) < 1e-3
    # 第三天：(3190-3215)/3215*100
    assert abs(rows[2]["change_percent"] - (3190 - 3215) / 3215 * 100) < 1e-3


def test_normalize_intraday_minute_maps_and_tags_1m():
    # 新浪 stock_zh_a_minute 返回：day, open, high, low, close, volume, amount
    df = pd.DataFrame([
        {"day": "2024-01-02 09:30:00", "open": 4.000, "high": 4.010, "low": 3.998,
         "close": 4.005, "volume": 120000, "amount": 4.8e8},
        {"day": "2024-01-02 09:31:00", "open": 4.005, "high": 4.012, "low": 4.004,
         "close": 4.008, "volume": 90000, "amount": 3.6e8},
    ])
    df.attrs["__source"] = "sina"
    td = date(2024, 1, 2)
    rows = normalize.normalize_intraday_minute(df, "sina", "ETF", "510300", td, _now())
    assert len(rows) == 2
    r0 = rows[0]
    # timeframe=1m, data_kind=BAR
    assert r0["timeframe"] == "1m" and r0["data_kind"] == "BAR"
    assert r0["symbol_type"] == "ETF" and r0["symbol"] == "510300"
    assert r0["trading_date"] == td
    assert r0["close"] == 4.005 and r0["open"] == 4.000 and r0["high"] == 4.010
    assert r0["volume"] == 120000
    # 时间：北京 09:30 -> 存储 UTC 01:30
    assert r0["timestamp"].hour == 1 and r0["timestamp"].minute == 30
    # 幂等键字段齐全
    assert r0["data_source"] == "sina"

