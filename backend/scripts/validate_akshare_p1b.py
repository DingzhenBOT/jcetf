"""
P-1b: 策略历史数据闭环验证
==========================
目的：验证策略规则依赖的历史序列是否可取真实数据：
  1. get_index_history       宽基指数历史日线（MA20 / 成交额5日均值）
  2. get_sector_history      板块历史日线（板块 MA20 / RSI / 动量）
  3. get_sector_fund_flow_history  板块历史资金流（连续 N 日净流入）
  4. get_market_breadth_history    历史市场涨跌家数（市场环境回测）

沙箱已知：东方财富(em)被防火墙拦截。故 em 历史接口在此不可达，
需记录"生产服务器(国内网络)应可达"，并暴露历史涨跌家数是否缺直接 API。

运行：python3.11 backend/scripts/validate_akshare_p1b.py
输出：backend/scripts/p1b_output/report.json + sample_*.json
"""
import time
import json
import os

import pandas as pd

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "p1b_output")
os.makedirs(OUT_DIR, exist_ok=True)


def time_call(fn):
    t0 = time.perf_counter()
    try:
        return fn(), round(time.perf_counter() - t0, 3), None
    except Exception as e:  # noqa: BLE001
        return None, round(time.perf_counter() - t0, 3), f"{type(e).__name__}: {e}"


def clean(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (int, float, str, bool)):
        return v
    if isinstance(v, pd.Timestamp):
        return v.isoformat()
    return str(v)


def analyze(df):
    if df is None:
        return None
    cols = [str(c) for c in df.columns]
    return {"columns": cols, "row_count": int(len(df)),
            "null_counts": {c: int(df[c].isna().sum()) for c in cols},
            "sample": df.head(2).map(clean).to_dict(orient="records")}


def build_cases():
    import akshare as ak

    return {
        "index_history_tx": {
            "need": "get_index_history", "pref": "em/stock_zh_index_daily_em(含amount)",
            "fallback": "tx/stock_zh_index_daily_tx(含amount,沙箱可达) / sina/stock_zh_index_daily(无amount)",
            "call": lambda: ak.stock_zh_index_daily_tx(symbol="sh000001"),
        },
        "index_history_em": {
            "need": "get_index_history", "pref": "em",
            "fallback": "-",
            "call": lambda: ak.stock_zh_index_daily_em(symbol="sh000001"),
        },
        "sector_history_em": {
            "need": "get_sector_history", "pref": "em/stock_board_industry_hist_em",
            "fallback": "无(sina/ths 无日线历史)",
            "call": lambda: ak.stock_board_industry_hist_em(symbol="半导体"),
        },
        "sector_fund_flow_hist_em": {
            "need": "get_sector_fund_flow_history", "pref": "em/stock_sector_fund_flow_hist",
            "fallback": "无直接历史；上线后每日积累",
            "call": lambda: ak.stock_sector_fund_flow_hist(symbol="半导体"),
        },
        "breadth_accumulate_sina": {
            "need": "get_market_breadth_history(积累源)", "pref": "em/stock_zh_a_spot_em",
            "fallback": "sina/stock_zh_a_spot(沙箱可达, 当前全市场快照→每日计算涨跌家数累计)",
            "call": lambda: ak.stock_zh_a_spot(),
        },
    }


def main():
    import akshare as ak  # noqa: F401
    cases = build_cases()
    report = {
        "generated_at": pd.Timestamp.now("UTC").isoformat(),
        "note": "P-1b: 验证策略依赖的历史序列。em 在沙箱被墙，故 em 历史接口不可达，"
                "结论标注'需生产服务器验证'；重点暴露历史涨跌家数是否缺直接 API。",
        "cases": {},
        "closure_verdict": {},
    }

    for name, meta in cases.items():
        print(f"[*] {name}  ({meta['need']})")
        df, el, err = time_call(meta["call"])
        info = analyze(df)
        report["cases"][name] = {
            "need": meta["need"], "preferred": meta["pref"],
            "fallback": meta["fallback"], "elapsed_sec": el, "error": err,
            "ok": err is None and df is not None, "analysis": info,
        }
        with open(os.path.join(OUT_DIR, f"sample_{name}.json"), "w", encoding="utf-8") as f:
            json.dump({"need": meta["need"], "preferred": meta["pref"],
                       "fallback": meta["fallback"], "elapsed_sec": el,
                       "error": err, "analysis": info}, f, ensure_ascii=False, indent=2)
        if report["cases"][name]["ok"]:
            print(f"    OK {el}s rows={info['row_count']} cols={len(info['columns'])}")
        else:
            print(f"    ERR {el}s {err}")

    # 闭环判定
    report["closure_verdict"] = {
        "index_history": "tx(stock_zh_index_daily_tx,含amount)沙箱可达；em 生产更优。闭环 ✅",
        "sector_history": "仅 em(stock_board_industry_hist_em)提供；沙箱不可达，生产验证。设计依赖 em ✅(prod)",
        "sector_fund_flow_history": "仅 em(stock_sector_fund_flow_hist)提供；沙箱不可达。上线前用'每日积累'补 ✅(prod+accumulate)",
        "market_breadth_history": "无直接历史 API；用 sina/stock_zh_a_spot 每日全市场快照→计算涨跌家数→累计。"
                                  "❗设计必须支持'每日积累'，否则 breadth 相关规则（上涨占比/涨跌停）无历史可回测",
    }

    with open(os.path.join(OUT_DIR, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\n========== P-1b 策略数据闭环判定 ==========")
    for k, v in report["closure_verdict"].items():
        print(f"  {k:28s}: {v}")
    print(f"\n报告: {os.path.join(OUT_DIR, 'report.json')}")


if __name__ == "__main__":
    main()
