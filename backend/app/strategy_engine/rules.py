"""第一版策略规则（P3 冻结，DESIGN §9）。

本字典是 mint_strategy_version 参与哈希的「规则」部分；纯文本转录，便于审计与复现。
任何字段顺序/取值变化都会产生新 strategy_version（不可覆盖，旧版本禁止 UPDATE）。
引擎逻辑（engine.py）按此描述实现；此处只描述「规则是什么」，不含代码。
"""
from __future__ import annotations

from typing import Dict

RULES_V1: Dict = {
    "version": "1.0",
    "description": "DESIGN §9 五类评分确定性规则（P3 冻结）",
    "market_score": {
        "index_above_ma20_and_rising": "宽基指数站上 MA20 且上行 -> 加分",
        "advance_ratio": {"add_above": 0.60, "subtract_below": 0.40, "neutral_band": [0.45, 0.55]},
        "amount_vs_5d_avg": "全市场成交额较近 5 日均放大 -> 加分，萎缩 -> 减分",
        "regimes": ["STRONG_UP", "TREND_UP", "VOLATILE", "WEAK", "BEAR"],
    },
    "sector_trend_score": {
        "close_above_ma20": True,
        "ma20_rising": True,
        "momentum_percentiles": ["mom_5", "mom_10", "mom_20"],
        "rsi_band": {"healthy": [50, 70], "overheat": 80},
        "weights": {"above_ma20": 35, "ma20_rising": 20, "mom20_pos": 15, "rsi_healthy": 15, "mom5_pos": 5},
    },
    "fund_flow_score": {
        "consecutive_positive_days_threshold": 3,
        "inflow_strength": "net_inflow / sector_amount（跨期累计）",
        "large_order_confirm": True,
        "divergence_penalty": True,
        "same_source_only": True,
        "weights": {"consecutive_3": 40, "consecutive_2": 25, "consecutive_1": 10,
                     "inflow_strong": 30, "inflow_pos": 15, "inflow_neutral": 5,
                     "large_order_same": 10, "divergence": -10},
    },
    "etf_rs_score": {
        "rolling_rs_window": 20,
        "mapping": "rs_20d -> 0-100：50 + (rs_20d - 1) * 100",
        "peer_rank": "同类 ETF 分位（缺数据时回退宽基指数）",
    },
    "risk_filter": {
        "chase_high": ["rsi>80", "sector_short_surge"],
        "market_bear_or_high_vol": True,
        "drawdown_from_high": True,
        "atr_pct_threshold": 4.0,
        "data_quality_abnormal": True,
        "veto": "deny_market_bear_with_missing_data",
        "downgrade": "downgrade_on_chase_high",
    },
    "signal_synthesis": {
        "composite": "w1*market + w2*sector_trend + w3*fund_flow + w4*etf_rs（权重和=1，缺失项重归一化）",
        "risk_not_deducted": True,
        "veto_condition": "market_regime=BEAR AND 宽基/宽度数据缺失",
        "downgrade_condition": "chase_high OR high_vol（composite 下调一档）",
    },
    "tiers": [
        {"tier": "NO_PARTICIPATE", "trigger": "数据不全/刚上市/risk.veto"},
        {"tier": "OBSERVE", "trigger": "60<=composite<75"},
        {"tier": "SMALL_POSITION", "trigger": "composite>=75 且 risk 未命中否决/降级"},
        {"tier": "OPPORTUNITY_ENHANCE", "trigger": "composite>=85 且 资金持续性/相对强弱双强"},
        {"tier": "NO_CHASE_HIGH", "trigger": "risk.chase_high"},
        {"tier": "MARKET_RISK_HIGH", "trigger": "market_regime∈{WEAK,BEAR} 或 high_vol"},
    ],
}
