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
from app.repository import mapping_repo
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
    if as_of is None:
        as_of = trading_date_for()

    version = mint_strategy_version(session, settings, RULES_V1)
    mappings = mapping_repo.get_active_mappings(session, as_of)

    strategy_engine = StrategyEngine(settings)
    opinion_engine = OpinionEngine()

    result: Dict[str, Any] = {
        "as_of": as_of.isoformat(),
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
        try:
            sig = strategy_engine.evaluate_etf(session, m, version, as_of)

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
                    if k.startswith("_"):
                        continue
                    setattr(s, k, v)
                s.generated_at = utcnow()
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
                    **{k: v for k, v in sig.items() if not k.startswith("_")},
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
                o.input_summary = input_summary
                o.template_version = opin["template_version"]
                o.model_version = opin["model_version"]
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
                    input_summary=input_summary,
                    template_version=opin["template_version"],
                    model_version=opin["model_version"],
                )
                session.add(o)
                result["opinions_written"] += 1

        except Exception as e:  # noqa: BLE001 - 单支映射异常不中断其余
            result["errors"].append({"etf_code": m.etf_code, "error": str(e)})

    return result
