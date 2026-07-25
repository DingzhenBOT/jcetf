"""库内 OHLC 异常重标（#67 已入库脏数据清理）。

在 512000 这类脏数据入库后、#67 修复上线前已写入 market_quote 的历史 BAR / 分时，其
data_quality_status 仍为 "OK"（旧采集链路不跑 OHLC 校验），会持续污染策略计算。本脚本对
库内所有 BAR/分时 重跑 OHLC 合理性校验，把失真的行改标 "ANOMALY"，使读路径过滤生效。

- 仅扫描 data_quality_status != "ANOMALY" 的行（已标过的不重复处理）。
- 幂等：再次运行无副作用（状态不变）。
- 默认 dry-run 打印将改标的行；加 --apply 才真正写入。

用法：
  python3.11 -m scripts.flag_ohlc_anomalies            # 仅预览
  python3.11 -m scripts.flag_ohlc_anomalies --apply     # 真正改标
  python3.11 -m scripts.flag_ohlc_anomalies --symbol 512000   # 只看某标的
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from app.config import get_settings
from app.data_quality.checker import _check_ohlc_consistency
from app.db import init_db, make_engine, session_scope
from app.db.models.market import MarketQuote
from app.logging_conf import get_logger, setup_logging


def _row_dict(q: MarketQuote) -> dict:
    return {
        "open": q.open,
        "high": q.high,
        "low": q.low,
        "close": q.close,
        "change_percent": q.change_percent,
        "main_net_inflow": q.main_net_inflow,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="库内 OHLC 异常重标（#67 清理）")
    ap.add_argument("--apply", action="store_true", help="真正写入；默认 dry-run 预览")
    ap.add_argument("--symbol", default=None, help="仅处理指定标的代码")
    args = ap.parse_args()

    settings = get_settings()
    setup_logging(settings)
    settings.ensure_dirs()
    log = get_logger("flag_ohlc_anomalies")
    cfg = settings.data_quality

    eng = make_engine(settings)
    init_db(eng, settings)

    total = 0
    flagged: list = []
    with session_scope(eng) as session:
        stmt = select(MarketQuote).where(
            MarketQuote.data_kind == "BAR",
            MarketQuote.data_quality_status != "ANOMALY",
        )
        if args.symbol:
            stmt = stmt.where(MarketQuote.symbol == args.symbol)
        rows = session.execute(stmt).scalars().all()
        total = len(rows)
        for q in rows:
            status = _check_ohlc_consistency(_row_dict(q), cfg)
            if status == "ANOMALY":
                flagged.append(q)
                if args.apply:
                    q.data_quality_status = "ANOMALY"

        if args.apply:
            session.commit()

    print(f"扫描 BAR/分时行数: {total}")
    print(f"将改标 ANOMALY: {len(flagged)}" + ("" if args.apply else "  (dry-run，未写入)"))
    for q in flagged[:50]:
        print(
            f"  {q.symbol_type:6s} {q.symbol:8s} {q.trading_date} "
            f"O={q.open} H={q.high} L={q.low} C={q.close}"
        )
    if len(flagged) > 50:
        print(f"  ... 其余 {len(flagged) - 50} 行省略")
    if args.apply and flagged:
        print("已写入。建议随后重跑 backfill 覆盖最新交易日坏数据："
              "python3.11 -m scripts.collect_once --backfill")
    return 0


if __name__ == "__main__":
    sys.exit(main())
