from datetime import date, datetime, timezone

import pandas as pd

from app.sector_engine.engine import SectorEngine


def _rows(flows, *, source="westock", dates=None):
    dates = dates or [date(2026, 7, 28), date(2026, 7, 29), date(2026, 7, 30)]
    return pd.DataFrame(
        {
            "timestamp": [
                datetime(d.year, d.month, d.day, 7, tzinfo=timezone.utc) for d in dates
            ],
            "trading_date": dates,
            "metric_source": [source] * len(dates),
            "main_net_inflow": flows,
            "amount": [1000.0] * len(dates),
            "large_order_inflow": [None] * len(dates),
        }
    )


def test_fund_flow_all_null_is_missing_not_zero_score():
    result = SectorEngine().evaluate_fund_flow(_rows([None, None, None]), None)

    assert result["available"] is False
    assert result["score"] is None
    assert result["usable_observations"] == 0


def test_fund_flow_requires_three_actual_same_source_observations():
    empty_history = _rows([None, None, None], source="em")
    two_actual = _rows([10.0, 20.0], dates=[date(2026, 7, 29), date(2026, 7, 30)])
    result = SectorEngine().evaluate_fund_flow(
        pd.concat([empty_history, two_actual], ignore_index=True), None
    )

    assert result["available"] is False
    assert result["metric_source"] == "westock"
    assert result["usable_observations"] == 2


def test_fund_flow_selects_source_with_real_data_and_scores_it():
    empty_history = _rows([None, None, None], source="em")
    actual = _rows([10.0, 20.0, 30.0])
    result = SectorEngine().evaluate_fund_flow(
        pd.concat([empty_history, actual], ignore_index=True), None
    )

    assert result["available"] is True
    assert result["metric_source"] == "westock"
    assert result["usable_observations"] == 3
    assert result["consecutive_positive_days"] == 3
    assert result["score"] == 70.0


def test_missing_latest_trading_day_breaks_consecutive_inflow():
    actual = _rows(
        [10.0, 20.0, 30.0],
        dates=[date(2026, 7, 27), date(2026, 7, 28), date(2026, 7, 29)],
    )
    latest_without_flow = _rows(
        [None], source="em", dates=[date(2026, 7, 30)]
    )
    result = SectorEngine().evaluate_fund_flow(
        pd.concat([actual, latest_without_flow], ignore_index=True), None
    )

    assert result["available"] is True
    assert result["consecutive_positive_days"] == 0
