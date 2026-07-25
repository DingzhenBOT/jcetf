"""腾讯财经 qt.gtimg.cn 实时行情客户端（不封 IP，CVM 最稳；C2 已定实时主源）。

用途：盘中 SNAPSHOT（ETF/宽基指数实时涨跌幅），补 em/sina 在腾讯云可能被 RST 封的可用性间隙，
保证 P1「盘中综合分随实时行情更新」在 CVM 真正生效。

- 单次请求批量拉取（≤~100 代码），GBK 解码，解析 v_xxx="..." 的 ~ 分隔字段。
- 字段（实测 88 字段，2026-07-25）：[3]最新价 [4]昨收 [5]今开 [32]涨跌幅% [33]最高 [34]最低
  [35]="现价/成交量/成交额(元)" [2]数字代码（无前缀，直接匹配系统代码）。
- 返回 DataFrame（中文列：代码/名称/今开/最高/最低/最新价/昨收/成交量/成交额/涨跌幅），
  可直接喂 normalize.normalize_etf_snapshot / normalize_index_snapshot。
- 任何网络/解析异常直接抛出，由 Collector.collect_realtime_gtimg 捕获并优雅降级（不抛上层）。
"""
from __future__ import annotations

import urllib.request
from typing import List, Optional, Tuple

import pandas as pd

_QT_URL = "https://qt.gtimg.cn/q="

# 代码 -> 腾讯财经前缀（与 akshare_adapter._to_sina_symbol 同规则）
_INDEX_HEADS = ("0", "6", "9")  # 上交所指数
_ETF_SH_HEAD = "5"              # 上交所 ETF（51/56/58 开头）


def _to_gtimg_symbol(code: str, kind: str) -> str:
    code = str(code).strip()
    head = code[0] if code else ""
    if kind == "index":
        prefix = "sh" if head in _INDEX_HEADS else "sz"
    else:  # etf
        prefix = "sh" if head == _ETF_SH_HEAD else "sz"
    return prefix + code


def _to_float(x) -> Optional[float]:
    try:
        return float(x)
    except (ValueError, TypeError):
        return None


def fetch_realtime(codes_with_kind: List[Tuple[str, str]], timeout: int = 10) -> pd.DataFrame:
    """批量拉取实时行情。

    codes_with_kind: [(数字代码, 'etf'|'index'), ...]。
    返回 DataFrame（中文列：代码/名称/今开/最高/最低/最新价/昨收/成交量/成交额/涨跌幅）；
    空输入或全失败返回空 DataFrame。
    """
    if not codes_with_kind:
        return pd.DataFrame()
    symbols = [_to_gtimg_symbol(code, kind) for code, kind in codes_with_kind]
    url = _QT_URL + ",".join(symbols)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.qq.com/"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("gbk", errors="replace")

    rows: List[dict] = []
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        _, _, val = line.partition("=")
        val = val.strip().strip('"').rstrip(";")
        if not val:
            continue
        p = val.split("~")
        if len(p) < 38:
            continue
        code = (p[2] or "").strip()
        if not code:
            continue
        # [35] = "现价/成交量/成交额(元)"；取成交额（元）
        amount = None
        seg = p[35].split("/")
        if len(seg) >= 3:
            amount = _to_float(seg[2])
        rows.append(
            {
                "代码": code,
                "名称": p[1],
                "今开": _to_float(p[5]),
                "最高": _to_float(p[33]),
                "最低": _to_float(p[34]),
                "最新价": _to_float(p[3]),
                "昨收": _to_float(p[4]),
                "成交量": _to_float(p[6]),
                "成交额": amount,
                "涨跌幅": _to_float(p[32]),
            }
        )
    return pd.DataFrame(rows)
