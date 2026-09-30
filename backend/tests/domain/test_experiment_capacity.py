"""domain.experiment 용량 스윕(V4-04, 수학 노트 8절)을 손계산 정답으로 본다."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from strategy_workbench.domain.backtest.facade.runs import RawArtifactBundle, RawFill, RawOrder
from strategy_workbench.domain.experiment.facade.statistics import (
    CAPACITY_SHARPE_RATIO,
    CapacityGap,
    CapacityLimit,
    ExecutionCosts,
    capacity_limit,
    execution_costs,
)

_DAY = date(2021, 1, 4)
_NEXT = _DAY + timedelta(days=1)


def _order(order_id: str, quantity: str, session: date = _DAY) -> RawOrder:
    # 워크벤치 전략 주문은 모두 DAY 다 — 잔량은 그 세션에 사라진다.
    return RawOrder(order_id, "d", session, "KRX:005930", "buy", quantity, "market", "day")


def _fill(
    order_id: str,
    quantity: str,
    price: float,
    slippage: float,
    session: date = _DAY,
    cap_volume: int | None = 1_000,
) -> RawFill:
    return RawFill(
        f"f-{order_id}",
        order_id,
        session,
        "KRX:005930",
        "buy",
        quantity,
        price,
        0.0,
        slippage,
        cap_volume,
    )


def _bundle(orders: tuple[RawOrder, ...], fills: tuple[RawFill, ...]) -> RawArtifactBundle:
    return RawArtifactBundle((), (), orders, fills, (), ())


def test_execution_costs_weigh_impact_by_traded_value_and_count_each_session_order() -> None:
    # 첫날 주문 a 150주는 참여 한도로 100주만 체결되고 잔량은 사라졌다. 다음 리밸런스가 부족분을 새
    # 주문 c 40주로 다시 내 30주 체결됐다. 주문 b 50주는 첫날 다 체결됐다.
    bundle = _bundle(
        (_order("a", "150"), _order("b", "50"), _order("c", "40", _NEXT)),
        (
            _fill("a", "100", 1000.0, 2.0),
            _fill("b", "50", 2000.0, 4.0),
            _fill("c", "30", 1000.0, 2.0, _NEXT, cap_volume=600),
        ),
    )

    # 충격 (2×100 + 4×50 + 2×30) / (1000×100 + 2000×50 + 1000×30) = 460 / 230000 = 20bp.
    # 세션 미체결 1 − 180/240 = 0.25 — 같은 부족분이 다음 세션 주문에서 다시 세어진다.
    # 참여율 180 / (첫날 기준 거래량 1,000 + 다음 날 600) = 0.1125 — 첫날 두 체결은 한 거래량을
    # 쓴다.
    costs = execution_costs(bundle)
    assert (costs.impact_cost_bps, costs.session_unfilled_ratio, costs.participation_rate) == (
        pytest.approx(20.0),
        pytest.approx(0.25),
        pytest.approx(0.1125),
    )


def test_execution_costs_are_empty_without_orders_or_fills() -> None:
    assert execution_costs(_bundle((), ())) == ExecutionCosts(None, None, None)
    # 주문은 냈지만 하나도 체결되지 않았다(참여 한도 0주 등).
    assert execution_costs(_bundle((_order("a", "10"),), ())) == ExecutionCosts(None, 1.0, None)


def test_participation_is_empty_for_a_result_written_before_cap_volume() -> None:
    """`backtest-artifacts-v1` 결과는 체결에 기준 거래량이 없다 — 참여율을 지어내지 않는다."""
    bundle = _bundle(
        (_order("a", "100"), _order("b", "50", _NEXT)),
        (_fill("a", "100", 1000.0, 2.0), _fill("b", "50", 1000.0, 2.0, _NEXT, cap_volume=None)),
    )

    costs = execution_costs(bundle)

    assert (costs.session_unfilled_ratio, costs.participation_rate) == (0.0, None)


def _limit(amount: float, best: float, threshold: float, gap: CapacityGap | None = None):
    return CapacityLimit(amount, best, pytest.approx(threshold), gap)  # pyright: ignore[reportArgumentType]  # reason: 기준선은 부동소수 곱이라 근사로 본다


@pytest.mark.parametrize(
    ("points", "expected"),
    [
        # 최고 1억 0.05, 기준 0.025. 10억 0.03 은 넘고 100억 0.02 에서 처음 떨어진다.
        ([(1e10, 0.02), (1e7, 0.02), (1e9, 0.03), (1e8, 0.05)], _limit(1e9, 1e8, 0.025)),
        # 비단조: 100억에서 떨어졌다가 1000억에서 0.04 로 다시 올라도 첫 하락 직전 10억이 한계다 —
        # 다시 오른 금액까지 치면 용량을 부풀린다(보수 쪽).
        (
            [(1e10, 0.02), (1e9, 0.03), (1e11, 0.04), (1e8, 0.05)],
            _limit(1e9, 1e8, 0.025),
        ),
        # 가장 큰 금액까지 기준 위다.
        (
            [(1e7, 0.04), (1e8, 0.05), (1e9, 0.03)],
            _limit(1e9, 1e8, 0.025, CapacityGap.BEYOND_TESTED),
        ),
        # 최고의 정확히 절반은 기준 밑이 아니다.
        ([(1e8, 0.5), (1e9, 0.25)], _limit(1e9, 1e8, 0.25, CapacityGap.BEYOND_TESTED)),
        # 큰 금액이 실패·파산했으면 기준 밑이다.
        ([(1e8, 0.05), (1e9, None), (1e10, 0.05)], _limit(1e8, 1e8, 0.025)),
        # 최고 금액보다 작은 금액의 부진·실패는 한계와 무관하다(반올림 손해).
        (
            [(1e6, None), (1e7, 0.01), (1e8, 0.05)],
            _limit(1e8, 1e8, 0.025, CapacityGap.BEYOND_TESTED),
        ),
        # 같은 최고 샤프면 작은 금액부터 본다.
        ([(1e8, 0.05), (1e9, 0.01), (1e10, 0.05)], _limit(1e8, 1e8, 0.025)),
    ],
)
def test_the_capacity_limit_is_the_last_amount_before_the_sharpe_first_halves(
    points: list[tuple[float, float | None]], expected: CapacityLimit
) -> None:
    assert CAPACITY_SHARPE_RATIO == 0.5
    assert capacity_limit(points) == expected


@pytest.mark.parametrize(
    ("points", "flags", "gap"),
    [
        # 최고 샤프가 0 이하거나 샤프가 난 금액이 없다.
        ([(1e8, -0.01), (1e9, 0.0)], {}, CapacityGap.NO_POSITIVE_SHARPE),
        ([(1e8, None)], {}, CapacityGap.NO_POSITIVE_SHARPE),
        ([], {}, CapacityGap.NO_POSITIVE_SHARPE),
        # 도는 금액·취소된 금액이 있으면 확정하지 않는다 — 돌지 않은 금액에서 꺾였다고 하지 않는다.
        ([(1e8, 0.05), (1e9, 0.049), (1e10, None)], {"pending": True}, CapacityGap.PENDING),
        ([(1e8, 0.05), (1e9, 0.049), (1e10, None)], {"cancelled": True}, CapacityGap.CANCELLED),
    ],
)
def test_no_capacity_limit_is_given_without_a_settled_positive_curve(
    points: list[tuple[float, float | None]], flags: dict[str, bool], gap: CapacityGap
) -> None:
    assert capacity_limit(points, **flags) == CapacityLimit(None, None, None, gap)
