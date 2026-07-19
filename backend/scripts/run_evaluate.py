"""一次性评估（P3 自测 / 手动补算）。

不启动调度器，直接跑 post_collection_evaluate 并写入 SQLite，打印汇总。
可选先回填历史 BAR（需联网；em-only 板块历史在沙箱/用户服务器会失败，属预期，非致命）。

用法：
  python3.11 -m scripts.run_evaluate --phase post_close
  python3.11 -m scripts.run_evaluate --phase pre_close
  python3.11 -m scripts.run_evaluate --phase post_close --backfill   # 先回填历史 BAR
"""
from __future__ import annotations

import argparse
import sys

from app.config import get_settings
from app.data_provider import build_provider
from app.db import init_db, make_engine, session_scope
from app.collector.collector import Collector
from app.evaluation.pipeline import post_collection_evaluate
from app.logging_conf import get_logger, setup_logging


def main() -> int:
    ap = argparse.ArgumentParser(description="一次性评估（P3 自测）")
    ap.add_argument("--phase", default="post_close", choices=["pre_market", "midday", "pre_close", "post_close"])
    ap.add_argument("--backfill", action="store_true", help="评估前先回填历史 BAR（需联网）")
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    settings = get_settings(config_path=args.config)
    setup_logging(settings)
    settings.ensure_dirs()
    log = get_logger("run_evaluate")

    eng = make_engine(settings)
    init_db(eng, settings)

    if args.backfill:
        collector = Collector(build_provider(settings), settings)
        with session_scope(eng) as session:
            bf = collector.backfill_history(session)
        log.info("backfill summary", extra=bf)
        print("backfill:", bf)

    with session_scope(eng) as session:
        res = post_collection_evaluate(session, settings, phase=args.phase)
    eng.dispose()

    log.info("evaluate summary", extra=res)
    print(
        f"phase={res['phase']} as_of={res['as_of']} version={res['strategy_version']}\n"
        f"  signals  +{res['signals_written']} ~{res['signals_updated']}\n"
        f"  opinions +{res['opinions_written']} ~{res['opinions_updated']}\n"
        f"  errors={res['errors']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
