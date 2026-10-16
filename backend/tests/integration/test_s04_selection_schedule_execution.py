"""동결 일정의 단일 판단들을 기존 커널의 한 계좌에서 합성 검산한다."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from strategy_workbench.domain.backtest.facade.runs import ExecutionCore
from strategy_workbench.domain.experiment.facade.design import WalkForwardWindow
from strategy_workbench.domain.experiment.facade.selection_schedule import (
    SelectionSchedule,
    SelectionScheduleStatus,
    WindowSelection,
)
from tests.s04_account_fixture import (
    CORES,
    SyntheticAccountScenario,
    account_bar,
    account_observations,
)
from tests.s04_selection_schedule_fixture import (
    run_selection_schedule,
    synthetic_candidates,
)

SESSIONS = tuple(date(2024, 1, day) for day in (5, 8, 9, 10, 11, 12, 15, 16))
A, B = "synthetic-a", "synthetic-b"


def _schedule(second_candidate: str = "low") -> SelectionSchedule:
    return SelectionSchedule(
        selections=(
            WindowSelection(
                window_index=0,
                window=WalkForwardWindow(
                    date(2023, 1, 1), date(2024, 1, 4), SESSIONS[0], SESSIONS[4]
                ),
                candidate_id="high",
                known_as_of=date(2024, 1, 4),
            ),
            WindowSelection(
                window_index=1,
                window=WalkForwardWindow(date(2023, 1, 1), SESSIONS[4], SESSIONS[5], SESSIONS[-1]),
                candidate_id=second_candidate,
                known_as_of=SESSIONS[4],
            ),
        ),
        sessions=SESSIONS,
    )


def _scenario(*, fee_bps: float = 0.0, tax_bps: float = 0.0) -> SyntheticAccountScenario:
    return SyntheticAccountScenario(
        sessions=SESSIONS,
        bars=tuple(
            account_bar(
                session,
                security_id,
                110 if security_id == A and index >= 4 else 100,
                110
                if (security_id == A and index >= 4) or (security_id == B and index == 7)
                else 100,
            )
            for index, session in enumerate(SESSIONS)
            for security_id in (A, B)
        ),
        observations=account_observations(SESSIONS, (((A, 2.0), (B, 1.0)),) * len(SESSIONS)),
        memberships=(),
        fee_bps=fee_bps,
        sell_tax_bps=tax_bps,
    )


@pytest.mark.parametrize("core", CORES)
@pytest.mark.parametrize(
    "fee_bps,tax_bps,final_equity", [(0.0, 0.0, 1210.0), (100.0, 200.0, 1141.3)]
)
def test_distinct_candidates_use_next_common_session_and_boundary_costs(
    core: ExecutionCore, fee_bps: float, tax_bps: float, final_equity: float
) -> None:
    schedule = _schedule()
    assert schedule.status is SelectionScheduleStatus.READY
    assert [(item.signal_as_of, item.execution_on) for item in schedule.transitions] == [
        (SESSIONS[0], SESSIONS[1]),
        (SESSIONS[5], SESSIONS[6]),
    ]
    assert schedule.candidate_at(SESSIONS[0]) is None
    assert schedule.candidate_at(SESSIONS[5]) == "high"
    assert schedule.candidate_at(SESSIONS[6]) == "low"
    run = run_selection_schedule(
        _scenario(fee_bps=fee_bps, tax_bps=tax_bps), schedule, synthetic_candidates(), core
    )
    assert run.evidence[0].strategy_hash != run.evidence[1].strategy_hash
    assert [[target.security_id for target in item.frame.targets] for item in run.evidence] == [
        [A],
        [B],
    ]
    fills = run.store.fills()
    first_qty, second_qty = (10, 11) if fee_bps == 0 else (9, 10)
    assert [
        (fill.ts.date(), fill.instrument.symbol, fill.side.value, int(fill.quantity))
        for fill in fills
    ] == [
        (SESSIONS[1], A, "buy", first_qty),
        (SESSIONS[6], A, "sell", first_qty),
        (SESSIONS[6], B, "buy", second_qty),
    ]
    assert sum(fill.fee for fill in fills) == pytest.approx(0 if fee_bps == 0 else 28.9)
    assert sum(fill.sell_tax for fill in fills) == pytest.approx(0 if tax_bps == 0 else 19.8)
    snapshots = run.store.snapshots()
    assert [item.ts.date() for item in snapshots] == list(SESSIONS)
    assert snapshots[0].cash == snapshots[0].equity == 1000
    assert snapshots[5].equity == pytest.approx(1100 if fee_bps == 0 else 1081)
    assert snapshots[6].cash == pytest.approx(0 if fee_bps == 0 else 41.3)
    assert snapshots[-1].equity == pytest.approx(final_equity)
    assert [
        (position.instrument.symbol, int(position.quantity)) for position in snapshots[-1].positions
    ] == [(B, second_qty)]


@pytest.mark.parametrize("core", CORES)
def test_same_candidate_keeps_actual_holdings_without_boundary_liquidation(
    core: ExecutionCore,
) -> None:
    schedule = _schedule("high")
    assert [item.candidate_changed for item in schedule.transitions] == [True, False]
    scenario = _scenario()
    scenario = replace(
        scenario,
        bars=tuple(account_bar(row.session, row.security_id, 100, 100) for row in scenario.bars),
    )
    run = run_selection_schedule(scenario, schedule, synthetic_candidates(), core)
    assert len(run.store.orders()) == len(run.store.fills()) == 1
    assert run.evidence[0].strategy_hash == run.evidence[1].strategy_hash
    assert run.store.snapshots()[-1].cash == 0
    assert run.store.snapshots()[-1].positions[0].quantity == Decimal(10)
    assert run.store.snapshots()[-1].equity == 1000


@pytest.mark.parametrize("core", CORES)
def test_partial_fill_rotation_sells_only_actual_two_shares(core: ExecutionCore) -> None:
    scenario = _scenario()
    scenario = replace(
        scenario,
        bars=tuple(
            replace(row, volume=20) if row.session == SESSIONS[1] and row.security_id == A else row
            for row in scenario.bars
        ),
    )
    run = run_selection_schedule(scenario, _schedule(), synthetic_candidates(), core)
    assert [
        (fill.instrument.symbol, fill.side.value, int(fill.quantity)) for fill in run.store.fills()
    ] == [
        (A, "buy", 2),
        (A, "sell", 2),
        (B, "buy", 10),
    ]
    # 1000−2×100+2×110−10×100=20원. 목표 10주가 아닌 실제 2주만 판다.
    assert run.store.snapshots()[-1].cash == 20
    assert run.store.snapshots()[-1].equity == 1120


@pytest.mark.parametrize("core", CORES)
def test_boundary_day_orders_expire_in_empty_session_without_retry(core: ExecutionCore) -> None:
    scenario = _scenario()
    scenario = replace(
        scenario, bars=tuple(row for row in scenario.bars if row.session != SESSIONS[6])
    )
    run = run_selection_schedule(scenario, _schedule(), synthetic_candidates(), core)
    boundary_orders = [order for order in run.store.orders() if order.ts.date() == SESSIONS[5]]
    assert len(boundary_orders) == 2
    assert {order.time_in_force.value for order in boundary_orders} == {"day"}
    boundary_ids = {order.order_id for order in boundary_orders}
    assert [
        (event.ts.date(), event.status.value)
        for event in run.store.order_updates()
        if event.order_id in boundary_ids
    ] == [
        (SESSIONS[6], "cancelled"),
        (SESSIONS[6], "cancelled"),
    ]
    assert len(run.store.fills()) == 1
    final = run.store.snapshots()[-1]
    assert final.cash == 0
    assert [
        (position.instrument.symbol, int(position.quantity)) for position in final.positions
    ] == [(A, 10)]
    assert final.equity == 1100


@pytest.mark.parametrize("core", CORES)
def test_halted_old_candidate_does_not_fund_replacement_with_invented_proceeds(
    core: ExecutionCore,
) -> None:
    scenario = _scenario()
    scenario = replace(
        scenario,
        bars=tuple(
            row for row in scenario.bars if row.security_id != A or row.session < SESSIONS[5]
        ),
    )
    run = run_selection_schedule(scenario, _schedule(), synthetic_candidates(), core)
    assert len(run.store.fills()) == 1
    assert sum(fill.fee + fill.sell_tax for fill in run.store.fills()) == 0
    final = run.store.snapshots()[-1]
    assert final.cash == 0
    assert [
        (position.instrument.symbol, int(position.quantity), position.market_price)
        for position in final.positions
    ] == [(A, 10, 110)]
    # 마지막 평가 1100원은 실현된 회수금이나 B를 매수할 현금이 아니다.
    assert final.equity == 1100
