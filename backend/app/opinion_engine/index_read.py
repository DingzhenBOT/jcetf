"""指数自解读（确定性、无 LLM）。

humanize_index_read(code, name, points) -> {"read": str, "signals": List[str]}
- points：升序的 INDEX BAR 序列（含 close/volume/amount/change_percent），
  来源 quote_repo.get_bar_history("INDEX", code, ...)。
- 仅做阈值化叙述，不引入外部判断；输出「该指数代表的宽基/板块是否值得参与」的
  人话理由 + 结构化标签 chip（供前端展示）。
- 对全部宽基指数通用（含无跟踪 ETF 的上证综指/深证成指）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

MA_WINDOW = 20
VOL_SHORT = 5
VOL_LONG = 20


def _acc(p: Any, key: str) -> Any:
    """兼容 ORM 对象（属性）与 dict（键）两种取值。"""
    if isinstance(p, dict):
        return p.get(key)
    return getattr(p, key, None)


def _num(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # 过滤 NaN


def _mean(xs: Sequence[float]) -> float:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else 0.0


def _pct(x: float) -> str:
    return f"{abs(x) * 100:.2f}%"


def _extract(points: Sequence[Any]) -> List[Dict[str, Optional[float]]]:
    out: List[Dict[str, Optional[float]]] = []
    for p in points:
        out.append(
            {
                "date": _acc(p, "trading_date") or _acc(p, "date"),
                "close": _num(_acc(p, "close")),
                "volume": _num(_acc(p, "volume")),
                "amount": _num(_acc(p, "amount")),
                "change_percent": _num(_acc(p, "change_percent")),
            }
        )
    return out


def humanize_index_read(
    code: str, name: str, points: Sequence[Any]
) -> Dict[str, Any]:
    """把指数历史形态翻译成人话理由 + 标签。"""
    recs = _extract(points)
    if len(recs) < 2:
        return {
            "read": f"暂无足够的历史数据，暂时无法对{name}（{code}）形成明确判断，建议先以观察为主。",
            "signals": [],
        }

    closes = [r["close"] for r in recs if r["close"] is not None]
    n = len(closes)
    last = closes[-1]
    first = closes[0]
    cum_return = (last / first - 1) if first else 0.0
    today = recs[-1]["change_percent"] or 0.0

    # 均线位置（不足 MA_WINDOW 则用全部可用数据）
    ma_src = closes[-MA_WINDOW:] if n >= MA_WINDOW else closes
    ma = _mean(ma_src)
    gap = (last / ma - 1) if ma else 0.0
    if abs(gap) < 0.01:
        ma_pos = "near"
    elif last > ma:
        ma_pos = "above"
    else:
        ma_pos = "below"

    # 量能：近 VOL_SHORT 日均值 vs 前 VOL_LONG 日均值
    vols = [r["volume"] for r in recs if r["volume"] is not None]
    if len(vols) >= VOL_SHORT + 1:
        short_avg = _mean(vols[-VOL_SHORT:])
        long_window = vols[-VOL_LONG:] if len(vols) >= VOL_LONG else vols[:-VOL_SHORT]
        long_avg = _mean(long_window)
        vol_ratio = (short_avg / long_avg) if long_avg else 1.0
    else:
        vol_ratio = 1.0
    if vol_ratio > 1.2:
        vol_state = "up"
    elif vol_ratio < 0.8:
        vol_state = "down"
    else:
        vol_state = "flat"

    # 短期动能：最近若干日涨跌分布
    recent = [r["change_percent"] for r in recs[-VOL_SHORT:] if r["change_percent"] is not None]
    up_days = sum(1 for c in recent if c > 0)
    down_days = sum(1 for c in recent if c < 0)
    if up_days > down_days:
        momentum = "strong"
    elif down_days > up_days:
        momentum = "weak"
    else:
        momentum = "mixed"

    # ---- 标签 chip ----
    signals: List[str] = []
    signals.append("站上20日线" if ma_pos == "above" else ("跌破20日线" if ma_pos == "below" else "20日线附近"))
    signals.append({"up": "放量", "down": "缩量", "flat": "量能持平"}[vol_state])
    signals.append({"strong": "短期偏强", "weak": "短期偏弱", "mixed": "短期震荡"}[momentum])
    signals.append("今日上涨" if today >= 0 else "今日下跌")

    # ---- 人话叙述 ----
    win_label = f"近 {len(closes)} 个交易日"
    trend_word = "上涨" if cum_return >= 0 else "下跌"
    today_word = "上涨" if today >= 0 else "下跌"
    ma_phrase = (
        "站上 20 日均线"
        if ma_pos == "above"
        else ("跌破 20 日均线" if ma_pos == "below" else "围绕 20 日均线窄幅震荡")
    )
    mom_phrase = (
        "短期动能偏强"
        if momentum == "strong"
        else ("短期动能偏弱" if momentum == "weak" else "短期方向尚不明朗")
    )
    s1 = (
        f"{win_label}{name}累计{trend_word}{_pct(cum_return)}，"
        f"今日{today_word}{abs(today):.2f}%，{ma_phrase}，{mom_phrase}。"
    )

    vol_phrase = {
        "up": "近 5 日成交量较前阶段明显放大，说明资金参与度提升，行情有量能配合。",
        "down": "近 5 日成交量较前阶段萎缩，资金观望情绪较浓，行情持续性仍需观察。",
        "flat": "近 5 日成交量与前阶段基本持平，多空分歧不大。",
    }[vol_state]

    if cum_return < 0 or ma_pos == "below":
        s3 = "综合看，当前位置不宜急于介入，建议等待企稳信号（放量止跌或重新站回均线）后再考虑。"
    elif cum_return > 0 and ma_pos == "above":
        s3 = "综合看，可逢回调关注，但应避免在连续拉升、情绪过热后追高，注意控制仓位。"
    else:
        s3 = "综合看，方向尚不明朗，建议以观察为主、控制仓位，待趋势确认后再做决策。"

    read = s1 + vol_phrase + s3
    return {"read": read, "signals": signals}
