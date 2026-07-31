"""worker 调度测试（P3，C23 调度模型）。

C23 调度：
- job_intraday_signal（interval 300s，is_trading_now 守卫 -> phase="live"，即「最新信号」盘中即时判断）。
- job_lunch_opinion（Cron 11:40，is_trading_day 守卫 -> phase="lunch"，午盘意见，可留历史）。
- job_pre_close_evaluate（Cron 14:50 -> pre_close）、job_post_close_evaluate（Cron 15:10 -> post_close）。
- 旧 job_intraday_evaluate（midday 整点）已移除，由 job_intraday_signal 取代。
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger


def test_build_scheduler_registers_c23_jobs():
    from app.config import get_settings
    from app.worker import build_scheduler

    sched = build_scheduler(get_settings())
    ids = {j.id for j in sched.get_jobs()}
    for jid in ("intraday_signal", "lunch_opinion", "pre_close_evaluate", "post_close_evaluate"):
        assert jid in ids, jid

    # 盘中实时信号：interval 300s（5 分钟）
    intraday = sched.get_job("intraday_signal")
    assert isinstance(intraday.trigger, IntervalTrigger)
    assert "seconds=300" in repr(intraday.trigger)

    # 午盘意见：Cron 11:40
    lunch = sched.get_job("lunch_opinion")
    assert isinstance(lunch.trigger, CronTrigger)
    assert "hour='11'" in repr(lunch.trigger)
    assert "minute='40'" in repr(lunch.trigger)

    # 收盘前/收盘后评估
    pre = sched.get_job("pre_close_evaluate")
    assert isinstance(pre.trigger, CronTrigger)
    assert "hour='14'" in repr(pre.trigger) and "minute='50'" in repr(pre.trigger)
    post = sched.get_job("post_close_evaluate")
    assert isinstance(post.trigger, CronTrigger)
    assert "hour='15'" in repr(post.trigger)


def _patch_run(monkeypatch, w, captured):
    """绕过写锁/真实 session：直接以 mock session 调用 pipeline，并记录 phase。"""
    def _wrap(session, settings, *, phase, as_of=None):
        captured["phase"] = phase
        return {"phase": phase}
    monkeypatch.setattr(w, "post_collection_evaluate", _wrap)
    monkeypatch.setattr(
        w, "run_write_job",
        lambda name, fn, engine, *a, **k: fn(MagicMock(), *a, **k),
    )


def test_job_intraday_signal_calls_pipeline_live(monkeypatch):
    import app.market_calendar as mc
    import app.worker as w

    captured = {}
    _patch_run(monkeypatch, w, captured)
    monkeypatch.setattr(mc, "is_trading_now", lambda: True)

    w.job_intraday_signal()
    assert captured.get("phase") == "live"


def test_job_intraday_signal_skips_non_trading_time(monkeypatch):
    import app.market_calendar as mc
    import app.worker as w

    called = {"n": 0}

    def _wrap(session, settings, *, phase, as_of=None):
        called["n"] += 1
        return {"phase": phase}
    monkeypatch.setattr(w, "post_collection_evaluate", _wrap)
    monkeypatch.setattr(
        w, "run_write_job",
        lambda name, fn, engine, *a, **k: fn(MagicMock(), *a, **k),
    )
    monkeypatch.setattr(mc, "is_trading_now", lambda: False)

    w.job_intraday_signal()
    assert called["n"] == 0


def test_job_lunch_opinion_calls_pipeline_lunch(monkeypatch):
    import app.market_calendar as mc
    import app.worker as w

    captured = {}
    _patch_run(monkeypatch, w, captured)
    monkeypatch.setattr(mc, "is_trading_day", lambda td: True)
    monkeypatch.setattr(mc, "trading_date_for", lambda: date(2025, 7, 18))

    w.job_lunch_opinion()
    assert captured.get("phase") == "lunch"
