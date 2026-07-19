"""风险过滤（P3，DESIGN §9.1-5）。

输入 metrics（由 strategy_engine 汇总）：
  - rsi14: ETF 的 RSI(14)
  - sector_surge: 板块/ETF 短期涨幅过大（过热）标志
  - market_regime: 市场环境（STRONG_UP/TREND_UP/VOLATILE/WEAK/BEAR）
  - drawdown_pct: ETF 距近期高点回撤（负数为回撤）
  - atr_pct: ETF 波动率（ATR/收盘）
  - missing_data: 宽基指数/宽度数据是否缺失（否决条件依赖此项）
输出：
  - veto: 硬否决（默认仅「大盘 BEAR 且 数据缺失」）
  - downgrade: 降级（追高 / 高波动）
  - high_vol / chase_high: 细分标志
  - reasons / flags（flags 受 settings.strategy.risk_filter 开关约束）
"""
from __future__ import annotations

from typing import Any, Dict

# 高波动阈值（ATR%）：超过视为高波动
ATR_PCT_HIGH_VOL = 4.0
# 回撤超过此幅度（绝对值）触发风险标记
DRAWDOWN_RISK_PCT = 15.0


class RiskEngine:
    def __init__(self, risk_filter: Dict[str, Any] | None = None) -> None:
        # 默认开启两项（与 StrategyConfig 默认一致）
        self.risk_filter = risk_filter or {
            "deny_market_bear_with_missing_data": True,
            "downgrade_on_chase_high": True,
        }

    def evaluate(self, metrics: Dict[str, Any]) -> Dict[str, Any]:
        veto = False
        downgrade = False
        high_vol = False
        chase_high = False
        reasons: list[str] = []
        flags: Dict[str, Any] = {}

        rsi = metrics.get("rsi14")
        if rsi is not None and rsi == rsi and rsi > 80:
            chase_high = True
            downgrade = True
            reasons.append("rsi_overheat>80")

        if metrics.get("sector_surge"):
            chase_high = True
            downgrade = True
            reasons.append("sector_short_surge")

        regime = metrics.get("market_regime")
        if regime in ("WEAK", "BEAR"):
            high_vol = True
            reasons.append(f"market_regime={regime}")

        atr_pct = metrics.get("atr_pct")
        if atr_pct is not None and atr_pct == atr_pct and atr_pct > ATR_PCT_HIGH_VOL:
            high_vol = True
            downgrade = True
            reasons.append("atr_pct_high")

        dd = metrics.get("drawdown_pct")
        if dd is not None and dd == dd and dd < -DRAWDOWN_RISK_PCT:
            reasons.append("drawdown_from_high")

        # 否决：仅「大盘 BEAR 且 数据缺失」（受开关约束）
        if (
            self.risk_filter.get("deny_market_bear_with_missing_data")
            and regime == "BEAR"
            and metrics.get("missing_data")
        ):
            veto = True
            reasons.append("deny_market_bear_with_missing_data")

        # 降级：追高/高波动（受开关约束）
        if not self.risk_filter.get("downgrade_on_chase_high"):
            downgrade = False

        flags["deny_market_bear_with_missing_data"] = veto
        flags["downgrade_on_chase_high"] = downgrade

        return {
            "veto": veto,
            "downgrade": downgrade,
            "high_vol": high_vol,
            "chase_high": chase_high,
            "reasons": reasons,
            "flags": flags,
        }
