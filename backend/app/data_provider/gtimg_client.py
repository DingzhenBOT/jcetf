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

import json
import urllib.request
from datetime import date, datetime
from typing import List, Optional, Tuple

import pandas as pd

_QT_URL = "https://qt.gtimg.cn/q="
# 腾讯财经当日分时接口（web.ifzq.gtimg.cn）：返回当日 1 分钟分时，CVM 不封 IP。
# 注意：与 qt.gtimg.cn 实时快照不同，分钟数据走 web.ifzq.gtimg.cn 的 appstock/minute/query。
_MINUTE_URL = "https://web.ifzq.gtimg.cn/appstock/app/minute/query?code="

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


# 美股指数代码前缀（腾讯财经 qt.gtimg.cn）：us + 代码，如 usDJI/usIXIC/usINX。
# 注意：与 A股不同，美股用 `us` 前缀（非 `s_us_`）；道琼斯=.DJI 纳斯达克=.IXIC 标普500=.INX。
# 字段位置（实测 2026-07-24）：[1]名称 [2]代码(.DJI) [3]最新价 [4]昨收 [5]今开
#   [31]时间戳 [32]涨跌额 [33]涨跌幅% [34]最高 [35]最低
_US_NAME = 1
_US_CODE = 2
_US_CLOSE = 3
_US_PREV = 4
_US_OPEN = 5
_US_TS = 31
_US_CHG = 32
_US_PCT = 33
_US_HIGH = 34
_US_LOW = 35


def fetch_us_indices(codes: List[str], timeout: int = 10) -> pd.DataFrame:
    """批量拉取美股指数实时行情（道琼斯/纳斯达克/标普500）。

    codes: 腾讯财经代码列表，如 ['usDJI', 'usIXIC', 'usINX']。
    返回 DataFrame（中文列：代码/名称/今开/最高/最低/最新价/昨收/涨跌幅），与 A股快照同列名，
    可直接喂 normalize_us_index_snapshot；空输入/全失败返回空 DataFrame。
    任何网络/解析异常直接抛出，由 Collector.collect_us_indices 捕获并优雅降级。
    """
    if not codes:
        return pd.DataFrame()
    url = _QT_URL + ",".join(codes)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.qq.com/"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("gbk", errors="replace")

    rows: List[dict] = []
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line.startswith("v_") or "=" not in line:
            continue
        sym, _, val = line.partition("=")
        sym = sym[2:]  # 去掉 "v_" 前缀得到代码（如 usDJI）
        val = val.strip().strip('"').rstrip(";")
        if not val or "none_match" in val:
            continue
        p = val.split("~")
        if len(p) <= _US_LOW:
            continue
        rows.append(
            {
                "代码": sym,
                "名称": p[_US_NAME],
                "今开": _to_float(p[_US_OPEN]),
                "最高": _to_float(p[_US_HIGH]),
                "最低": _to_float(p[_US_LOW]),
                "最新价": _to_float(p[_US_CLOSE]),
                "昨收": _to_float(p[_US_PREV]),
                "涨跌幅": _to_float(p[_US_PCT]),
            }
        )
    return pd.DataFrame(rows)


def _parse_minute_date(s: str) -> Optional[date]:
    """分时 date 节点容错解析：支持 '2026-07-27' / '20260727'；异常返 None。"""
    if not s:
        return None
    s = s.strip()
    try:
        if len(s) == 8 and s.isdigit():
            return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _merge_minute_dt(d: Optional[date], hhmm: str) -> Optional[datetime]:
    """把 'HHMM' 合并到交易日期得到北京时 naive datetime；格式异常返 None。"""
    hhmm = (hhmm or "").strip().zfill(4)
    if len(hhmm) != 4 or not hhmm.isdigit():
        return None
    base = d or date.today()
    try:
        return datetime(base.year, base.month, base.day, int(hhmm[:2]), int(hhmm[2:4]))
    except (ValueError, TypeError):
        return None


def fetch_intraday_minute(code: str, kind: str, timeout: int = 10) -> pd.DataFrame:
    """腾讯财经 web.ifzq.gtimg.cn 当日 1 分钟分时（CVM 不封 IP、返回当日，替代 sina 旧数据问题）。

    返回 DataFrame（列：day/open/high/low/close/volume，day 为北京时 naive datetime），
    可直接喂 normalize.normalize_intraday_minute（与 sina stock_zh_a_minute 同列名）。

    腾讯分时每行格式："HHMM price cum_vol cum_amount"：
    - 仅含该分钟标记价、无分钟级 OHLC -> open/high/low/close 均取该分钟价（价格线准确，分钟高低点为近似）。
    - volume 为「累计」成交量 -> 取相对上一分钟的增量，与 sina 每分钟量语义一致。
    任何网络/解析异常直接抛出，由 Collector.collect_intraday_minute 捕获并优雅降级（回退 sina）。
    """
    code = str(code).strip()
    if not code:
        raise ValueError("empty code")
    symbol = _to_gtimg_symbol(code, (kind or "").lower())
    url = _MINUTE_URL + symbol
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.qq.com/"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    try:
        payload = json.loads(text)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"gtimg minute json parse failed: {e}")

    node = ((payload.get("data") or {}).get(symbol) or {}).get("data") or {}
    arr = node.get("data") or []
    if not arr:
        raise ValueError(f"gtimg minute {symbol} returned empty")
    trade_date = _parse_minute_date((node.get("date") or "").strip())

    rows: List[dict] = []
    prev_vol: Optional[float] = None
    for line in arr:
        parts = str(line).split()
        if len(parts) < 3:
            continue
        hhmm, price = parts[0], _to_float(parts[1])
        cum_vol = _to_float(parts[2]) if len(parts) > 2 else None
        if price is None:
            continue
        dt = _merge_minute_dt(trade_date, hhmm)
        if dt is None:
            continue
        inc_vol = (cum_vol - prev_vol) if (prev_vol is not None and cum_vol is not None) else (cum_vol or 0.0)
        prev_vol = cum_vol
        rows.append({
            "day": dt,
            "open": price, "high": price, "low": price, "close": price,
            "volume": inc_vol,
        })
    if not rows:
        raise ValueError(f"gtimg minute {symbol} parsed 0 rows")
    df = pd.DataFrame(rows)
    df.attrs["__source"] = "gtimg"
    return df
