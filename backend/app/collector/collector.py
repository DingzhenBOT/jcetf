"""采集编排（DESIGN §3.1 / §8 / P2）。

Collector 把「provider 取数 -> normalize 映射 -> 质量评估 -> 切源标记 -> 幂等入库 -> 数据源状态」
串成可幂等重跑的任务。每个采集方法：成功记 OK + 重置连续失败；失败记 FAILED + 连续失败+1，
不抛到上层（由 worker.run_job 兜底日志）。业务代码只依赖 BaseDataProvider。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.collector import normalize
from app.config import Settings
from app.data_provider.base import BaseDataProvider
from app.data_quality.checker import assess
from app.logging_conf import get_logger
from app.market_calendar import is_trading_now
from app.repository import quote_repo


class Collector:
    def __init__(self, provider: BaseDataProvider, settings: Settings):
        self.provider = provider
        self.settings = settings
        self.log = get_logger("etf-collector")

    # ---- 内部工具 ----
    def _now(self) -> datetime:
        return datetime.now(timezone.utc).replace(tzinfo=None)

    def _record_failure(
        self, session: Session, *, source: Optional[str], symbol_type: str, now: datetime, err: str
    ) -> None:
        ds = source or "unknown"
        prev = quote_repo.get_data_source_status(session, ds, symbol_type)
        fails = (prev.consecutive_failures + 1) if prev else 1
        quote_repo.record_data_source_status(
            session,
            data_source=ds,
            symbol_type=symbol_type,
            status="FAILED",
            last_success_at=prev.last_success_at if prev else None,
            last_attempt_at=now,
            consecutive_failures=fails,
            note=err[:500],
        )

    def _record_success(
        self, session: Session, *, source: str, symbol_type: str, now: datetime, note: str
    ) -> None:
        quote_repo.record_data_source_status(
            session,
            data_source=source,
            symbol_type=symbol_type,
            status="OK",
            last_success_at=now,
            last_attempt_at=now,
            consecutive_failures=0,
            note=note[:500],
        )

    # ---- 快照采集（指数/ETF/板块） ----
    def _collect_snapshot(
        self,
        session: Session,
        symbol_type: str,
        fetch_fn,
        source_hint: Optional[str] = None,
    ) -> Dict[str, Any]:
        now = self._now()
        source: Optional[str] = source_hint
        try:
            df = fetch_fn()
            if df is None or (hasattr(df, "empty") and df.empty):
                raise ValueError("data source returned empty")
            source = df.attrs.get("__source") or source_hint or "unknown"
            if symbol_type == "INDEX":
                rows = normalize.normalize_index_snapshot(df, source, now)
            elif symbol_type == "ETF":
                rows = normalize.normalize_etf_snapshot(df, source, now)
            else:
                rows = normalize.normalize_sector_ranking(df, source, symbol_type, now)
            if not rows:
                raise ValueError("no parseable rows after normalize")
        except Exception as e:  # noqa: BLE001 - 采集失败：记状态，不抛出
            self.log.error("collect failed", extra={"symbol_type": symbol_type, "err": str(e)})
            self._record_failure(session, source=source, symbol_type=symbol_type, now=now, err=str(e))
            session.commit()
            return {"symbol_type": symbol_type, "status": "FAILED", "count": 0, "error": str(e)}

        # 质量评估（仅交易时段严格校验时间新鲜度）
        is_trading = is_trading_now(now)
        assess(rows, is_trading_now=is_trading, now=now, cfg=self.settings.data_quality)

        # 切源标记：本批次数据源 != 该 symbol_type 上一次数据源 -> 全部标 1
        last_src = quote_repo.get_last_source_for_symbol_type(session, symbol_type)
        switched = 0 if last_src is None else (1 if last_src != source else 0)
        if switched:
            for r in rows:
                r["source_switched"] = 1

        n = quote_repo.upsert_market_quotes(session, rows)
        self._record_success(
            session,
            source=source,
            symbol_type=symbol_type,
            now=now,
            note=f"rows={n};switched={switched};trading={is_trading}",
        )
        session.commit()
        self.log.info(
            "collect ok",
            extra={"symbol_type": symbol_type, "source": source, "count": n, "switched": switched},
        )
        return {
            "symbol_type": symbol_type,
            "status": "OK",
            "source": source,
            "count": n,
            "switched": switched,
        }

    def collect_index_snapshot(self, session: Session) -> Dict[str, Any]:
        return self._collect_snapshot(
            session, "INDEX", self.provider.get_index_snapshot,
            source_hint=self.settings.data_source.preferred,
        )

    def collect_etf_snapshot(self, session: Session) -> Dict[str, Any]:
        return self._collect_snapshot(
            session, "ETF", self.provider.get_etf_snapshot,
            source_hint=self.settings.data_source.preferred,
        )

    def collect_sector_ranking(self, session: Session, sector_type: str) -> Dict[str, Any]:
        return self._collect_snapshot(
            session, sector_type, lambda: self.provider.get_sector_ranking(sector_type),
            source_hint=self.settings.data_source.preferred,
        )

    # ---- 全市场宽度（每日累计） ----
    def collect_breadth(self, session: Session) -> Dict[str, Any]:
        now = self._now()
        source: Optional[str] = None
        try:
            df = self.provider.get_market_breadth_raw()
            if df is None or (hasattr(df, "empty") and df.empty):
                raise ValueError("breadth raw empty")
            source = df.attrs.get("__source") or "unknown"
            row = normalize.normalize_breadth(df, source, now)
        except Exception as e:  # noqa: BLE001
            self.log.error("breadth collect failed", extra={"err": str(e)})
            self._record_failure(session, source=source, symbol_type="BREADTH", now=now, err=str(e))
            session.commit()
            return {"symbol_type": "BREADTH", "status": "FAILED", "error": str(e)}

        quote_repo.upsert_breadth(session, row)
        self._record_success(
            session,
            source=source,
            symbol_type="BREADTH",
            now=now,
            note=f"rise={row['total_rise']};fall={row['total_fall']};limit_up={row['limit_up']}",
        )
        session.commit()
        self.log.info(
            "breadth ok",
            extra={"source": source, "rise": row["total_rise"], "fall": row["total_fall"]},
        )
        return {"symbol_type": "BREADTH", "status": "OK", "source": source, "row": row}

    # ---- 组合 ----
    def collect_market(self, session: Session) -> Dict[str, Any]:
        """盘中轻量采集：指数 + ETF + 行业 + 概念（不含全市场宽度，宽度单列任务）。"""
        return {
            "index": self.collect_index_snapshot(session),
            "etf": self.collect_etf_snapshot(session),
            "industry": self.collect_sector_ranking(session, "INDUSTRY"),
            "concept": self.collect_sector_ranking(session, "CONCEPT"),
        }

    def collect_all(self, session: Session) -> Dict[str, Any]:
        """完整采集（含宽度），用于收盘复盘/手动全量。"""
        out = self.collect_market(session)
        out["breadth"] = self.collect_breadth(session)
        return out
