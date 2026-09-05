from __future__ import annotations

import pandas as pd
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from quant_study import api
from quant_study.api import BacktestRequest, add_buy_hold_benchmark, serialize_prices


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
    monkeypatch.setattr(api, "PROJECT_ROOT", tmp_path)

    with pytest.raises(HTTPException, match="저장된 최신 가격 데이터") as error:
        api.latest_prices()

    assert error.value.status_code == 404


def test_backtest_request_rejects_equal_moving_average_windows() -> None:
    with pytest.raises(ValidationError, match="단기 MA는 장기 MA보다 작아야 합니다"):
        BacktestRequest(
            ticker="005930",
            start="2020-01-01",
            end="2020-01-31",
            strategy="ma-trend",
            short_window=20,
            long_window=20,
            rebalance="daily",
            allocation=1.0,
            capital=10_000_000,
            fee_bps=0,
            slippage_bps=0,
        )


def test_run_backtest_serializes_zipline_performance(tmp_path, monkeypatch) -> None:
    performance = pd.DataFrame(
        {
            "portfolio_value": [10_000_000.0, 10_100_000.0],
            "price": [70_000.0, 71_000.0],
            "invested": [0.0, 1.0],
            "gross_leverage": [0.0, 0.8],
            "benchmark_period_return": [0.0, 0.02],
            "algorithm_period_return": [0.0, 0.01],
            "max_drawdown": [0.0, 0.0],
            "sharpe": [float("nan"), 1.2],
            "sortino": [float("nan"), 1.5],
            "algo_volatility": [float("nan"), 0.1],
        },
        index=pd.DatetimeIndex(["2020-01-02", "2020-01-03"]),
    )
    monkeypatch.setattr(api, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(api, "run_zipline_backtest", lambda config: performance)
    request = BacktestRequest(
        ticker="005930",
        start="2020-01-02",
        end="2020-01-03",
        strategy="ma-trend",
        short_window=2,
        long_window=5,
        rebalance="daily",
        allocation=1.0,
        capital=10_000_000,
        fee_bps=1,
        slippage_bps=2,
    )

    response = api.run_backtest(request)

    assert response.engine == "zipline-reloaded"
    assert response.rows == 2
    assert response.points[0].sharpe is None
    assert response.points[1].invested == 0.8
    assert response.points[1].benchmark_value == 10_200_000
    assert response.points[1].benchmark_period_return == 0.02
    assert (tmp_path / "public/backtests/latest_performance.csv").exists()


def test_add_buy_hold_benchmark_fills_missing_zipline_values() -> None:
    frame = pd.DataFrame(
        {
            "price": [50_000.0, 55_000.0],
            "benchmark_period_return": [float("nan"), float("nan")],
        }
    )

    result = add_buy_hold_benchmark(frame)

    assert result["benchmark_period_return"].tolist() == pytest.approx([0.0, 0.1])
