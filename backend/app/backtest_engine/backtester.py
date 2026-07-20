"""日线回测引擎（P7，DESIGN §10 / R4 / R5 / R8 / R9）。

设计要点
--------
- **复用冻结的规则引擎**：按交易日循环调用 `StrategyEngine.evaluate_etf(session, mapping,
  version, as_of)` 产出当日信号档位，再映射为「多头/空仓」立场。**不复制规则逻辑**，保证与
  DESIGN §9 冻结引擎一致（LLM 只润色不判断 / 确定性规则不可改）。
- **R4 无未来数据**：信号基于截至 `as_of` 当日窗口计算（引擎内部 `end=as_of`）；**成交价取
  信号日的下一可交易时刻（次根 BAR 开盘）**，绝对不碰未来数据。
- **R9 涨跌停 / 停牌**：次根 BAR 若为涨停（close≈前收×1.10）则「不买」；跌停则「不卖」；
  ETF 停牌（无 BAR）天然被「下一可用 BAR」跳过。
- **R8 前复权**：回测价格一律使用前复权 BAR（回填默认前复权，DESIGN §0 假设）；实时展示用
  不复权，二者靠 `data_kind`/配置区分（已知限制见 devlog）。
- **R5 样本内/外分离**：将交易日序列在 `in_sample_end` 处切分（缺省 70/30），分别计算指标并
  对比；每笔交易按入场日标记 IN/OUT/FULL。策略参数来自**已注册不可覆盖**的 `strategy_version`
  （白名单，不可现场编造）。
- **成本模型**：佣金 `commission_per_thousand/1000` + 滑点 `slippage_bps/10000`，双边收取。

立场模型（MVP）：单 ETF 多头/空仓（long/flat）。
  OPPORTUNITY_ENHANCE / SMALL_POSITION -> 满仓(1.0)；其余 -> 空仓(0.0)。
  更细的仓位区间（DESIGN §9.6 数值区间）为后续增强，不影响策略信号正确性。

性能（R6）：MVP 逐日调用引擎（小样本回测足够）；批量预读 + 向量化指标为后续优化项。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models.backtest import BacktestRun, BacktestTrade
from app.db.models.market import MarketQuote
from app.repository import mapping_repo, quote_repo
from app.strategy_engine.engine import StrategyEngine

# 信号档位 -> 目标仓位（long/flat 立场模型，MVP）
SIGNAL_TARGET_WEIGHT: Dict[str, float] = {
    "OPPORTUNITY_ENHANCE": 1.0,
    "SMALL_POSITION": 1.0,
    "OBSERVE": 0.0,
    "NO_PARTICIPATE": 0.0,
    "NO_CHASE_HIGH": 0.0,
    "MARKET_RISK_HIGH": 0.0,
}

# 最少 BAR 数（需覆盖 ma20 / rsi14 等窗口）
MIN_BARS = 60

# 涨跌停判定阈值（ETF ±10%，留 1‰ 容差处理四舍五入）
LIMIT_UP_FACTOR = 1.099
LIMIT_DOWN_FACTOR = 0.901


def _bars_to_dict(rows: List[MarketQuote]) -> Dict[date, MarketQuote]:
    """MarketQuote 列表 -> {trading_date: row}（升序，通常一一对应）。"""
    return {r.trading_date: r for r in rows}


def _limit_up(row: MarketQuote) -> bool:
    if row.previous_close and row.previous_close > 0 and row.close is not None:
        return float(row.close) >= float(row.previous_close) * LIMIT_UP_FACTOR
    return False


def _limit_down(row: MarketQuote) -> bool:
    if row.previous_close and row.previous_close > 0 and row.close is not None:
        return float(row.close) <= float(row.previous_close) * LIMIT_DOWN_FACTOR
    return False


def _compute_metrics(equity: List[Tuple[str, float]]) -> Dict[str, Any]:
    """由净值曲线（[(date_iso, value), ...]）计算绩效指标。纯 Python，无 pandas 依赖。"""
    if len(equity) < 2:
        return {
            "total_return_pct": None,
            "annualized_return_pct": None,
            "max_drawdown_pct": None,
            "sharpe": None,
            "trades_count": 0,
            "win_rate": None,
            "avg_pnl_pct": None,
        }

    values = [v for _, v in equity]
    start_val = values[0]
    end_val = values[-1]
    n_days = len(values)

    total_return = (end_val / start_val - 1.0) if start_val > 0 else 0.0
    # 年化（交易日按 252）
    if end_val > 0 and n_days > 1:
        annualized = ((end_val / start_val) ** (252.0 / (n_days - 1)) - 1.0)
    else:
        annualized = 0.0

    # 最大回撤
    peak = values[0]
    max_dd = 0.0
    for v in values:
        if v > peak:
            peak = v
        if peak > 0:
            dd = v / peak - 1.0
            if dd < max_dd:
                max_dd = dd

    # 日收益序列 -> Sharpe（rf=0）
    daily = [(values[i] / values[i - 1] - 1.0) for i in range(1, len(values))]
    mean_r = sum(daily) / len(daily)
    var = sum((d - mean_r) ** 2 for d in daily) / len(daily)
    std_r = var ** 0.5
    sharpe = (mean_r / std_r * (252 ** 0.5)) if std_r > 0 else 0.0

    return {
        "total_return_pct": round(total_return * 100.0, 4),
        "annualized_return_pct": round(annualized * 100.0, 4),
        "max_drawdown_pct": round(max_dd * 100.0, 4),
        "sharpe": round(sharpe, 4),
        "trades_count": 0,  # 由外层按样本填充
        "win_rate": None,
        "avg_pnl_pct": None,
    }


def _split_equity(
    equity: List[Tuple[str, float]], in_sample_end: date
) -> Tuple[List[Tuple[str, float]], List[Tuple[str, float]]]:
    """按 in_sample_end（不含）将净值曲线切为样本内/外两段（ISO 字符串可直接比较）。"""
    in_eq: List[Tuple[str, float]] = []
    out_eq: List[Tuple[str, float]] = []
    boundary = in_sample_end.isoformat()
    for d_iso, v in equity:
        if d_iso < boundary:
            in_eq.append((d_iso, v))
        else:
            out_eq.append((d_iso, v))
    return in_eq, out_eq


def _benchmark_buy_hold(
    bench_rows: List[MarketQuote],
    trading_dates: List[date],
    initial_capital: float,
    commission_rate: float,
    slippage_rate: float,
) -> Dict[str, Any]:
    """基准买入持有：首根开盘买入、末根收盘卖出（双边成本）。无数据返回 None。"""
    if not bench_rows or len(trading_dates) < 2:
        return {"return_pct": None, "equity_curve": [], "available": False}
    first = bench_rows[0]
    last = bench_rows[-1]
    if first.open is None or last.close is None:
        return {"return_pct": None, "equity_curve": [], "available": False}

    buy_net = float(first.open) * (1 + slippage_rate)
    qty = initial_capital / buy_net
    commission_buy = initial_capital * commission_rate
    proceeds = qty * float(last.close)
    commission_sell = proceeds * commission_rate
    end_cash = initial_capital - commission_buy + proceeds - commission_sell
    ret = end_cash / initial_capital - 1.0

    # 基准净值曲线（每日按 close 标记）
    bars_by_date = _bars_to_dict(bench_rows)
    eq: List[Tuple[str, float]] = []
    for d in trading_dates:
        row = bars_by_date.get(d)
        if row is None or row.close is None:
            continue
        eq.append((d.isoformat(), initial_capital - commission_buy + qty * float(row.close)))
    return {
        "return_pct": round(ret * 100.0, 4),
        "equity_curve": [{"date": d, "value": round(v, 2)} for d, v in eq],
        "available": True,
    }


def _compute_backtest(
    session: Session,
    *,
    etf_code: str,
    start_date: date,
    end_date: date,
    initial_capital: float,
    benchmark: str,
    strategy_version: str,
    in_sample_end: Optional[date],
    settings: Settings,
    run: Optional[BacktestRun] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """回测纯计算（可被单测直接调用）。返回 (results_dict, trades_list)。

    不负责状态机；调用方（runner）负责写库与进度。若 run 传入，则周期性提交 progress。
    """
    engine = StrategyEngine(settings)

    # 1) 加载 ETF BAR（前复权假设，R8）
    etf_rows = quote_repo.get_bar_history(session, "ETF", etf_code, start_date, end_date)
    if len(etf_rows) < MIN_BARS:
        raise ValueError(
            f"insufficient ETF BAR for {etf_code}: got {len(etf_rows)}, need >= {MIN_BARS} "
            f"(回测需覆盖 ma20/rsi14 等指标窗口)"
        )
    etf_rows.sort(key=lambda r: r.timestamp)
    bars_by_date = _bars_to_dict(etf_rows)
    trading_dates = [r.trading_date for r in etf_rows]

    # 基准 BAR（买入持有对比）
    bench_rows = quote_repo.get_bar_history(session, "ETF", benchmark, start_date, end_date)
    bench_rows.sort(key=lambda r: r.timestamp)

    # 数据可用性统计（仅记录，不阻断）
    idx_rows = quote_repo.get_bar_history(
        session, "INDEX", settings.strategy.broad_index_codes[0], start_date, end_date
    )
    breadth_first = quote_repo.get_breadth_on_date(session, trading_dates[0])
    breadth_last = quote_repo.get_breadth_on_date(session, trading_dates[-1])

    # 样本内/外分界（缺省 70/30）
    if in_sample_end is None:
        split_idx = max(1, int(len(trading_dates) * 0.7))
        in_sample_end = trading_dates[split_idx]
    elif in_sample_end <= trading_dates[0]:
        in_sample_end = trading_dates[1]

    # 2) 主循环
    cash = initial_capital
    shares = 0.0
    current_weight = 0.0
    entry_net = 0.0
    current_qty = 0.0
    pending_action: Optional[str] = None  # BUY / SELL（来自前一日信号）
    pending_reason: Optional[str] = None

    commission_rate = settings.backtest.commission_per_thousand / 1000.0
    slippage_rate = settings.backtest.slippage_bps / 10000.0

    equity: List[Tuple[str, float]] = []
    trades: List[Dict[str, Any]] = []

    total = len(trading_dates)
    last_i = total - 1

    for i, as_of in enumerate(trading_dates):
        bar = bars_by_date[as_of]

        # 2a) 执行前一日挂起的动作（次根开盘，R4）
        if pending_action == "BUY" and not _limit_up(bar):
            open_px = float(bar.open) if bar.open is not None else float(bar.close)
            net = open_px * (1 + slippage_rate)
            qty = cash / net
            commission = cash * commission_rate
            cash = cash - cash - commission  # 全部投入
            shares += qty
            current_weight = 1.0
            entry_net = net
            current_qty = qty
            trades.append({
                "etf_code": etf_code,
                "sample": "IN" if as_of < in_sample_end else "OUT",
                "entry_time": bar.timestamp.isoformat() if isinstance(bar.timestamp, datetime) else str(bar.timestamp),
                "exit_time": None,
                "entry_price": round(net, 4),
                "exit_price": None,
                "qty": round(qty, 4),
                "pnl": None,
                "pnl_percent": None,
                "reason": pending_reason,
            })
            pending_action = None
        elif pending_action == "SELL" and not _limit_down(bar):
            open_px = float(bar.open) if bar.open is not None else float(bar.close)
            proceeds = shares * open_px
            commission = proceeds * commission_rate
            cash += proceeds - commission
            # 本笔交易盈亏（扣双边佣金）
            pnl = (open_px * (1 - slippage_rate) - entry_net) * current_qty - commission - (
                entry_net * current_qty * commission_rate
            )
            pnl_pct = (pnl / (entry_net * current_qty)) * 100.0 if entry_net * current_qty > 0 else 0.0
            if trades:
                trades[-1]["exit_time"] = bar.timestamp.isoformat() if isinstance(bar.timestamp, datetime) else str(bar.timestamp)
                trades[-1]["exit_price"] = round(open_px * (1 - slippage_rate), 4)
                trades[-1]["pnl"] = round(pnl, 4)
                trades[-1]["pnl_percent"] = round(pnl_pct, 4)
            shares = 0.0
            current_weight = 0.0
            pending_action = None

        # 2b) 当日信号 -> 目标立场
        mapping = mapping_repo.get_active_mappings(session, as_of=as_of)
        mp = next((m for m in mapping if m.etf_code == etf_code), None)
        if mp is None:
            # 该历史日无生效映射 -> 空仓立场（不交易）
            target = 0.0
            sig_type = None
        else:
            sig = engine.evaluate_etf(session, mp, strategy_version, as_of)
            sig_type = sig.get("signal_type", "")
            target = SIGNAL_TARGET_WEIGHT.get(sig_type, 0.0)

        if target > current_weight:
            pending_action = "BUY"
            pending_reason = sig_type
        elif target < current_weight:
            pending_action = "SELL"

        # 2c) 当日收盘标记净值
        close_px = float(bar.close) if bar.close is not None else 0.0
        eq_val = cash + shares * close_px
        equity.append((as_of.isoformat(), round(eq_val, 2)))

        # 进度（传入 run 时周期提交）
        if run is not None and (i % max(1, total // 10) == 0):
            run.progress = int(round((i + 1) / total * 100))
            session.commit()

    # 3) 期末强制平仓（末根收盘，落袋盈亏）
    if shares > 0 and trading_dates:
        last_date = trading_dates[-1]
        last_bar = bars_by_date[last_date]
        close_px = float(last_bar.close) if last_bar.close is not None else 0.0
        net = close_px * (1 - slippage_rate)
        proceeds = shares * close_px
        commission = proceeds * commission_rate
        cash += proceeds - commission
        pnl = (net - entry_net) * current_qty - commission - (entry_net * current_qty * commission_rate)
        pnl_pct = (pnl / (entry_net * current_qty)) * 100.0 if entry_net * current_qty > 0 else 0.0
        if trades:
            trades[-1]["exit_time"] = last_bar.timestamp.isoformat() if isinstance(last_bar.timestamp, datetime) else str(last_bar.timestamp)
            trades[-1]["exit_price"] = round(net, 4)
            trades[-1]["pnl"] = round(pnl, 4)
            trades[-1]["pnl_percent"] = round(pnl_pct, 4)
        equity[-1] = (last_date.isoformat(), round(cash, 2))  # 期末现金即净值
        shares = 0.0

    # 4) 指标（样本内/外/全样本）
    in_eq, out_eq = _split_equity(equity, in_sample_end)
    full_metrics = _compute_metrics(equity)
    in_metrics = _compute_metrics(in_eq)
    out_metrics = _compute_metrics(out_eq)

    # 交易统计按样本（FULL = 全部交易；IN/OUT 按入场日标记）
    def _trade_stats(sample_tag: str) -> Dict[str, Any]:
        if sample_tag == "FULL":
            sub = trades
        else:
            sub = [t for t in trades if t["sample"] == sample_tag]
        closed = [t for t in sub if t["pnl"] is not None]
        n = len(closed)
        wins = [t for t in closed if (t["pnl"] or 0) > 0]
        avg = (sum(t["pnl_percent"] for t in closed) / n) if n else None
        return {
            "trades_count": n,
            "win_rate": round(len(wins) / n * 100.0, 2) if n else None,
            "avg_pnl_pct": round(avg, 4) if avg is not None else None,
        }

    in_stats = _trade_stats("IN")
    out_stats = _trade_stats("OUT")
    full_stats = _trade_stats("FULL")
    in_metrics.update(in_stats)
    out_metrics.update(out_stats)
    full_metrics.update(full_stats)

    benchmark_result = _benchmark_buy_hold(
        bench_rows, trading_dates, initial_capital, commission_rate, slippage_rate
    )

    results: Dict[str, Any] = {
        "in_sample": {
            "start": in_eq[0][0] if in_eq else None,
            "end": in_eq[-1][0] if in_eq else None,
            "metrics": in_metrics,
            "equity_curve": [{"date": d, "value": v} for d, v in in_eq],
        },
        "out_of_sample": {
            "start": out_eq[0][0] if out_eq else None,
            "end": out_eq[-1][0] if out_eq else None,
            "metrics": out_metrics,
            "equity_curve": [{"date": d, "value": v} for d, v in out_eq],
        },
        "full": {
            "start": equity[0][0] if equity else None,
            "end": equity[-1][0] if equity else None,
            "metrics": full_metrics,
            "equity_curve": [{"date": d, "value": v} for d, v in equity],
        },
        "benchmark": benchmark_result,
        "params": {
            "etf_code": etf_code,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "initial_capital": initial_capital,
            "strategy_version": strategy_version,
            "in_sample_end": in_sample_end.isoformat(),
            "benchmark_etf": benchmark,
            "commission_per_thousand": settings.backtest.commission_per_thousand,
            "slippage_bps": settings.backtest.slippage_bps,
        },
        "data_availability": {
            "etf_bars": len(etf_rows),
            "benchmark_bars": len(bench_rows),
            "index_bars": len(idx_rows),
            "breadth_available": breadth_first is not None or breadth_last is not None,
            "note": "价格使用前复权 BAR（R8）；实时展示用不复权，靠 data_kind 区分",
        },
        "notes": [
            "MVP 立场模型为单 ETF 多头/空仓（long/flat）；更细仓位区间见 DESIGN §9.6（后续增强）",
            "成交价取信号日次根 BAR 开盘（R4 无未来数据）",
            "涨停不买 / 跌停不卖 / 停牌跳过（R9）",
        ],
    }
    return results, trades
