"""采集编排（DESIGN §3.1 / §8 / P2）。

Collector 把「provider 取数 -> normalize 映射 -> 质量评估 -> 切源标记 -> 幂等入库 -> 数据源状态」
串成可幂等重跑的任务。每个采集方法：成功记 OK + 重置连续失败；失败记 FAILED + 连续失败+1，
不抛到上层（由 worker.run_job 兜底日志）。业务代码只依赖 BaseDataProvider。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

import pandas as pd

from app.collector import normalize
from app.collector import sector_map
from app.config import Settings
from app.data_provider import akshare_adapter
from app.data_provider import eastmoney_web
from app.data_provider.base import BaseDataProvider
from app.data_quality.checker import assess
from app.logging_conf import get_logger
from app.market_calendar import is_trading_now, trading_date_for
from app.repository import mapping_repo, quote_repo


def _ymd(s: str) -> date:
    """'YYYYMMDD' -> date（backfill 的 start/end 格式）。"""
    return datetime.strptime(s, "%Y%m%d").date()


class Collector:
    def __init__(self, provider: BaseDataProvider, settings: Settings, gtimg_fetcher=None, us_index_fetcher=None, gtimg_intraday_fetcher=None, us_index_history_fetcher=None):
        self.provider = provider
        self.settings = settings
        self.gtimg_fetcher = gtimg_fetcher  # 腾讯财经实时行情拉取器（可注入；None=跳过）
        self.us_index_fetcher = us_index_fetcher  # 美股指数拉取器（gtimg_client.fetch_us_indices；None=跳过）
        self.gtimg_intraday_fetcher = gtimg_intraday_fetcher  # 腾讯财经当日分时拉取器（gtimg_client.fetch_intraday_minute；None=跳过，降级 sina）
        self.us_index_history_fetcher = us_index_history_fetcher  # 美股指数日线拉取器（akshare_adapter.get_us_index_history；None=直接用 akshare）
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

    def collect_realtime_gtimg(self, session: Session) -> Dict[str, Any]:
        """腾讯财经 qt.gtimg.cn 实时快照（ETF + 宽基指数），CVM 不封 IP 的可靠盘中源（C2）。

        作为 em/sina 之外的「附加实时源」：在 collect_market 末尾运行，写入时间戳最新 ->
        P1 盘中综合分优先采用 gtimg 的涨跌幅（get_latest_snapshot_change_map 跨源取 max(timestamp)）；
        em/sina 在 CVM 被 RST 时，gtimg 仍提供新鲜快照，P1 不再静默 no-op。
        gtimg_fetcher=None（测试/未注入）-> 直接跳过，零网络。
        任何异常：记 FAILED 状态 + 返回，绝不抛出（优雅降级）。
        """
        if self.gtimg_fetcher is None:
            return {"status": "skipped", "reason": "gtimg_fetcher not injected"}
        now = self._now()
        # 构造 (代码, kind) 列表：生效 ETF 映射 + 宽基指数
        etf_codes: set = set()
        for m in mapping_repo.get_active_mappings(session):
            if m.etf_code:
                etf_codes.add(str(m.etf_code))
        index_codes: set = set(self.settings.strategy.broad_index_codes)
        codes_with_kind = [(c, "etf") for c in etf_codes] + [(c, "index") for c in index_codes]

        try:
            if not codes_with_kind:
                raise ValueError("no active etf/index targets for gtimg")
            df = self.gtimg_fetcher(codes_with_kind)
            if df is None or (hasattr(df, "empty") and df.empty):
                raise ValueError("gtimg returned empty")
            # gtimg 单批混采 ETF+指数，normalize 需分类型（按代码集合拆回）
            etf_df = df[df["代码"].astype(str).isin(etf_codes)] if etf_codes else df.iloc[0:0]
            idx_df = df[df["代码"].astype(str).isin(index_codes)] if index_codes else df.iloc[0:0]
            rows: List[Dict[str, Any]] = []
            if not etf_df.empty:
                rows += normalize.normalize_etf_snapshot(etf_df, "gtimg", now)
            if not idx_df.empty:
                rows += normalize.normalize_index_snapshot(idx_df, "gtimg", now)
            if not rows:
                raise ValueError("no parseable gtimg rows after normalize")
        except Exception as e:  # noqa: BLE001 - 实时源失败：记状态，不抛出
            self.log.error("collect gtimg realtime failed", extra={"err": str(e)})
            self._record_failure(session, source="gtimg", symbol_type="ETF", now=now, err=str(e))
            self._record_failure(session, source="gtimg", symbol_type="INDEX", now=now, err=str(e))
            session.commit()
            return {"status": "FAILED", "error": str(e)}

        n = quote_repo.upsert_market_quotes(session, rows)
        note = f"rows={n};etf={len(etf_codes)};index={len(index_codes)}"
        self._record_success(session, source="gtimg", symbol_type="ETF", now=now, note=note)
        self._record_success(session, source="gtimg", symbol_type="INDEX", now=now, note=note)
        session.commit()
        self.log.info("gtimg realtime ok", extra={"rows": n})
        return {"status": "OK", "source": "gtimg", "count": n}

    def collect_us_indices(self, session: Session) -> Dict[str, Any]:
        """美股三大指数实时快照（道琼斯/纳斯达克/标普500，腾讯财经 usDJI/usIXIC/usINX）。

        作为首页「美股大盘」面板数据源，CVM 不封 IP 的可靠盘中/盘后源。
        存为独立 symbol_type=US_INDEX，与 A股 regime 计算隔离（engine 只读 INDEX 类型）。
        us_index_fetcher=None（测试/未注入）-> 直接跳过，零网络；任何异常记 FAILED 不抛出。
        """
        if self.us_index_fetcher is None:
            return {"status": "skipped", "reason": "us_index_fetcher not injected"}
        now = self._now()
        codes = list(self.settings.strategy.us_index_codes)
        if not codes:
            return {"status": "skipped", "reason": "no us_index_codes configured"}
        try:
            df = self.us_index_fetcher(codes)
            if df is None or (hasattr(df, "empty") and df.empty):
                raise ValueError("us index returned empty")
            rows = normalize.normalize_us_index_snapshot(df, "gtimg_us", now)
            if not rows:
                raise ValueError("no parseable us index rows after normalize")
        except Exception as e:  # noqa: BLE001 - 美股源失败：记状态，不抛出
            self.log.error("collect us indices failed", extra={"err": str(e)})
            self._record_failure(session, source="gtimg_us", symbol_type="US_INDEX", now=now, err=str(e))
            session.commit()
            return {"status": "FAILED", "error": str(e)}

        n = quote_repo.upsert_market_quotes(session, rows)
        self._record_success(session, source="gtimg_us", symbol_type="US_INDEX", now=now, note=f"rows={n}")
        session.commit()
        self.log.info("us indices ok", extra={"rows": n})
        return {"status": "OK", "source": "gtimg_us", "count": n}

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
        """盘中轻量采集：指数 + ETF + 行业 + 概念 + 腾讯财经实时快照（gtimg，CVM 可靠源）+ 美股指数。

        gtimg 在末尾运行，写入时间戳最新 -> P1 盘中综合分优先采用其涨跌幅；
        gtimg_fetcher 未注入时 collect_realtime_gtimg 直接跳过（零网络，不影响其余采集）。
        美股指数（usDJI/usIXIC/usINX）同样走腾讯财经，与 A股 regime 隔离（US_INDEX）。
        板块异动（westock-data）因 npx 较慢不进本高频采集，由独立低频任务 job_collect_sector_westock
        + backfill_history 负责落库（见 worker / backfill_history）。
        """
        res = {
            "index": self.collect_index_snapshot(session),
            "etf": self.collect_etf_snapshot(session),
            "industry": self.collect_sector_ranking(session, "INDUSTRY"),
            "concept": self.collect_sector_ranking(session, "CONCEPT"),
            "gtimg": self.collect_realtime_gtimg(session),
            "us_index": self.collect_us_indices(session),
        }
        return res

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
            self.log.error(
                f"collect bar failed: {symbol_type}/{symbol}: {e}",
                extra={"symbol_type": symbol_type, "symbol": symbol, "err": str(e)},
            )
            self._record_failure(session, source=source, symbol_type=symbol_type, now=now, err=str(e))
            session.commit()
            return {"symbol_type": symbol_type, "symbol": symbol, "status": "FAILED", "error": str(e)}

        # 历史 BAR 质量评估（#67）：OHLC 关系/跨度异常标记 ANOMALY。
        # 历史数据不校验时间新鲜度 -> is_trading_now=False（避免把旧 BAR 误标 STALE）。
        assess(rows, is_trading_now=False, now=now, cfg=self.settings.data_quality)

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

    def collect_us_index_history(self, session: Session, symbol: str, start: str, end: str) -> Dict[str, Any]:
        """美股指数日线 BAR（akshare index_us_stock_sina，sina 源，CVM 可达）。

        symbol 为系统美股代码（usDJI/usIXIC/usINX），内部映射到 akshare 代码（.DJI/.IXIC/.INX）。
        存为独立 symbol_type=US_INDEX，与 A股 INDEX 物理隔离；供 #109 跨市场影响分析。
        us_index_history_fetcher=None -> 直接用 akshare_adapter.get_us_index_history。
        """
        ak_symbol = akshare_adapter.US_INDEX_AKSHARE_SYMBOL.get(symbol, symbol)
        fetcher = self.us_index_history_fetcher or akshare_adapter.get_us_index_history
        return self._collect_bar(
            session, "US_INDEX", symbol,
            lambda: fetcher(ak_symbol, _ymd(start), _ymd(end)),
            normalize.normalize_us_index_bar,
            source_hint="sina_us",
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

    # ---- 东方财富 push2 直连（CVM 可达，替代被墙的 akshare em / ths）----
    def _sector_codes(self, session: Session, as_of: date) -> set:
        """板块 BK 代码全集：生效映射 related_sector_codes 并集 + settings.backfill.major_sector_codes。"""
        sector_codes: set = set()
        mappings = mapping_repo.get_active_mappings(session, as_of)
        for m in mappings:
            if m.related_sector_codes:
                sector_codes.update(m.related_sector_codes)
        sector_codes.update(self.settings.backfill.major_sector_codes)
        return sector_codes

    def collect_sector_history_web(self, session: Session, sector_codes: set, as_of: date) -> Dict[str, Any]:
        """板块日K历史（东方财富 push2 kline 主机，CVM 可达）。逐 BK 拉取后归一化入库。

        替代被墙的 akshare em(push2his RST) / ths(返回空)；源标签 'em_web'。
        任一板块失败记 FAILED 不抛出（部分板块失败不影响其余）。
        """
        now = self._now()
        end = as_of.strftime("%Y%m%d")
        bucket: Dict[str, int] = {"ok": 0, "failed": 0}
        for code in sorted(sector_codes):
            try:
                df = eastmoney_web.fetch_sector_kline(code, "19900101", end)
                source = df.attrs.get("__source") or "em_web"
                rows = normalize.normalize_sector_bar(df, source, code, now)
                if not rows:
                    raise ValueError("no parseable kline rows")
            except Exception as e:  # noqa: BLE001
                self.log.error(f"sector history web failed: {code}: {e}", extra={"symbol": code, "err": str(e)})
                self._record_failure(session, source="em_web", symbol_type="SECTOR", now=now, err=str(e))
                bucket["failed"] += 1
                continue
            assess(rows, is_trading_now=False, now=now, cfg=self.settings.data_quality)
            quote_repo.upsert_market_quotes(session, rows)
            self._record_success(session, source="em_web", symbol_type="SECTOR", now=now, note=f"rows={len(rows)}")
            bucket["ok"] += 1
        session.commit()
        return {"status": "done", **bucket}

    def collect_sector_fund_flow_web(self, session: Session, sector_codes: set, as_of: date) -> Dict[str, Any]:
        """板块资金流快照（东方财富 push2 clist，CVM 可达）。一次性拉全板块，按 BK 过滤入库。

        仅含当日资金流（clist 不提供历史区间）；源标签 'em_web'。替代被墙的 akshare em / ths。
        """
        now = self._now()
        tdate = as_of
        bucket: Dict[str, int] = {"ok": 0, "failed": 0}
        try:
            df = eastmoney_web.fetch_sector_fund_flow_snapshot(trade_date=tdate)
            source = df.attrs.get("__source") or "em_web"
        except Exception as e:  # noqa: BLE001
            self.log.error("sector fund flow web failed", extra={"err": str(e)})
            self._record_failure(session, source="em_web", symbol_type="SECTOR", now=now, err=str(e))
            session.commit()
            return {"status": "FAILED", "error": str(e)}
        for code in sorted(sector_codes):
            row = df[df["bk_code"] == code]
            if row.empty:
                self._record_failure(session, source=source, symbol_type="SECTOR", now=now, err=f"bk {code} not in clist")
                bucket["failed"] += 1
                continue
            try:
                rows = normalize.normalize_sector_fund_flow_bar(row, source, code, now)
                if not rows:
                    raise ValueError("no parseable fund flow rows")
            except Exception as e:  # noqa: BLE001
                self.log.error(f"sector fund flow web normalize failed: {code}: {e}", extra={"symbol": code, "err": str(e)})
                self._record_failure(session, source=source, symbol_type="SECTOR", now=now, err=str(e))
                bucket["failed"] += 1
                continue
            assess(rows, is_trading_now=False, now=now, cfg=self.settings.data_quality)
            quote_repo.upsert_market_quotes(session, rows)
            self._record_success(session, source=source, symbol_type="SECTOR", now=now, note=f"rows={len(rows)}")
            bucket["ok"] += 1
        session.commit()
        return {"status": "done", **bucket}

    def collect_sector_from_westock(self, session: Session, sector_codes: set, as_of: date) -> Dict[str, Any]:
        """板块异动（腾讯自选股 westock-data，CVM 稳定）落库 SECTOR BAR。

        westock `sector ranking` 返回「行业/概念涨幅 + 资金流入 TOP 榜」（异动榜，非全量）：
        - industry / concept 表：name + changePct（涨跌幅）
        - fund_flow 表：name + changePct + mainNetInflow（主力净流入）
        按 sector_map.resolve_sector_bk 把板块名解析为跟踪 BK（仅当日活跃出现在榜上的板块入库），
        合并为每个 BK 一行（change_percent + main_net_inflow），normalize_sector_fund_flow_bar
        入库（source='westock'，data_kind=BAR）。引擎对非活跃板块优雅降级（D4）。
        注：westock 不提供板块指数收盘价，close 留空；引擎 evaluate_sector_trend 在 close 缺失时
        改用 change_percent 做动量（见 sector_engine.engine）。
        westock 失败（npx 超时/不可用）记 FAILED 不抛。
        """
        now = self._now()
        bucket: Dict[str, int] = {"ok": 0, "failed": 0}
        try:
            from app.services import external_data

            mov = external_data.collect_sector_movement()
            if not mov.get("available"):
                raise RuntimeError("westock sector movement unavailable: " + str(mov.get("reason", "")))
        except Exception as e:  # noqa: BLE001
            self.log.error("sector westock failed", extra={"err": str(e)})
            self._record_failure(session, source="westock", symbol_type="SECTOR", now=now, err=str(e))
            session.commit()
            return {"status": "FAILED", "error": str(e)}

        merged: Dict[str, dict] = {}

        def _merge(name, change_pct, main_inflow=None):
            bk = sector_map.resolve_sector_bk(name, sector_codes)
            if bk is None:
                self.log.debug("sector westock name not mapped", extra={"name": name})
                return
            d = merged.setdefault(bk, {})
            if change_pct is not None:
                d["change_percent"] = change_pct
            if main_inflow is not None:
                d["main_net_inflow"] = main_inflow

        for r in list(mov.get("industry", [])) + list(mov.get("concept", [])):
            _merge(r.get("name"), r.get("changePct"))
        for r in mov.get("fund_flow", []):
            _merge(r.get("name"), r.get("changePct"), r.get("mainNetInflow"))

        for bk, vals in merged.items():
            try:
                df = pd.DataFrame([{
                    "日期": as_of.isoformat(),
                    "主力净流入-净额": vals.get("main_net_inflow"),
                    "涨跌幅": vals.get("change_percent"),
                    "收盘": None,
                    "成交额": None,
                    "超大单净流入-净额": None,
                }])
                rows = normalize.normalize_sector_fund_flow_bar(df, "westock", bk, now)
                if not rows:
                    raise ValueError("no parseable westock sector rows")
            except Exception as e:  # noqa: BLE001
                self.log.error(f"sector westock normalize failed: {bk}: {e}", extra={"symbol": bk, "err": str(e)})
                self._record_failure(session, source="westock", symbol_type="SECTOR", now=now, err=str(e))
                bucket["failed"] += 1
                continue
            assess(rows, is_trading_now=False, now=now, cfg=self.settings.data_quality)
            quote_repo.upsert_market_quotes(session, rows)
            self._record_success(session, source="westock", symbol_type="SECTOR", now=now, note=f"rows={len(rows)}")
            bucket["ok"] += 1
        session.commit()
        return {"status": "done", **bucket}

    def collect_intraday_minute(self, session: Session) -> Dict[str, Any]:
        """盘中 1 分钟分时采集：遍历 ETF(生效映射) + 宽基指数。

        源优先级（C19 修复 sina 在 CVM 返回两周前旧数据）：
        - 腾讯财经 web.ifzq.gtimg.cn（gtimg_intraday_fetcher 注入）：返回当日分时，CVM 不封 IP -> 优先。
        - sina stock_zh_a_minute：腾讯失败时降级兜底。
        - 单次批量；每个标的失败记 FAILED 不抛出（源偶发超时属非致命）。
        - 幂等：同一分钟 timestamp 覆盖更新（upsert）。
        """
        now = self._now()
        tdate = trading_date_for()
        # 刷掉前一交易日分时：只保留当前交易日的 1m 数据（避免多日累积、x 轴只画当日）
        try:
            purged = quote_repo.purge_intraday_before(session, tdate)
            if purged:
                self.log.info("intraday purge old days", extra={"keep_date": tdate.isoformat(), "purged": purged})
        except Exception as e:  # noqa: BLE001
            self.log.warning("intraday purge failed (non-fatal)", extra={"err": str(e)})
        etf_codes = [m.etf_code for m in mapping_repo.get_active_mappings(session) if self._is_on_exchange(m)]
        targets = [("ETF", c) for c in etf_codes] + [
            ("INDEX", c) for c in self.settings.strategy.broad_index_codes
        ]
        bucket: Dict[str, int] = {"ok": 0, "failed": 0}
        for symbol_type, code in targets:
            rows = None
            tried_source = None
            # 优先腾讯分时（返回当日、CVM 可用）
            if self.gtimg_intraday_fetcher is not None:
                try:
                    df = self.gtimg_intraday_fetcher(code, symbol_type)
                    tried_source = df.attrs.get("__source") or "gtimg"
                    rows = normalize.normalize_intraday_minute(df, tried_source, symbol_type, code, tdate, now)
                except Exception as e:  # noqa: BLE001
                    self.log.warning(
                        "intraday gtimg failed, fallback sina",
                        extra={"symbol_type": symbol_type, "symbol": code, "err": str(e)},
                    )
                    tried_source = None
                    rows = None
            # 降级 sina
            if rows is None:
                try:
                    df = self.provider.get_intraday_minute(symbol_type, code)
                    tried_source = df.attrs.get("__source") or "sina"
                    rows = normalize.normalize_intraday_minute(df, tried_source, symbol_type, code, tdate, now)
                except Exception as e:  # noqa: BLE001
                    self.log.error(
                        f"collect intraday failed: {symbol_type}/{code}: {e}",
                        extra={"symbol_type": symbol_type, "symbol": code, "err": str(e)},
                    )
                    self._record_failure(session, source=(tried_source or "sina"), symbol_type=symbol_type, now=now, err=str(e))
                    bucket["failed"] += 1
                    continue
            if not rows:
                self._record_failure(session, source=(tried_source or "sina"), symbol_type=symbol_type, now=now, err="no parseable intraday rows")
                bucket["failed"] += 1
                continue
            # 盘中分时质量评估（#67）：OHLC 关系/跨度异常标记 ANOMALY；不校验时间新鲜度。
            assess(rows, is_trading_now=False, now=now, cfg=self.settings.data_quality)
            quote_repo.upsert_market_quotes(session, rows)
            self._record_success(session, source=tried_source, symbol_type=symbol_type, now=now, note=f"rows={len(rows)}")
            bucket["ok"] += 1
        session.commit()
        return {"status": "done", **bucket}

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
        # batch 采集方法（板块 westock/em_web、分时）返回 status="done" 并自带 ok/failed 桶；
        # 单标的采集（ETF/指数历史）返回 "OK"/"FAILED"。两者均按成功计（除非显式 FAILED）。
        if res.get("status") == "FAILED":
            bucket["failed"] += 1
        elif "ok" in res and "failed" in res:
            # batch 方法：直接合并内部 ok/failed 桶，避免把「部分成功」误判为整批失败
            bucket["ok"] += int(res.get("ok", 0))
            bucket["failed"] += int(res.get("failed", 0))
        else:
            bucket["ok"] += 1

    @staticmethod
    def _is_on_exchange(m) -> bool:
        """场内 ETF（走场内行情管道）；场外联接基金走盈米/开放式基金源，不采场内历史/分时。

        场外 fund_etf_hist_em / sina 均无数据 -> 采则必然 FAILED；其行情由 盈米 CLI / 开放式基金源提供。
        """
        return (m.listing or "场内") != "场外"

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
            "us_index": {"ok": 0, "failed": 0},
            "sector": {"ok": 0, "failed": 0},
            "sector_flow": {"ok": 0, "failed": 0},
        }

        # ETF（来自生效映射；场外联接基金走盈米/开放式基金源，不采场内历史）
        mappings = mapping_repo.get_active_mappings(session, as_of)
        for m in mappings:
            if not self._is_on_exchange(m):
                continue
            start = self._backfill_start(session, "ETF", m.etf_code, as_of, lookback_days)
            if start is None:
                continue
            r = self.collect_etf_history(session, m.etf_code, start, end)
            self._tally(result["etf"], r)

        # 宽基指数（market_regime 基准）+ 每个 ETF 的跟踪指数（related_index_code，作为 etf_rs 的 RS 基准）。
        # 合并去重后回填，保证 etf_rs 能取到基准日线，避免 etf_rs_missing 误判。
        index_codes = set(self.settings.strategy.broad_index_codes)
        for m in mappings:
            if m.related_index_code:
                index_codes.add(m.related_index_code)
        for code in sorted(index_codes):
            start = self._backfill_start(session, "INDEX", code, as_of, lookback_days)
            if start is None:
                continue
            r = self.collect_index_history(session, code, start, end)
            self._tally(result["index"], r)

        # 美股指数日线（usDJI/usIXIC/usINX，akshare sina 源）：供 #109「美股对A股影响」分析。
        # 与 A股 INDEX 物理隔离（US_INDEX 类型）；每日 16:30 回填增量维护。
        for code in sorted(self.settings.strategy.us_index_codes):
            start = self._backfill_start(session, "US_INDEX", code, as_of, lookback_days)
            if start is None:
                continue
            r = self.collect_us_index_history(session, code, start, end)
            self._tally(result["us_index"], r)

        # 板块（行业/概念 BK 代码并集 + 额外 major）
        # 主源：腾讯自选股 westock-data 板块异动榜（CVM 稳定，返回板块名+涨跌幅+主力净流入）。
        # 仅当日活跃出现在榜上的板块入库；未出现板块由引擎优雅降级（D4）。
        sector_codes = self._sector_codes(session, as_of)
        r = self.collect_sector_from_westock(session, sector_codes, as_of)
        self._tally(result["sector"], r)

        # 备选：东方财富 push2 直连（eastmoney_web）。CVM 上 push2 被 RST 拦截，默认关闭，
        # 避免回填中 10 个板块 kline 超时拖慢；仅在与东财直连可达的部署开 settings.backfill.use_em_web。
        if self.settings.backfill.use_em_web:
            r = self.collect_sector_history_web(session, sector_codes, as_of)
            self._tally(result["sector"], r)
            r2 = self.collect_sector_fund_flow_web(session, sector_codes, as_of)
            self._tally(result["sector_flow"], r2)

        self.log.info("backfill done", extra=result)
        return result
