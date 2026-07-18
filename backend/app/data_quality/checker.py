"""数据质量（DESIGN §3.1 / R7 / P2）。

对 normalize 产出的字典逐条评估 data_quality_status：OK / STALE / MISSING / DELAY / ANOMALY。
- MISSING：关键字段（close/change_percent/主力净流入）全空。
- ANOMALY：价格为非正，或涨跌幅超阈值（A股 ±10% 护栏）。
- STALE/DELAY：仅交易时段内按时间新鲜度判定（收盘后不惩罚陈旧，避免误标）。
- OK：其余。
"""
from __future__ import annotations

from typing import Any, Dict, List

from app.config import DataQualityConfig


def _assess_row(row: Dict[str, Any], *, is_trading_now: bool, now, cfg: DataQualityConfig) -> str:
    close = row.get("close")
    chg = row.get("change_percent")
    net = row.get("main_net_inflow")

    # 关键字段全空 -> 缺失
    if close is None and chg is None and net is None:
        return "MISSING"

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
