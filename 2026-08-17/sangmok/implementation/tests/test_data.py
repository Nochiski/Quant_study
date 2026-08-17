from __future__ import annotations

import pandas as pd
import pytest

from quant_study.data import normalize_yyyymmdd, to_zipline_daily_csv


def test_normalize_yyyymmdd_accepts_compact_and_hyphenated_dates() -> None:
    assert normalize_yyyymmdd("20260817") == "20260817"
    assert normalize_yyyymmdd("2026-08-17") == "20260817"


@pytest.mark.parametrize("value", ["2026/08/17", "202608", "not-a-date"])
def test_normalize_yyyymmdd_rejects_invalid_formats(value: str) -> None:
    with pytest.raises(ValueError, match="date must be YYYYMMDD"):
        normalize_yyyymmdd(value)


def test_to_zipline_daily_csv_adds_required_corporate_action_columns() -> None:
    frame = pd.DataFrame(
        {
            "open": [70_000],
            "high": [71_000],
            "low": [69_500],
            "close": [70_500],
            "volume": [1_000_000],
        },
        index=pd.DatetimeIndex(["2026-08-17"], name="date"),
    )

    result = to_zipline_daily_csv(frame)

    assert result.to_dict(orient="records") == [
        {
            "date": "2026-08-17",
            "open": 70_000,
            "high": 71_000,
            "low": 69_500,
            "close": 70_500,
            "volume": 1_000_000,
            "dividend": 0.0,
            "split": 1.0,
        }
    ]
