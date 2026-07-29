"""美股对A股影响分析单测（#109）。

- compute_us_impact：注入已知「A股次日收益 = 0.5 × 美股当日收益」的合成序列，
  断言近期相关≈1、β≈0.5、available=True、近期传导明细非空。
- 缺数据：空库 → 各指数 available=False（优雅降级，不抛 500）。
"""
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from app.analysis.us_impact import compute_us_impact
from app.collector import normalize
from app.config import get_settings
from app.db import init_db, make_engine, session_scope
from app.repository import quote_repo


def _weekdays(n: int, start: date = date(2026, 1, 1)) -> list:
    out, d, step = [], start, timedelta(days=1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += step
    return out


def _naive_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _seed_correlated(session, n: int = 60, beta: float = 0.5):
    """US_INDEX(usDJI) 与 INDEX(000300) 日线：a_ret[k] = beta * us_ret[k-1]。

    经 normalize（含 change_percent 反算）入库；配对 US(d_i)→A(d_{i+1}) 收益强相关。
    """
    now = _naive_now()
    days = _weekdays(n)
    rng = np.random.default_rng(42)
    us_ret = rng.uniform(-0.03, 0.03, size=n)
    # US 收盘价随机游走
    us_close = [100.0]
    for r in us_ret[1:]:
        us_close.append(us_close[-1] * (1 + r))
    us_close = [round(c, 4) for c in us_close]
    # A 收盘：a_ret[k] = beta * us_ret[k-1]（k>=1），即 A股次日反应美股前一日
    a_close = [100.0]
    for k in range(1, n):
        a_r = beta * us_ret[k - 1]
        a_close.append(a_close[-1] * (1 + a_r))
    a_close = [round(c, 4) for c in a_close]

    def _df(closes):
        return pd.DataFrame(
            {
                "date": [d.isoformat() for d in days],
                "open": closes,
                "high": closes,
                "low": closes,
                "close": closes,
                "volume": [1_000_000] * n,
            }
        )

    us_rows = normalize.normalize_index_bar(_df(us_close), "sina_us", "usDJI", now, symbol_type="US_INDEX")
    a_rows = normalize.normalize_index_bar(_df(a_close), "sina", "000300", now, symbol_type="INDEX")
    quote_repo.upsert_market_quotes(session, us_rows)
    quote_repo.upsert_market_quotes(session, a_rows)
    session.commit()


def _setup(tmp_path):
    s = get_settings(force_reload=True)
    s.paths.sqlite_path_abs = tmp_path / "etf_monitor.db"
    s.paths.backup_dir_abs = tmp_path / "backups"
    s.paths.log_dir_abs = tmp_path / "logs"
    eng = make_engine(s)
    init_db(eng, s)
    return s, eng


def test_compute_us_impact_correlated(tmp_path):
    s, eng = _setup(tmp_path)
    with session_scope(eng) as session:
        _seed_correlated(session, n=60, beta=0.5)
        out = compute_us_impact(session)
    assert out["primary_benchmark"] == "000300"
    items = {it["code"]: it for it in out["items"]}
    assert "usDJI" in items
    dji = items["usDJI"]
    assert dji["available"] is True
    assert dji["pair_count"] >= 40
    # 合成关系强相关：近期相关应显著为正（≈1）
    assert dji["correlation_recent"] is not None and dji["correlation_recent"] > 0.8
    assert dji["correlation_long"] is not None and dji["correlation_long"] > 0.8
    # β ≈ 0.5
    assert dji["beta"] is not None and abs(dji["beta"] - 0.5) < 0.2
    # 近期传导明细非空且字段齐全
    assert len(dji["recent"]) > 0
    pt = dji["recent"][0]
    assert set(pt.keys()) == {"us_date", "us_pct", "ashare_date", "ashare_pct"}
    # A股反应日严格晚于美股日
    assert pt["ashare_date"] > pt["us_date"]


def test_compute_us_impact_graceful_when_empty(tmp_path):
    s, eng = _setup(tmp_path)
    with session_scope(eng) as session:
        out = compute_us_impact(session)
    assert out["items"]
    for it in out["items"]:
        assert it["available"] is False
        assert it["correlation_recent"] is None
        assert it["beta"] is None
        assert "观察期" in (it["note"] or "")
