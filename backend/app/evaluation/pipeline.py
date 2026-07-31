"""采集后评估流水线（P3，DESIGN §0 / §9）。

post_collection_evaluate(session, settings, *, phase, as_of) -> dict
- 1) version = mint_strategy_version(...)（不可覆盖，复用已存在行）。
- 2) mappings = get_active_mappings(as_of)。
- 3) 每支映射：StrategyEngine.evaluate_etf -> 幂等 upsert Signal；
       OpinionEngine.generate -> 幂等 upsert Opinion。
- 返回 {signals_written, signals_updated, opinions_written, opinions_updated, skipped, errors}。

幂等（§7.1）：
- Signal 自然键 (trading_date, target_etf, strategy_version)；存在则原地更新（保持 signal_id 稳定）。
- Opinion 自然键 (trading_date, signal_id, phase)；存在则原地更新（signal_id 指向稳定父信号）。
  注：Opinion 模型无 target_etf 列（P1 定义），用 signal_id 关联即可唯一，故不改动 schema。
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.db.models.signal_opinion import Opinion, Signal
from app.market_calendar import trading_date_for
from app.opinion_engine.engine import OpinionEngine
from app.repository import mapping_repo, quote_repo
from app.strategy_engine.engine import StrategyEngine
from app.strategy_engine.rules import RULES_V1
from app.strategy_versioning import mint_strategy_version


def post_collection_evaluate(
    session: Session,
    settings,
    *,
    phase: str = "post_close",
    as_of: Optional[date] = None,
) -> Dict[str, Any]:
    requested_as_of: Optional[date] = as_of
    bar_coverage: Optional[tuple[date, int, int]] = None
    if as_of is None:
        requested_as_of = trading_date_for()
        as_of = requested_as_of
        if phase == "post_close":
            requested_mappings = mapping_repo.get_active_mappings(session, requested_as_of)
            exchange_codes = [
                m.etf_code
                for m in requested_mappings
                if (getattr(m, "listing", None) or "场内") != "场外"
            ]
            bar_coverage = quote_repo.get_latest_daily_bar_coverage(
                session,
                "ETF",
                exchange_codes,
                on_or_before=requested_as_of,
                min_coverage_ratio=settings.data_quality.post_close_min_bar_coverage_ratio,
            )
            if bar_coverage is not None:
                as_of = bar_coverage[0]

    version = mint_strategy_version(session, settings, RULES_V1)
    mappings = mapping_repo.get_active_mappings(session, as_of)

    strategy_engine = StrategyEngine(settings)
    opinion_engine = OpinionEngine()

    result: Dict[str, Any] = {
        "as_of": as_of.isoformat(),
        "requested_as_of": requested_as_of.isoformat() if requested_as_of else None,
        "bar_coverage": (
            {"actual": bar_coverage[1], "required": bar_coverage[2]}
            if bar_coverage is not None
            else None
        ),
        "phase": phase,
        "strategy_version": version,
        "signals_written": 0,
        "signals_updated": 0,
        "opinions_written": 0,
        "opinions_updated": 0,
        "skipped": 0,
        "errors": [],
    }

    for m in mappings:
        # 场外基金 T+1、无盘中分时 -> 仅收盘后评估；live/lunch 相位跳过（不计入 errors）
        # getattr 兜底：m 来自 get_active_mappings（真实 ORM 行，listing 存在；None 默认"场内"）。
        if (getattr(m, "listing", None) or "场内") == "场外" and phase != "post_close":
            result["skipped_offexchange"] = result.get("skipped_offexchange", 0) + 1
            continue
        try:
            sig = strategy_engine.evaluate_etf(session, m, version, as_of, phase=phase)

            # --- Signal 幂等 upsert（按 trading_date+target_etf+version） ---
            existing = session.execute(
                select(Signal).where(
                    Signal.trading_date == as_of,
                    Signal.target_etf == m.etf_code,
                    Signal.strategy_version == version,
                )
            ).first()
            if existing:
                s = existing[0]
                for k, v in sig.items():
                    if k.startswith("_") or k == "trade_plan":  # trade_plan 仅落 Opinion，不入 Signal
                        continue
                    setattr(s, k, v)
                s.generated_at = utcnow()
                s.phase = phase  # 该信号最后由哪个阶段评估生成（盘中/收盘后）
                result["signals_updated"] += 1
                signal_id = s.signal_id
            else:
                signal_id = str(uuid.uuid4())
                s = Signal(
                    signal_id=signal_id,
                    strategy_version=version,
                    generated_at=utcnow(),
                    trading_date=as_of,
                    target_etf=m.etf_code,
                    phase=phase,
                    **{k: v for k, v in sig.items() if not k.startswith("_") and k != "trade_plan"},
                )
                session.add(s)
                result["signals_written"] += 1

            # --- Opinion 幂等 upsert（按 trading_date+signal_id+phase） ---
            input_summary = {
                "as_of": as_of.isoformat(),
                "etf_code": m.etf_code,
                "sector_code": (m.related_sector_codes or [None])[0],
                "related_index_code": m.related_index_code,
                "market_regime": sig.get("market_regime"),
            }
            opin = opinion_engine.generate(
                {**sig, "target_etf": m.etf_code}, phase, input_summary
            )
            existing_o = session.execute(
                select(Opinion).where(
                    Opinion.trading_date == as_of,
                    Opinion.signal_id == signal_id,
                    Opinion.phase == phase,
                )
            ).first()
            if existing_o:
                o = existing_o[0]
                o.generated_at = utcnow()
                o.title = opin["title"]
                o.content = opin["content"]
                o.basis_text = opin.get("basis_text")
                o.input_summary = input_summary
                o.template_version = opin["template_version"]
                o.model_version = opin["model_version"]
                o.trade_plan = opin.get("trade_plan")
                result["opinions_updated"] += 1
            else:
                o = Opinion(
                    opinion_id=str(uuid.uuid4()),
                    signal_id=signal_id,
                    generated_at=utcnow(),
                    trading_date=as_of,
                    phase=phase,
                    title=opin["title"],
                    content=opin["content"],
                    basis_text=opin.get("basis_text"),
                    input_summary=input_summary,
                    template_version=opin["template_version"],
                    model_version=opin["model_version"],
                    trade_plan=opin.get("trade_plan"),
                )
                session.add(o)
                result["opinions_written"] += 1

        except Exception as e:  # noqa: BLE001 - 单支映射异常不中断其余
            # 回滚，复位事务状态：避免单支 ETF 的 DB 级异常（如缺列/坏查询）污染 session，
            # 导致后续 ETF 评估与循环结束后的查询连锁失败（表现为顶层 500）。worker 周期重试会补齐。
            session.rollback()
            result["errors"].append({"etf_code": m.etf_code, "error": str(e)})

    return result
