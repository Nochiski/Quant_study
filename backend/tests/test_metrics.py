"""성과 지표 단위 테스트: 손계산 값과 대조, None 정책 확인."""

from __future__ import annotations

from decimal import Decimal

import pytest

from backtest_engine.engine.metrics import compute_metrics
from backtest_engine.types.events import FillEvent
from backtest_engine.types.orders import Side
from backtest_engine.types.portfolio import PortfolioSnapshot
from tests.conftest import day, make_instrument

ANNUALIZATION_DAYS = 252


def snapshot(ts_day: int, equity: float) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        ts=day(ts_day), cash=equity, positions=(), equity=equity, gross_exposure=0.0
    )


def test_total_return_and_drawdown_hand_computed() -> None:
    snapshots = (snapshot(1, 100.0), snapshot(2, 110.0), snapshot(3, 99.0))
    metrics = compute_metrics(snapshots, (), ANNUALIZATION_DAYS)

    assert metrics.total_return == pytest.approx(-0.01)
    # 고점 110 → 99: MDD = 99/110 - 1 = -0.1 (음수 비율)
    assert metrics.max_drawdown == pytest.approx(-0.1)
    assert metrics.sharpe is not None
    assert metrics.calmar is not None


def test_flat_equity_curve_uses_none_not_zero() -> None:
    snapshots = (snapshot(1, 100.0), snapshot(2, 100.0), snapshot(3, 100.0))
    metrics = compute_metrics(snapshots, (), ANNUALIZATION_DAYS)

    assert metrics.total_return == 0.0
    assert metrics.volatility == 0.0
    assert metrics.sharpe is None  # 변동성 0 → 0으로 위장하지 않는다
    assert metrics.sortino is None
    assert metrics.max_drawdown == 0.0
    assert metrics.calmar is None  # MDD 0 → None


def test_turnover_from_fills() -> None:
    snapshots = (snapshot(1, 100_000.0), snapshot(2, 100_000.0))
    fill = FillEvent(
        fill_id="F-000001",
        order_id="O-000001",
        ts=day(2),
        instrument=make_instrument(),
        quantity=Decimal(100),
        side=Side.BUY,
        price=500.0,
        fee=0.0,
        slippage_per_share=0.0,
    )
    metrics = compute_metrics(snapshots, (fill,), ANNUALIZATION_DAYS)
    # 체결 금액 50,000 / 평균 equity 100,000 = 0.5
    assert metrics.turnover == pytest.approx(0.5)


def test_empty_run_rejected() -> None:
    with pytest.raises(ValueError, match="snapshots=0"):
        compute_metrics((), (), ANNUALIZATION_DAYS)
