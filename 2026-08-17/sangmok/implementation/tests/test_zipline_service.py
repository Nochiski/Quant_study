from __future__ import annotations

from quant_study.zipline_service import warmup_start


def test_warmup_start_reserves_more_than_the_requested_long_window() -> None:
    assert warmup_start("2020-01-13", long_window_days=20) == "20191114"
    assert warmup_start("2020-01-13", long_window_days=5) == "20191129"
