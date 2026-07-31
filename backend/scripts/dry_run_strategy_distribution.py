"""只读演算指定交易日的策略分布，不写 Signal / Opinion / StrategyVersion。"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import make_engine
from app.repository import mapping_repo
from app.strategy_engine.engine import StrategyEngine


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", required=True, type=date.fromisoformat)
    parser.add_argument("--phase", default="post_close")
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args()

    settings = get_settings(force_reload=True)
    engine = make_engine(settings)
    rows = []
    errors = []
    with Session(engine, future=True) as session:
        evaluator = StrategyEngine(settings)
        for mapping in mapping_repo.get_active_mappings(session, args.as_of):
            try:
                signal = evaluator.evaluate_etf(
                    session, mapping, "dry-run", args.as_of, phase=args.phase
                )
                supporting = signal.get("supporting_metrics") or {}
                rows.append(
                    {
                        "etf_code": mapping.etf_code,
                        "signal_type": signal.get("signal_type"),
                        "base_signal_type": supporting.get("base_signal_type"),
                        "score": signal.get("score"),
                        "confidence": signal.get("confidence"),
                        "market_regime": signal.get("market_regime"),
                        "fund_flow_score": supporting.get("fund_flow_score"),
                        "component_scores": supporting.get("component_scores") or {},
                        "effective_weights": supporting.get("effective_weights") or {},
                        "adjustments": supporting.get("decision_adjustments") or [],
                    }
                )
            except Exception as exc:  # pragma: no cover - 生产诊断输出
                errors.append({"etf_code": mapping.etf_code, "error": repr(exc)})
        session.rollback()

    score_rows = [row for row in rows if row["score"] is not None]
    adjustment_counts = Counter(
        adjustment for row in rows for adjustment in row["adjustments"]
    )
    result = {
        "as_of": args.as_of.isoformat(),
        "phase": args.phase,
        "evaluated": len(rows),
        "errors": errors,
        "signal_types": Counter(row["signal_type"] for row in rows),
        "base_signal_types": Counter(row["base_signal_type"] for row in rows),
        "market_regimes": Counter(row["market_regime"] for row in rows),
        "fund_flow_available": sum(row["fund_flow_score"] is not None for row in rows),
        "adjustments": adjustment_counts,
        "score_min": min((row["score"] for row in score_rows), default=None),
        "score_max": max((row["score"] for row in score_rows), default=None),
        "top": sorted(
            score_rows, key=lambda row: row["score"], reverse=True
        )[: max(0, args.top)],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, default=dict))


if __name__ == "__main__":
    main()
