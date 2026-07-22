"""策略规则（DESIGN §9）。

本字典是 mint_strategy_version 参与哈希的「规则」部分；纯文本转录，便于审计与复现。
任何字段顺序/取值变化都会产生新 strategy_version（不可覆盖，旧版本禁止 UPDATE）。
引擎逻辑（engine.py）按此描述实现；此处只描述「规则是什么」，不含代码。

版本演进：
- 1.0（P3 冻结）：五类评分确定性规则 + 档位映射。
- 2.0（方案B 扩展）：在 1.0 之上新增「量价关系技术分析」段（volume_price_ta），
  作为 additive 触发规则与档位增强，不改变原 composite 权重公式（扩展而非覆盖）。
  hash 变化 -> 自动生成新 strategy_version 行，旧版本保留不可改写。
"""
from __future__ import annotations

from typing import Dict

RULES_V1: Dict = {
    "version": "2.0",
    "description": "DESIGN §9 五类评分确定性规则 + 方案B 量价关系技术分析（扩展 v2.0）",
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
    "volume_price_ta": {
        "description": "方案B 量价关系技术分析（additive，不改 composite 权重；参考 ta_signals.md）",
        "min_bars": 21,
        "vol_state_bands": {"放量": ">1.5", "温和放量": "[1.0,1.5)", "平量": "[0.8,1.0)", "缩量": "[0.5,0.8)", "极度缩量": "<0.5"},
        "state_matrix": {
            "VOL_UP_RISE": "放量上涨（做多）",
            "VOL_DOWN_RISE": "缩量上涨（动力不足）",
            "VOL_UP_FALL": "放量下跌（出货）",
            "VOL_DOWN_FALL": "缩量下跌（洗盘）",
            "VOL_LOW_FLAT": "缩量横盘（蓄势）",
        },
        "patterns": {
            "breakout_volume": "站上MA20 + 量比>1.5 + 动量正",
            "shrink_wash": "下跌日缩量(<0.8)且守住MA20",
            "divergence": "价创20日新高但量比<1（价升量缩）",
            "segment_up": "近3日阳线且量能递增",
            "anomaly": "量比>2.5 或 单日涨跌幅>5%",
        },
        "tier_enhance": "strong_breakout(breakout_volume|segment_up) 且 etf_rs>=60 且 非降级 -> OBSERVE→SMALL_POSITION / SMALL_POSITION→OPPORTUNITY_ENHANCE",
        "strength_score": "量价配合 + 趋势位置(MA20) + 形态，基准50，clamp[0,100]",
    },
}
