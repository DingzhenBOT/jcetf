"""worker 调度测试：盘中每小时 midday 评估 + 收盘前 14:50（替代 14:59）。

- build_scheduler 注册 intraday_evaluate（hour=10,11,13,14 / minute=0）与 pre_close_evaluate（14:50）。
- job_intraday_evaluate 在非交易日跳过、交易日调用 post_collection_evaluate(phase="midday")。
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from unittest.mock import MagicMock

import pytest


@contextmanager
def _fake_scope(eng):
    yield MagicMock()


def test_build_scheduler_intraday_and_preclose_times():
    from app.config import get_settings
    from app.worker import build_scheduler

    sched = build_scheduler(get_settings())
    jobs = {j.id: j for j in sched.get_jobs()}
    assert "intraday_evaluate" in jobs
    assert "pre_close_evaluate" in jobs

    intraday = repr(jobs["intraday_evaluate"].trigger)
    assert "hour='10,11,13,14'" in intraday
    assert "minute='0'" in intraday

    preclose = repr(jobs["pre_close_evaluate"].trigger)
    assert "hour='14'" in preclose
    assert "minute='50'" in preclose


def test_job_intraday_evaluate_calls_pipeline_midday(monkeypatch):
    import app.market_calendar as mc
    import app.worker as w

    captured = {}

    def fake_pipeline(session, settings, *, phase, as_of=None):
        captured["phase"] = phase
        return {"phase": phase}

    monkeypatch.setattr(w, "post_collection_evaluate", fake_pipeline)
    monkeypatch.setattr(w, "session_scope", _fake_scope)
    monkeypatch.setattr(w, "_engine", lambda: None)
    monkeypatch.setattr(mc, "is_trading_day", lambda td: True)
    monkeypatch.setattr(mc, "trading_date_for", lambda: date(2025, 7, 18))

    w.job_intraday_evaluate()
    assert captured.get("phase") == "midday"


def test_job_intraday_evaluate_skips_non_trading_day(monkeypatch):
    import app.market_calendar as mc
    import app.worker as w

    called = {"n": 0}

    def fake_pipeline(session, settings, *, phase, as_of=None):
        called["n"] += 1
        return {"phase": phase}

    monkeypatch.setattr(w, "post_collection_evaluate", fake_pipeline)
    monkeypatch.setattr(w, "session_scope", _fake_scope)
    monkeypatch.setattr(w, "_engine", lambda: None)
    monkeypatch.setattr(mc, "is_trading_day", lambda td: False)

    w.job_intraday_evaluate()
    assert called["n"] == 0
