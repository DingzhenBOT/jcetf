"""
P-1: AkShare 接口与字段验证脚本（V2，基于沙箱实测修正）
====================================================
目的：
  1. 验证六个能力是否可取真实数据：交易日历 / 大盘指数 / 板块排行 / 板块资金流 /
     ETF实时行情 / ETF历史日线。
  2. 记录每个接口的：耗时、原始字段、空值分布、异常。
  3. 导出真实 JSON 样例，并对照 DESIGN.md 统一数据模型做字段覆盖映射。

沙箱实测关键结论（写于 2025 验证时）：
  - 东方财富(em)主机在本沙箱被防火墙拦截（RemoteDisconnected）。
  - 新浪(sina)与同花顺(ths)可达，足以覆盖全部六个能力。
  - 板块排行与板块资金流在 ths 中合并于 stock_fund_flow_industry / _concept。
  - 依赖 em 的字段（板块涨跌家数、大单净流入、涨跌停数）沙箱缺失，需部署服务器验证。

设计说明：
  - 本脚本只做"数据源可行性验证"，不写业务规则。
  - 所有调用包在 time_call 里，成功/异常都记录耗时。
  - 输出为 AkShare 真实返回；字段缺口写入 source_notes。

运行命令：
  python3.11 backend/scripts/validate_akshare.py

输出：
  backend/scripts/p1_output/report.json        汇总
  backend/scripts/p1_output/sample_*.json      各接口真实样例（前 2 行）
"""
import time
import json
import os

import pandas as pd

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "p1_output")
os.makedirs(OUT_DIR, exist_ok=True)


def time_call(fn):
    t0 = time.perf_counter()
    try:
        df = fn()
        return df, round(time.perf_counter() - t0, 3), None
    except Exception as e:  # noqa: BLE001
        return None, round(time.perf_counter() - t0, 3), f"{type(e).__name__}: {e}"


def clean_value(v):
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
    null_counts = {c: int(df[c].isna().sum()) for c in cols}
    sample = df.head(2).map(clean_value).to_dict(orient="records")
    return {"columns": cols, "row_count": int(len(df)),
            "null_counts": null_counts, "sample": sample}


def build_cases():
    import akshare as ak

    return {
        "trade_calendar": {
            "desc": "交易日历（新浪）",
            "source": "sina",
            "call": lambda: ak.tool_trade_date_hist_sina(),
        },
        "index_spot": {
            "desc": "大盘/宽基指数实时行情（新浪）",
            "source": "sina",
            "call": lambda: ak.stock_zh_index_spot_sina(),
        },
        "industry_board": {
            "desc": "行业板块排行+资金流（同花顺，em 被拦截时的可用源）",
            "source": "ths",
            "call": lambda: ak.stock_fund_flow_industry(),
        },
        "concept_board": {
            "desc": "概念板块排行+资金流（同花顺）",
            "source": "ths",
            "call": lambda: ak.stock_fund_flow_concept(),
        },
        "etf_spot": {
            "desc": "ETF 实时行情（新浪）",
            "source": "sina",
            "call": lambda: ak.fund_etf_category_sina(symbol="ETF基金"),
        },
        "etf_hist": {
            "desc": "ETF 历史日线（新浪，sh510300）",
            "source": "sina",
            "call": lambda: ak.fund_etf_hist_sina(symbol="sh510300"),
        },
    }


# 统一模型字段覆盖（依据沙箱实测真实返回列；em=需部署服务器验证）
UNIFIED_MODEL_COVERAGE = {
    "data_source": "derive（固定 'akshare'）",
    "symbol_type": "derive（INDEX/INDUSTRY/CONCEPT/ETF）",
    "symbol": "direct（代码列；指数 sh/sz 前缀，ETF 同，板块用 ths code）",
    "data_kind": "derive（实时=SNAPSHOT；历史日线=BAR）",
    "timeframe": "derive（snapshot / 1d）",
    "trading_date": "derive（由 timestamp 按北京时间取日期）",
    "timestamp": "derive（采集时刻 UTC；历史用日期列）",
    "open": "direct（今开 / open）",
    "high": "direct（最高 / high）",
    "low": "direct（最低 / low）",
    "close": "direct（最新价 / close）",
    "previous_close": "direct（昨收）",
    "volume": "direct（成交量）",
    "amount": "direct（成交额）",
    "change_percent": "direct（涨跌幅 / 行业-涨跌幅）",
    "turnover_rate": "direct（ETF 实时含换手率；板块/指数缺失→可留空）",
    "main_net_inflow": "direct（ths: 净额；em 更细但沙箱不可达）",
    "large_order_inflow": "MISSING（ths 无大单列；em stock_board_*_fund_flow_em 有，需部署服务器）",
    "rise_count": "MISSING（ths 仅有公司家数总数；em stock_board_industry_name_em 有上涨家数，需部署服务器）",
    "fall_count": "MISSING（同上，em 有下跌家数）",
    "limit_up_count": "MISSING（需 stock_zt_pool_em 关联，em 沙箱不可达）",
    "limit_down_count": "MISSING（同上）",
    "collected_at": "derive（采集时刻 UTC）",
    "data_quality_status": "derive（data_quality 模块判定）",
}

SOURCE_NOTES = {
    "sina": "沙箱可达。提供日历/指数/ETF 实时/ETF 历史，字段完整（OHLCV+涨跌幅）。",
    "ths": "沙箱可达。stock_fund_flow_industry/_concept 同时给板块排行(涨跌幅)+资金流(净额)+领涨股+公司家数。",
    "em_blocked": "东方财富(em)主机在本沙箱被防火墙拦截(RemoteDisconnected)。生产部署服务器(国内网络)应可达，"
                  "届时优先用 em 以补齐 rise/fall 家数、大单净流入、涨跌停数。",
}


def main():
    try:
        import akshare as ak  # noqa: F401
    except Exception as e:  # noqa: BLE001
        print(f"[FATAL] akshare 未安装或导入失败: {e}")
        return

    cases = build_cases()
    report = {
        "generated_at": pd.Timestamp.now("UTC").isoformat(),
        "env_note": "P-1 验证：东方财富(em)在沙箱被拦截，已用新浪(sina)+同花顺(ths)取到全部六个能力真实数据。",
        "source_notes": SOURCE_NOTES,
        "cases": {},
        "unified_model_coverage": UNIFIED_MODEL_COVERAGE,
    }

    for name, meta in cases.items():
        print(f"[*] 验证 {name}: {meta['desc']}")
        df, elapsed, err = time_call(meta["call"])
        info = analyze(df)
        report["cases"][name] = {
            "desc": meta["desc"], "source": meta["source"],
            "elapsed_sec": elapsed, "error": err,
            "ok": err is None and df is not None, "analysis": info,
        }
        with open(os.path.join(OUT_DIR, f"sample_{name}.json"), "w", encoding="utf-8") as f:
            json.dump({"desc": meta["desc"], "source": meta["source"],
                       "elapsed_sec": elapsed, "error": err, "analysis": info},
                      f, ensure_ascii=False, indent=2)
        if report["cases"][name]["ok"]:
            print(f"    -> OK, {elapsed}s, 行数 {info['row_count']}, 列数 {len(info['columns'])}")
        else:
            print(f"    -> FAIL({err})")

    with open(os.path.join(OUT_DIR, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\n================ P-1 验证汇总 ================")
    ok = sum(1 for c in report["cases"].values() if c["ok"])
    for name, c in report["cases"].items():
        flag = "OK " if c["ok"] else "ERR"
        extra = f"cols={len(c['analysis']['columns'])}" if c["analysis"] else c["error"]
        print(f"  [{flag}] {name:16s} {c['source']:5s} {c['elapsed_sec']:>7.3f}s  {extra}")
    print(f"\n通过: {ok}/{len(cases)}   报告: {os.path.join(OUT_DIR, 'report.json')}")


if __name__ == "__main__":
    main()
