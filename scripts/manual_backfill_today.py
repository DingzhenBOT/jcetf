#!/usr/bin/env python3
"""手动补采今日数据 + 收盘复盘（CVM 运维补救用）。

场景：worker 因交易日历误判（C19 前旧进程）全天 skip 采集，导致今日数据缺失；
或盘中/收盘窗口因重启错过，需手动补一次。

逻辑复刻 worker.job_post_close + worker.job_post_close_evaluate：
- collect_all：指数/ETF/行业/概念快照 + gtimg 实时 + 美股 + 市场宽度
- post_close_evaluate：基于今日数据生成每支 ETF 的收盘后信号 + 复盘意见

注意：本脚本直接 import app.worker 复用其内部 _engine/_collector（含 gtimg/美股注入），
不在 worker 进程内运行，是一次性补救。无需 systemd。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app.worker as worker  # noqa: E402


def main() -> None:
    print("=== [1/4] 完整采集 collect_all (post_close: 指数/ETF/板块快照 + gtimg + 美股 + 宽度) ===")
    worker.job_post_close()

    print("=== [2/4] 日K历史回填 backfill_history (ETF/指数/板块 BAR) ===")
    worker.job_backfill_history()

    print("=== [3/4] 补今日分时（绕过盘中 is_trading_now 守卫；sina 分时接口返回当日完整数据） ===")
    from app.db import session_scope

    with session_scope(worker._engine()) as session:
        worker._collector().collect_intraday_minute(session)

    print("=== [4/4] 收盘后评估 post_close_evaluate ===")
    worker.job_post_close_evaluate()

    print("=== 手动补采完成。检查数据库 SNAPSHOT/BAR/INTRADAY_MINUTE 时间戳是否已更新到今日 ===")


if __name__ == "__main__":
    main()
