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
from app.data_provider import build_provider, gtimg_client
from app.db import init_db, make_engine, session_scope
from app.db.lock import db_writer_lock
from app.market_calendar import is_trading_now
from app.collector.collector import Collector
from app.evaluation.pipeline import post_collection_evaluate
from app.logging_conf import get_logger, setup_logging


def main() -> int:
    ap = argparse.ArgumentParser(description="一次性评估（P3 自测）")
    ap.add_argument("--phase", default="post_close", choices=["pre_market", "midday", "pre_close", "post_close"])
    ap.add_argument("--backfill", action="store_true", help="评估前先回填历史 BAR（需联网）")
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    # 盘中守卫（仅拦截「收盘阶段评估」；回填历史 BAR 不受限）：
    # 无 --backfill 且为收盘阶段且盘中 -> 直接拒绝，避免无谓建库。
    if (not args.backfill) and args.phase in ("post_close", "pre_close") and is_trading_now():
        print(
            f"[中止] phase={args.phase} 不能在盘中运行（当前为交易时段）。\n"
            "收盘复盘/收盘前评估须在 15:00 之后执行，否则会基于不完整的盘中数据生成复盘记录。\n"
            "请于收盘后（北京时间 15:10 之后）再运行，或改用 --phase midday / pre_market。"
        )
        return 2

    settings = get_settings(config_path=args.config)
    setup_logging(settings)
    settings.ensure_dirs()
    log = get_logger("run_evaluate")

    eng = make_engine(settings)
    init_db(eng, settings)

    # 跨进程写锁（#111）：手动评估/回填与 worker 串行，彻底避免抢 SQLite 写锁导致
    # database is locked。blocking=True 阻塞等待 worker 写完当前批次（通常亚秒~数秒）；
    # 超时(默认120s)说明另一 run_evaluate 正在跑，友好退出。
    try:
        with db_writer_lock(settings, blocking=True, timeout=120) as acquired:
            if not acquired:
                print("[错误] 无法获取 SQLite 写锁（可能被另一 run_evaluate 占用），请稍后重试。")
                return 1
            log.info("db_writer_lock acquired (manual run_evaluate); worker 写任务将暂时让行")

            if args.backfill:
                collector = Collector(build_provider(settings), settings, gtimg_fetcher=gtimg_client.fetch_realtime)
                with session_scope(eng) as session:
                    bf = collector.backfill_history(session)
                log.info("backfill summary", extra=bf)
                print("backfill:", bf)

            # 有 --backfill 时盘中已跑完回填；收盘阶段评估仍须等收盘后，避免生成盘中复盘。
            if args.phase in ("post_close", "pre_close") and is_trading_now():
                print(
                    f"[中止] phase={args.phase} 评估不能在盘中运行（当前为交易时段）。\n"
                    "回填已完成（如有 --backfill）；收盘复盘/收盘前评估请于收盘后（北京时间 15:10 之后）再运行，"
                    "或由 worker 在 15:10 自动生成。"
                )
                return 2

            with session_scope(eng) as session:
                res = post_collection_evaluate(session, settings, phase=args.phase)
    except TimeoutError as e:
        log.error("db_writer_lock timeout: %s", e)
        print(f"[错误] {e}")
        return 1

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
