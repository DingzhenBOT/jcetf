"""东方财富网页直连源（CVM 实测 push2.eastmoney.com 不被 RST，替代被墙的 akshare em / push2his）。

CVM 连通性实测（scripts/diag_sources_cvm.py，2026-07-27）：
- push2.eastmoney.com/api/qt/clist/get（板块资金流/涨跌排名）可达 -> 板块资金流快照
- push2.eastmoney.com/api/qt/stock/kline/get（secid=90.BKxxxx，同主机）可达 -> 板块日K历史
- push2his.eastmoney.com（akshare em 历史主机）被 RST 拦截 -> 不走

设计：
- 板块资金流：clist 一次性返回全部行业(t:2)+概念(t:3)板块的当日主力净流入/涨跌幅等，按 BK 代码过滤入库。
- 板块日K：kline 按 secid=90.BKxxxx 逐板块拉取（同主机，CVM 可达）。
- 任何网络/解析异常直接抛出，由 Collector 对应 *web 方法捕获并优雅降级（不抛上层）。
"""
from __future__ import annotations

import json
import urllib.request
from datetime import date, datetime
from typing import List, Optional

import pandas as pd

_PUSH2 = "https://push2.eastmoney.com/api/qt"
# clist 字段：f12代码 f14名称 f2最新价(收盘) f3涨跌幅% f62主力净流入 f66超大单净流入 f184主力净流入率% f6成交额
_CLIST_FIELDS = "f12,f14,f2,f3,f62,f66,f184,f6"


def _get_json(url: str, timeout: int = 12) -> dict:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _to_float(x) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def fetch_sector_fund_flow_snapshot(trade_date: Optional[date] = None, timeout: int = 12) -> pd.DataFrame:
    """一次性拉取全部行业/概念板块的当日资金流+涨跌（push2 clist）。

    返回 DataFrame 列（与 normalize_sector_fund_flow_bar 对齐）：
    bk_code, name, 日期, 收盘, 涨跌幅, 主力净流入-净额, 超大单净流入-净额, 成交额。
    trade_date 缺省用今天。空结果抛 RuntimeError（由调用方降级）。
    """
    if trade_date is None:
        trade_date = date.today()
    date_str = trade_date.isoformat()
    out: List[dict] = []
    for fs in ("m:90+t:2", "m:90+t:3"):  # 行业板块 / 概念板块
        url = f"{_PUSH2}/clist/get?pn=1&pz=500&fid=f62&fs={fs}&fields={_CLIST_FIELDS}"
        try:
            d = _get_json(url, timeout)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"em_web clist {fs} failed: {e}")
        diff = (d.get("data") or {}).get("diff") or {}
        for item in diff.values():
            out.append({
                "bk_code": str(item.get("f12") or ""),
                "name": item.get("f14"),
                "日期": date_str,
                "收盘": _to_float(item.get("f2")),
                "涨跌幅": _to_float(item.get("f3")),
                "主力净流入-净额": _to_float(item.get("f62")),
                "超大单净流入-净额": _to_float(item.get("f66")),
                "成交额": _to_float(item.get("f6")),
            })
    if not out:
        raise RuntimeError("em_web clist returned empty for both industry/concept")
    df = pd.DataFrame(out)
    df.attrs["__source"] = "em_web"
    return df


def fetch_sector_kline(bk_code: str, start: str, end: str, timeout: int = 12) -> pd.DataFrame:
    """板块日K历史（push2 kline 主机，secid=90.BKxxxx）。

    返回 DataFrame 列（与 normalize_sector_bar 对齐）：
    日期, 开盘, 收盘, 最高, 最低, 成交量, 成交额, 振幅, 涨跌幅, 涨跌额, 换手率。
    start/end: YYYYMMDD。空结果抛 RuntimeError（由调用方降级）。
    """
    bk = str(bk_code).strip()
    if not bk:
        raise ValueError("empty bk_code")
    secid = f"90.{bk}"
    url = (
        f"{_PUSH2}/stock/kline/get?secid={secid}"
        f"&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
        f"&klt=101&fqt=0&beg={start}&end={end}"
    )
    try:
        d = _get_json(url, timeout)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"em_web kline {bk} failed: {e}")
    klines = (d.get("data") or {}).get("klines") or []
    if not klines:
        raise RuntimeError(f"em_web kline {bk} returned empty")
    rows: List[dict] = []
    for line in klines:
        parts = str(line).split(",")
        if len(parts) < 11:
            continue
        try:
            dd = datetime.strptime(parts[0], "%Y-%m-%d").date()
        except ValueError:
            continue
        rows.append({
            "日期": dd.isoformat(),
            "开盘": _to_float(parts[1]),
            "收盘": _to_float(parts[2]),
            "最高": _to_float(parts[3]),
            "最低": _to_float(parts[4]),
            "成交量": _to_float(parts[5]),
            "成交额": _to_float(parts[6]),
            "振幅": _to_float(parts[7]),
            "涨跌幅": _to_float(parts[8]),
            "涨跌额": _to_float(parts[9]),
            "换手率": _to_float(parts[10]),
        })
    if not rows:
        raise RuntimeError(f"em_web kline {bk} parsed 0 rows")
    df = pd.DataFrame(rows)
    df.attrs["__source"] = "em_web"
    return df
