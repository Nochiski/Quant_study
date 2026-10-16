"""보고 기간 경계의 정지·분할·상장 종료·빈 세션을 한 계좌에서 손계산한다."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from strategy_workbench.application.backtest_run.facade.ports import (
    CorporateActionRecord,
    UniverseMembershipRecord,
)
from strategy_workbench.domain.backtest.facade.runs import ExecutionCore
from tests.s04_account_fixture import (
    CORES,
    SyntheticAccountScenario,
    account_bar,
    account_observations,
    run_account_scenario,
)

SESSIONS = tuple(date(2024, 1, day) for day in (5, 8, 9, 10, 11, 12, 15))
A, B = "synthetic-a", "synthetic-b"


@pytest.mark.parametrize("core", CORES)
def test_split_during_boundary_halt_settles_once_at_actual_resumption(
    core: ExecutionCore,
) -> None:
    """01-10 보고 경계는 실행을 나누지 않으며 정지 중 분할을 조기 정산하지 않는다."""
    scenario = SyntheticAccountScenario(
        sessions=SESSIONS,
        bars=tuple(
            account_bar(
                session,
                A,
                50 if index >= 5 else 100,
                55 if index == 6 else 50 if index >= 5 else 100,
            )
            for index, session in enumerate(SESSIONS)
            if index not in (3, 4)
        ),
        observations=account_observations(SESSIONS, (((A, 1.0),),) * len(SESSIONS)),
        memberships=(UniverseMembershipRecord(A, SESSIONS[0], SESSIONS[-1]),),
        corporate_actions=(CorporateActionRecord(SESSIONS[3], A, "split", "2", "합성 2:1 분할"),),
    )
    run = run_account_scenario(scenario, core)
    assert [(frame.signal_as_of, frame.execution_on) for frame in run.tape.frames] == [
        (SESSIONS[1], SESSIONS[2]),
        (SESSIONS[3], SESSIONS[4]),
        (SESSIONS[5], SESSIONS[6]),
    ]
    artifacts = run.result.artifacts
    assert [snapshot.session for snapshot in artifacts.snapshots] == list(SESSIONS)
    assert [(order.session, order.quantity) for order in artifacts.orders] == [(SESSIONS[1], "10")]
    assert [(fill.session, fill.quantity, fill.price) for fill in artifacts.fills] == [
        (SESSIONS[2], "10", 100),
    ]
    # 1000−10×100=0. 정지 중 10×100, 재개 후 20×50, 이후 20×55로만 평가한다.
    # 대기 주문 없는 DAY 경로다. GTC 분할 취소는 기존 기업행동 커널 테스트가 소유한다.
    assert [snapshot.cash for snapshot in artifacts.snapshots] == [1000, 1000, 0, 0, 0, 0, 0]
    assert [snapshot.equity for snapshot in artifacts.snapshots] == [1000] * 6 + [1100]
    assert [
        (position.session, position.quantity, position.average_price, position.market_price)
        for position in artifacts.positions
    ] == [
        (SESSIONS[2], "10", 100, 100),
        (SESSIONS[3], "10", 100, 100),
        (SESSIONS[4], "10", 100, 100),
        (SESSIONS[5], "20", 50, 50),
        (SESSIONS[6], "20", 50, 55),
    ]
    assert artifacts.costs == ()
    assert {metric.metric_id: metric.value for metric in run.result.metrics}[
        "total_return"
    ] == pytest.approx(0.1)


@pytest.mark.parametrize("core", CORES)
def test_listing_end_across_boundary_retains_unrecovered_asset_without_cash(
    core: ExecutionCore,
) -> None:
    """01-10 이후 B가 목표여도 상장 종료 A의 회수금을 만들지 않는 한계를 드러낸다."""
    observations = account_observations(
        SESSIONS,
        tuple(((A, 2.0), (B, 1.0)) if index < 3 else ((A, 1.0), (B, 2.0)) for index in range(7)),
    )
    scenario = SyntheticAccountScenario(
        sessions=SESSIONS,
        bars=tuple(
            account_bar(session, security_id, 100, 100)
            for index, session in enumerate(SESSIONS)
            for security_id in (A, B)
            if security_id != A or index <= 2
        ),
        observations=tuple(
            replace(observation, universe_member=False)
            if observation.security_id == A and observation.as_of >= SESSIONS[3]
            else observation
            for observation in observations
        ),
        memberships=(
            UniverseMembershipRecord(A, SESSIONS[0], SESSIONS[2]),
            UniverseMembershipRecord(B, SESSIONS[0], SESSIONS[-1]),
        ),
        benchmark_security_id=A,
    )
    run = run_account_scenario(scenario, core)
    assert [[target.security_id for target in frame.targets] for frame in run.tape.frames] == [
        [A],
        [B],
        [B],
    ]
    artifacts = run.result.artifacts
    assert [snapshot.session for snapshot in artifacts.snapshots] == list(SESSIONS)
    assert [
        (fill.session, fill.security_id, fill.side, fill.quantity) for fill in artifacts.fills
    ] == [
        (SESSIONS[2], A, "buy", "10"),
    ]
    # 초기 현금은 매수로 전부 소진되며 잔존 1000원은 현금화된 회수금이 아니다.
    assert [snapshot.cash for snapshot in artifacts.snapshots] == [1000, 1000, 0, 0, 0, 0, 0]
    assert [snapshot.equity for snapshot in artifacts.snapshots] == [1000] * 7
    assert [
        (
            position.session,
            position.security_id,
            position.quantity,
            position.market_price,
            position.market_value,
        )
        for position in artifacts.positions
    ] == [(session, A, "10", 100, 1000) for session in SESSIONS[2:]]
    assert artifacts.costs == ()
    assert "benchmark.delisted_sessions_frozen" in {
        warning.code for warning in run.result.manifest.warnings
    }


@pytest.mark.parametrize("core", CORES)
def test_rotation_day_orders_expire_at_barless_boundary_before_new_decision(
    core: ExecutionCore,
) -> None:
    """01-11 전체 빈 거래일에서 교체 주문이 만료하고 01-12 새 판단만 이후 체결된다."""
    scenario = SyntheticAccountScenario(
        sessions=SESSIONS,
        bars=tuple(
            account_bar(session, security_id, price, price)
            for index, session in enumerate(SESSIONS)
            for security_id in (A, B)
            if index != 4
            for price in (100 if security_id == B or index < 3 else 110 if index == 3 else 120,)
        ),
        observations=account_observations(
            SESSIONS,
            tuple(
                ((A, 2.0), (B, 1.0)) if index < 3 else ((A, 1.0), (B, 2.0)) for index in range(7)
            ),
        ),
        memberships=tuple(
            UniverseMembershipRecord(name, SESSIONS[0], SESSIONS[-1]) for name in (A, B)
        ),
    )
    run = run_account_scenario(scenario, core)
    artifacts = run.result.artifacts
    assert [snapshot.session for snapshot in artifacts.snapshots] == list(SESSIONS)
    assert [
        (fill.session, fill.security_id, fill.side, fill.quantity, fill.price)
        for fill in artifacts.fills
    ] == [
        (SESSIONS[2], A, "buy", "10", 100),
        (SESSIONS[6], A, "sell", "10", 120),
        (SESSIONS[6], B, "buy", "12", 100),
    ]
    # 01-10에는 1100원으로 B11주를 목표한다. 공백일에 만료되어 재개 시 이 주문은 살아나지 않는다.
    boundary_orders = [order for order in artifacts.orders if order.session == SESSIONS[3]]
    assert [
        (order.security_id, order.side, order.quantity, order.time_in_force)
        for order in boundary_orders
    ] == [
        (B, "buy", "11", "day"),
        (A, "sell", "10", "day"),
    ]
    updates = artifacts.order_updates
    assert updates is not None
    boundary_order_ids = {order.order_id for order in boundary_orders}
    assert [
        (update.session, update.order_id, update.status)
        for update in updates
        if update.order_id in boundary_order_ids
    ] == [(SESSIONS[4], order.order_id, "cancelled") for order in boundary_orders]
    # 재개 종가 120에서 새 목표는 B12주다. 10×120 매도금으로만 12×100을 산다.
    assert [snapshot.cash for snapshot in artifacts.snapshots] == [1000, 1000, 0, 0, 0, 0, 0]
    assert [snapshot.equity for snapshot in artifacts.snapshots] == [
        1000,
        1000,
        1000,
        1100,
        1100,
        1200,
        1200,
    ]
    assert [
        (position.session, position.security_id, position.quantity)
        for position in artifacts.positions
    ] == [
        *((session, A, "10") for session in SESSIONS[2:6]),
        (SESSIONS[6], B, "12"),
    ]
    assert artifacts.costs == ()
