"""一次性采集（自测 / 手动补数用，P2）。

不启动调度器，直接对当前配置的数据源跑一次「完整采集」并写入 SQLite，打印汇总。
适合：服务器上验证采集链路 / 非交易时段手动补数。

用法：
  python3.11 -m scripts.collect_once            # 完整采集（含 breadth）
  python3.11 -m scripts.collect_once --market   # 仅盘中四类快照（不含 breadth）
  python3.11 -m scripts.collect_once --intraday # 仅盘中 1 分钟分时（ETF + 宽基指数）
"""
from __future__ import annotations

import argparse
import sys

from app.config import get_settings
from app.data_provider import build_provider
from app.db import init_db, make_engine, session_scope
from app.collector.collector import Collector
from app.logging_conf import get_logger, setup_logging


def main() -> int:
    ap = argparse.ArgumentParser(description="一次性采集（P2 自测）")
    ap.add_argument("--market", action="store_true", help="仅盘中四类快照，不含 breadth")
    ap.add_argument("--backfill", action="store_true", help="仅回填历史 BAR（P3，需联网）")
    ap.add_argument("--intraday", action="store_true", help="仅盘中 1 分钟分时（ETF + 宽基指数）")
    args = ap.parse_args()

    settings = get_settings()
    setup_logging(settings)
    settings.ensure_dirs()
    log = get_logger("collect_once")

    eng = make_engine(settings)
    init_db(eng, settings)
    collector = Collector(build_provider(settings), settings)

    if args.backfill:
        with session_scope(eng) as session:
            res = collector.backfill_history(session)
        print("backfill:", res)
        print("done. 数据已写入", settings.paths.sqlite_path_abs)
        return 0

    if args.intraday:
        with session_scope(eng) as session:
            res = collector.collect_intraday_minute(session)
        print("intraday:", res)
        print("done. 数据已写入", settings.paths.sqlite_path_abs)
        return 0

    with session_scope(eng) as session:
        if args.market:
            res = collector.collect_market(session)
        else:
            res = collector.collect_all(session)

    if args.market:
        for k in ("index", "etf", "industry", "concept"):
            r = res[k]
            log.info("result", extra={"kind": k, "status": r["status"], "count": r.get("count"), "source": r.get("source")})
            print(f"  {k:9s} {r['status']:7s} count={r.get('count')} source={r.get('source')}")
    else:
        for k, r in res.items():
            if k == "breadth":
                print(f"  breadth   {r['status']:7s} rise={r.get('row', {}).get('total_rise')} fall={r.get('row', {}).get('total_fall')}")
            else:
                print(f"  {k:9s} {r['status']:7s} count={r.get('count')} source={r.get('source')}")
    print("done. 数据已写入", settings.paths.sqlite_path_abs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
