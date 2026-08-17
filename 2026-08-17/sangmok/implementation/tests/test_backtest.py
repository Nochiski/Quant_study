from __future__ import annotations

import pandas as pd
import pytest

from quant_study.backtest import RebalanceFrequency, make_handle_data, rebalance_period


@pytest.mark.parametrize("short_window,long_window", [(20, 20), (21, 20)])
def test_make_handle_data_requires_short_window_below_long_window(
    short_window: int,
    long_window: int,
) -> None:
    with pytest.raises(ValueError, match="short window must be smaller"):
        make_handle_data(short_window, long_window)


def test_make_handle_data_accepts_a_valid_window_pair() -> None:
    assert callable(make_handle_data(short_window_days=5, long_window_days=20))


def test_make_handle_data_rejects_invalid_allocation() -> None:
    with pytest.raises(ValueError, match="allocation must be between 0 and 1"):
        make_handle_data(5, 20, allocation=1.1)


def test_rebalance_period_groups_sessions_by_requested_frequency() -> None:
    timestamp = pd.Timestamp("2026-08-17")

    assert rebalance_period(timestamp, RebalanceFrequency.DAILY) == "2026-08-17"
    assert rebalance_period(timestamp, RebalanceFrequency.WEEKLY) == "2026-W34"
    assert rebalance_period(timestamp, RebalanceFrequency.MONTHLY) == "2026-08"
