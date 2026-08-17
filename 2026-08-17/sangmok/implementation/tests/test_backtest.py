from __future__ import annotations

import pytest

from quant_study.backtest import make_handle_data


@pytest.mark.parametrize("short_window,long_window", [(20, 20), (21, 20)])
def test_make_handle_data_requires_short_window_below_long_window(
    short_window: int,
    long_window: int,
) -> None:
    with pytest.raises(ValueError, match="short window must be smaller"):
        make_handle_data(short_window, long_window)


def test_make_handle_data_accepts_a_valid_window_pair() -> None:
    assert callable(make_handle_data(short_window_days=5, long_window_days=20))
