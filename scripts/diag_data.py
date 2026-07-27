#!/usr/bin/env python3
"""CVM 数据诊断 + sina 分时接口直接测试（排查盘中数据为何不更新）。

在 CVM 执行：backend/venv/bin/python scripts/diag_data.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

DB = "/home/ubuntu/workspace/data/etf_monitor.db"  # CVM 路径


def db_part() -> None:
    import sqlite3

    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    cols = [r[1] for r in db.execute("PRAGMA table_info(market_quote)")]
    chg = "change_percent" if "change_percent" in cols else "json_extract(data,'$.change_percent')"

    print("=== market_quote 各 symbol_type/data_kind 最新时间戳 ===")
    for r in db.execute(
        "SELECT symbol_type, data_kind, MAX(timestamp) ts, COUNT(*) n "
        "FROM market_quote GROUP BY symbol_type, data_kind ORDER BY symbol_type, data_kind"
    ):
        print(dict(r))

    print("\n=== 大盘 SNAPSHOT 涨跌（确认今天大盘实际方向） ===")
    for r in db.execute(
        f"SELECT symbol, {chg} chg, timestamp FROM market_quote "
        f"WHERE symbol_type='INDEX' AND data_kind='SNAPSHOT' ORDER BY timestamp DESC LIMIT 4"
    ):
        print(dict(r))

    print("\n=== signal 7/27 实际 regime/confidence/failed ===")
    for r in db.execute(
        "SELECT target_etf, score, confidence, market_regime, failed_rules "
        "FROM signal WHERE trading_date='2026-07-27' ORDER BY score DESC LIMIT 5"
    ):
        print(dict(r))
    db.close()


def sina_test() -> None:
    from app.config import get_settings
    from app.data_provider import build_provider

    print("\n=== sina 分时接口直接测试（CVM 网络） ===")
    s = get_settings()
    p = build_provider(s)
    for st, code in [("INDEX", "000300"), ("ETF", "510300")]:
        try:
            df = p.get_intraday_minute(st, code)
            if df is None:
                print(st, code, "-> None")
            elif hasattr(df, "empty") and df.empty:
                print(st, code, "-> EMPTY（接口返回空，sina 分时不可用）")
            else:
                print(st, code, "-> rows:", len(df), "| 前2行:", df.head(2).to_dict("records"))
        except Exception as e:  # noqa: BLE001
            print(st, code, "-> ERROR:", type(e).__name__, str(e)[:150])


if __name__ == "__main__":
    db_part()
    sina_test()
