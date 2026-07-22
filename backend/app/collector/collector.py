"""采集编排（DESIGN §3.1 / §8 / P2）。

Collector 把「provider 取数 -> normalize 映射 -> 质量评估 -> 切源标记 -> 幂等入库 -> 数据源状态」
串成可幂等重跑的任务。每个采集方法：成功记 OK + 重置连续失败；失败记 FAILED + 连续失败+1，
不抛到上层（由 worker.run_job 兜底日志）。业务代码只依赖 BaseDataProvider。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

from app.collector import normalize
from app.config import Settings
from app.data_provider.base import BaseDataProvider
from app.data_quality.checker import assess
from app.logging_conf import get_logger
from app.market_calendar import is_trading_now, trading_date_for
from app.repository import mapping_repo, quote_repo


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
            "codes": [r["symbol"] for r in rows],
        }

    def collect_index_snapshot(self, session: Session) -> Dict[str, Any]:
        """指数快照：主源（em）全量批次优先；对 broad_index_codes 中主源未覆盖的指数
        （如 em 不含深市 399001/399006）用 sina 等兜底源按代码补齐 SNAPSHOT。

        主路径沿用 _collect_snapshot（保留切源标记/数据源状态/质量评估）；补齐按「主批次
        实际覆盖的代码」判断缺失——每个采集周期都对缺失指数重新拉取，保证跨天新鲜度
        （不依赖历史 SNAPSHOT 是否存在）。
        """
        primary = self._collect_snapshot(
            session, "INDEX", self.provider.get_index_snapshot,
            source_hint=self.settings.data_source.preferred,
        )
        covered = set(primary.get("codes") or [])
        self._fill_index_snapshot_gaps(session, exclude_codes=covered)
        return primary

    def _fill_index_snapshot_gaps(self, session: Session, exclude_codes: set) -> None:
        """补齐 broad_index_codes 中主批次未覆盖的指数（em 不含深市指数时触发）。

        每兜底源只拉一次整批，按归一副代码查表填充所有缺失代码，避免重复网络调用。
        每个周期对缺失代码重新拉取，保证新鲜度。
        """
        filler = getattr(self.provider, "get_index_snapshot_from", None)
        if filler is None:
            return
        sources = getattr(self.provider, "index_spot_sources", lambda: [])()
        preferred = self.settings.data_source.preferred
        missing = [code for code in self.settings.strategy.broad_index_codes if code not in exclude_codes]
        if not missing:
            return
        now = self._now()
        for src in sources:
            if src == preferred or not missing:
                continue  # 主批次已采，或已补齐完毕
            try:
                df = filler(src)
            except Exception as e:  # noqa: BLE001
                self.log.warning("index snapshot gap-fill source failed", extra={"src": src, "err": str(e)})
                continue
            if df is None or (hasattr(df, "empty") and df.empty):
                continue
            by_code = {r["symbol"]: r for r in normalize.normalize_index_snapshot(df, src, now)}
            filled = False
            for code in list(missing):
                row = by_code.get(code)
                if row is not None:
                    quote_repo.upsert_market_quotes(session, [row])
                    self.log.info("index snapshot gap-filled", extra={"code": code, "src": src})
                    missing.remove(code)
                    filled = True
            if filled:
                session.commit()

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

    # ---- 历史 BAR 采集（ETF / 指数 / 板块趋势 / 板块资金流） ----
    def _collect_bar(
        self,
        session: Session,
        symbol_type: str,
        symbol: str,
        fetch_fn: Callable[[], Any],
        normalize_fn: Callable[[Any, str, str, datetime], List[Dict[str, Any]]],
        *,
        source_hint: Optional[str] = None,
    ) -> Dict[str, Any]:
        """采集单标的历史 BAR；失败记 FAILED 不抛出（em-only 板块历史在沙箱/用户服务器均会失败，须非致命）。"""
        now = self._now()
        source: Optional[str] = source_hint
        try:
            df = fetch_fn()
            if df is None or (hasattr(df, "empty") and df.empty):
                raise ValueError("data source returned empty")
            source = df.attrs.get("__source") or source_hint or "unknown"
            rows = normalize_fn(df, source, symbol, now)
            if not rows:
                raise ValueError("no parseable rows after normalize")
        except Exception as e:  # noqa: BLE001 - 历史采集失败：记状态，不抛出，继续回填其他标的
            self.log.error("collect bar failed", extra={"symbol_type": symbol_type, "symbol": symbol, "err": str(e)})
            self._record_failure(session, source=source, symbol_type=symbol_type, now=now, err=str(e))
            session.commit()
            return {"symbol_type": symbol_type, "symbol": symbol, "status": "FAILED", "error": str(e)}

        n = quote_repo.upsert_market_quotes(session, rows)
        self._record_success(
            session, source=source, symbol_type=symbol_type, now=now, note=f"rows={n}"
        )
        session.commit()
        return {
            "symbol_type": symbol_type,
            "symbol": symbol,
            "status": "OK",
            "source": source,
            "count": n,
        }

    def collect_etf_history(self, session: Session, symbol: str, start: str, end: str) -> Dict[str, Any]:
        return self._collect_bar(
            session, "ETF", symbol,
            lambda: self.provider.get_etf_history(symbol, start, end),
            normalize.normalize_etf_bar,
            source_hint=self.settings.data_source.preferred,
        )

    def collect_index_history(self, session: Session, symbol: str, start: str, end: str) -> Dict[str, Any]:
        return self._collect_bar(
            session, "INDEX", symbol,
            lambda: self.provider.get_index_history(symbol, start, end),
            normalize.normalize_index_bar,
            source_hint=self.settings.data_source.preferred,
        )

    def collect_sector_history(self, session: Session, symbol: str, start: str, end: str) -> Dict[str, Any]:
        return self._collect_bar(
            session, "SECTOR", symbol,
            lambda: self.provider.get_sector_history(symbol, start, end),
            normalize.normalize_sector_bar,
            source_hint=self.settings.data_source.preferred,
        )

    def collect_sector_fund_flow_history(self, session: Session, symbol: str, start: str, end: str) -> Dict[str, Any]:
        return self._collect_bar(
            session, "SECTOR", symbol,
            lambda: self.provider.get_sector_fund_flow_history(symbol, start, end),
            normalize.normalize_sector_fund_flow_bar,
            source_hint=self.settings.data_source.preferred,
        )

    # ---- 增量回填编排（bounded：符号列表 + lookback_days；增量按 max(timestamp)+1） ----
    def _backfill_start(self, session: Session, symbol_type: str, symbol: str, as_of: date, lookback_days: int) -> Optional[str]:
        """返回该标的本次应拉取的 start（YYYYMMDD）；已齐或无需拉取返回 None。"""
        max_ts = quote_repo.get_max_bar_timestamp(session, symbol_type, symbol)
        if max_ts is not None:
            start = max_ts.date() + timedelta(days=1)
        else:
            start = as_of - timedelta(days=lookback_days)
        if start > as_of:
            return None
        return start.strftime("%Y%m%d")

    @staticmethod
    def _tally(bucket: Dict[str, int], res: Dict[str, Any]) -> None:
        if res.get("status") == "OK":
            bucket["ok"] += 1
        else:
            bucket["failed"] += 1

    def backfill_history(
        self,
        session: Session,
        *,
        as_of: Optional[date] = None,
        lookback_days: Optional[int] = None,
    ) -> Dict[str, Any]:
        """回填所有相关标的的历史 BAR（指数/ETF/板块趋势/板块资金流）。

        - as_of 默认北京时间今日；end = as_of。
        - ETF 列表来自生效映射；宽基指数来自 settings.strategy.broad_index_codes；
          板块来自映射 related_sector_codes 并集 + settings.backfill.major_sector_codes。
        - 每个标的按 max(timestamp) 增量；em-only 板块历史失败被记为 FAILED 并继续（D4 优雅降级）。
        """
        if as_of is None:
            as_of = trading_date_for()
        if lookback_days is None:
            lookback_days = self.settings.backfill.lookback_days
        end = as_of.strftime("%Y%m%d")

        result: Dict[str, Any] = {
            "as_of": as_of.isoformat(),
            "etf": {"ok": 0, "failed": 0},
            "index": {"ok": 0, "failed": 0},
            "sector": {"ok": 0, "failed": 0},
            "sector_flow": {"ok": 0, "failed": 0},
        }

        # ETF（来自生效映射）
        mappings = mapping_repo.get_active_mappings(session, as_of)
        for m in mappings:
            start = self._backfill_start(session, "ETF", m.etf_code, as_of, lookback_days)
            if start is None:
                continue
            r = self.collect_etf_history(session, m.etf_code, start, end)
            self._tally(result["etf"], r)

        # 宽基指数（market_regime 基准）
        for code in self.settings.strategy.broad_index_codes:
            start = self._backfill_start(session, "INDEX", code, as_of, lookback_days)
            if start is None:
                continue
            r = self.collect_index_history(session, code, start, end)
            self._tally(result["index"], r)

        # 板块（行业/概念 BK 代码并集 + 额外 major）
        sector_codes: set = set()
        for m in mappings:
            if m.related_sector_codes:
                sector_codes.update(m.related_sector_codes)
        sector_codes.update(self.settings.backfill.major_sector_codes)
        for code in sorted(sector_codes):
            start = self._backfill_start(session, "SECTOR", code, as_of, lookback_days)
            if start is None:
                continue
            r = self.collect_sector_history(session, code, start, end)
            self._tally(result["sector"], r)
            r2 = self.collect_sector_fund_flow_history(session, code, start, end)
            self._tally(result["sector_flow"], r2)

        self.log.info("backfill done", extra=result)
        return result
