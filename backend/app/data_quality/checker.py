"""数据质量（DESIGN §3.1 / R7 / P2）。

对 normalize 产出的字典逐条评估 data_quality_status：OK / STALE / MISSING / DELAY / ANOMALY。
- MISSING：关键字段（close/change_percent/主力净流入）全空。
- ANOMALY：价格为非正，或涨跌幅超阈值（A股 ±10% 护栏）。
- STALE/DELAY：仅交易时段内按时间新鲜度判定（收盘后不惩罚陈旧，避免误标）。
- OK：其余。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.config import DataQualityConfig


def _check_ohlc_consistency(row: Dict[str, Any], cfg: DataQualityConfig) -> Optional[str]:
    """OHLC 合理性校验（#67）：标记价格关系失真的脏数据。

    返回 "ANOMALY" 当满足任一异常：
    - 任一价格为非正（含 0 / 负值）
    - high < low（高低关系颠倒，512000 即此：高0.525<低0.526）
    - 价格跨度 max/min > cfg.max_price_span_ratio（512000: 346/0.525≈659x）

    不校验「开/收是否落在 [low, high] 区间」：复权（前/后复权）行情中 open/close 与
    high/low 可能按不同系数调整，出现轻微越界属正常，硬判定会误伤真实数据。
    单位/拆细错乱类脏数据（如 512000 开346/收0.5）由「high<low」+「跨度」两条规则精准捕获。

    设计：A股单日涨跌幅限制 ±10%，正常 K 线 high/low 相对跨度约 ≤1.1，
    故 4.0 阈值对正常行情几乎不会误伤，但能拦截单位错乱类脏数据。
    """
    o, h, l, c = row.get("open"), row.get("high"), row.get("low"), row.get("close")
    present = [v for v in (o, h, l, c) if v is not None]
    if not present:
        return None

    # 任一价格为非正 -> 异常
    if any(v <= 0 for v in present):
        return "ANOMALY"

    # 高低关系必须 high >= low
    if h is not None and l is not None and h < l:
        return "ANOMALY"

    # 价格跨度异常（单位/拆细错乱：如 346 vs 0.5）-> 异常
    if len(present) >= 2:
        lo = min(present)
        if lo > 0 and (max(present) / lo) > cfg.max_price_span_ratio:
            return "ANOMALY"

    return None


def _assess_row(row: Dict[str, Any], *, is_trading_now: bool, now, cfg: DataQualityConfig) -> str:
    close = row.get("close")
    chg = row.get("change_percent")
    net = row.get("main_net_inflow")

    # 关键字段全空 -> 缺失
    if close is None and chg is None and net is None:
        return "MISSING"

    # OHLC 关系失真（#67）：开收越界 / 高低颠倒 / 跨度异常 -> 优先判 ANOMALY
    ohlc = _check_ohlc_consistency(row, cfg)
    if ohlc is not None:
        return ohlc

    # 异常数值
    if close is not None and close < cfg.min_price:
        return "ANOMALY"
    if chg is not None and abs(chg) > cfg.max_abs_change_percent:
        return "ANOMALY"

    # 时间新鲜度（仅交易时段内严格）
    src_ts = row.get("source_timestamp") or row.get("timestamp")
    if src_ts is not None and is_trading_now:
        age = (now - src_ts).total_seconds()
        if age > cfg.stale_seconds_threshold:
            return "STALE"
        if age > cfg.delay_seconds_threshold:
            return "DELAY"

    return "OK"


def assess(
    rows: List[Dict[str, Any]],
    *,
    is_trading_now: bool,
    now,
    cfg: DataQualityConfig,
) -> List[Dict[str, Any]]:
    """原地赋值 rows[*].data_quality_status；空列表直接返回（MISSING 由调用方按批次处理）。"""
    for row in rows:
        row["data_quality_status"] = _assess_row(
            row, is_trading_now=is_trading_now, now=now, cfg=cfg
        )
    return rows
