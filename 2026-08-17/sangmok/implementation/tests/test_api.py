from __future__ import annotations

import pandas as pd
import pytest
from fastapi import HTTPException

from quant_study.api import latest_prices, serialize_prices


def test_serialize_prices_returns_typed_price_rows() -> None:
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

    prices = serialize_prices(frame)

    assert len(prices) == 1
    assert prices[0].date == "2026-08-17"
    assert prices[0].close == 70_500


def test_latest_prices_reports_missing_persisted_data(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(HTTPException, match="저장된 최신 가격 데이터") as error:
        latest_prices()

    assert error.value.status_code == 404
