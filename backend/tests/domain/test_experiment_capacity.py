"""domain.experiment 용량 스윕(V4-04, 수학 노트 8절)을 손계산 정답으로 본다."""

from __future__ import annotations

from datetime import date

import pytest

from strategy_workbench.domain.backtest.facade.runs import RawArtifactBundle, RawFill, RawOrder
from strategy_workbench.domain.experiment.facade.statistics import (
    CAPACITY_SHARPE_RATIO,
    CapacityLimit,
    ExecutionCosts,
    capacity_limit,
    execution_costs,
)

_DAY = date(2021, 1, 4)


def _order(order_id: str, quantity: str) -> RawOrder:
    return RawOrder(order_id, "d", _DAY, "KRX:005930", "buy", quantity, "market", "gtc")


def _fill(order_id: str, quantity: str, price: float, slippage: float) -> RawFill:
    return RawFill(
        f"f-{order_id}-{quantity}",
        order_id,
        _DAY,
        "KRX:005930",
        "buy",
        quantity,
        price,
        0.0,
        slippage,
    )


def _bundle(orders: tuple[RawOrder, ...], fills: tuple[RawFill, ...]) -> RawArtifactBundle:
    return RawArtifactBundle((), (), orders, fills, (), ())


def test_execution_costs_weigh_impact_by_traded_value_and_count_each_order_once() -> None:
    # 주문 a 150주는 참여 한도로 100주·30주 두 번에 나눠 체결됐고, 주문 b 50주는 다 체결됐다.
    bundle = _bundle(
        (_order("a", "150"), _order("b", "50")),
        (
            _fill("a", "100", 1000.0, 2.0),
            _fill("a", "30", 1000.0, 2.0),
            _fill("b", "50", 2000.0, 4.0),
        ),
    )

    # 충격 (2×100 + 2×30 + 4×50) / (1000×100 + 1000×30 + 2000×50) = 460 / 230000 = 20bp.
    # 미체결 1 − 180/200 = 0.1.
    costs = execution_costs(bundle)
    assert (costs.impact_cost_bps, costs.unfilled_ratio) == (
        pytest.approx(20.0),
        pytest.approx(0.1),
    )


def test_execution_costs_are_empty_without_orders_or_fills() -> None:
    assert execution_costs(_bundle((), ())) == ExecutionCosts(None, None)
    # 주문은 냈지만 하나도 체결되지 않았다.
    assert execution_costs(_bundle((_order("a", "10"),), ())) == ExecutionCosts(None, 1.0)


@pytest.mark.parametrize(
    ("points", "expected"),
    [
        # 최고 1억 0.05, 기준 0.025. 10억 0.03 은 넘고 100억 0.02 에서 처음 떨어진다. 1000억에서
        # 다시 오른 0.04 는 보지 않는다.
        (
            [(1e10, 0.02), (1e7, 0.02), (1e9, 0.03), (1e11, 0.04), (1e8, 0.05)],
            CapacityLimit(1e9, beyond_tested=False),
        ),
        # 가장 큰 금액까지 기준 위다.
        ([(1e7, 0.04), (1e8, 0.05), (1e9, 0.03)], CapacityLimit(1e9, beyond_tested=True)),
        # 최고의 정확히 절반은 기준 밑이 아니다.
        ([(1e8, 0.5), (1e9, 0.25)], CapacityLimit(1e9, beyond_tested=True)),
        # 큰 금액이 실패·파산했으면 기준 밑이다.
        ([(1e8, 0.05), (1e9, None), (1e10, 0.05)], CapacityLimit(1e8, beyond_tested=False)),
        # 최고 금액보다 작은 금액의 부진·실패는 한계와 무관하다(반올림 손해).
        ([(1e6, None), (1e7, 0.01), (1e8, 0.05)], CapacityLimit(1e8, beyond_tested=True)),
        # 같은 최고 샤프면 작은 금액부터 본다.
        ([(1e8, 0.05), (1e9, 0.01), (1e10, 0.05)], CapacityLimit(1e8, beyond_tested=False)),
        # 최고 샤프가 0 이하거나 샤프가 난 금액이 없으면 한계가 없다.
        ([(1e8, -0.01), (1e9, 0.0)], CapacityLimit(None, beyond_tested=False)),
        ([(1e8, None)], CapacityLimit(None, beyond_tested=False)),
        ([], CapacityLimit(None, beyond_tested=False)),
    ],
)
def test_the_capacity_limit_is_the_last_amount_before_the_sharpe_halves(
    points: list[tuple[float, float | None]], expected: CapacityLimit
) -> None:
    assert CAPACITY_SHARPE_RATIO == 0.5
    assert capacity_limit(points) == expected
