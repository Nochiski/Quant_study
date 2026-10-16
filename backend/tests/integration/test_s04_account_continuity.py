"""보고용 창 경계를 가로지르는 단일 계좌의 비용·정수 수량·DAY 주문을 손검산한다."""

from datetime import date

import pytest

from strategy_workbench.application.backtest_run.facade.ports import UniverseMembershipRecord
from strategy_workbench.domain.analytics.facade.metrics import MetricScope
from strategy_workbench.domain.backtest.facade.runs import ExecutionCore, MetricWindow
from tests.s04_account_fixture import (
    CORES,
    SyntheticAccountScenario,
    account_bar,
    account_observations,
    run_account_scenario,
)

A, B = "synthetic-a", "synthetic-b"
SESSIONS = tuple(date(2024, 1, day) for day in (3, 4, 5, 8, 9, 10, 11, 12, 15))


@pytest.mark.parametrize("core", CORES)
@pytest.mark.parametrize("fee_bps", [0.0, 100.0])
def test_fixed_strategy_preserves_one_account_across_reporting_weekend(
    core: ExecutionCore,
    fee_bps: float,
) -> None:
    """01-12/15 보고 경계는 엔진 입력이 아니며 동일 A 목표는 재진입을 만들지 않는다."""
    run = run_account_scenario(
        SyntheticAccountScenario(
            sessions=SESSIONS,
            bars=tuple(account_bar(session, A, 100.0, 100.0) for session in SESSIONS),
            observations=account_observations(SESSIONS, (((A, 1.0),),) * len(SESSIONS)),
            memberships=(UniverseMembershipRecord(A, SESSIONS[0], SESSIONS[-1]),),
            fee_bps=fee_bps,
        ),
        core,
    )
    artifacts = run.result.artifacts
    quantity, cash_krw, equity_krw = (10, 0.0, 1000.0) if fee_bps == 0 else (9, 91.0, 991.0)
    # 비용 1%에서는 10주×100×1.01>1000, 9주 매수 후 1000−900−9=91원이다.
    assert [(fill.session, fill.quantity, fill.fee) for fill in artifacts.fills] == [
        (SESSIONS[2], str(quantity), pytest.approx(0.0 if fee_bps == 0 else 9.0))
    ]
    assert len(artifacts.orders) == 1
    assert [snapshot.cash for snapshot in artifacts.snapshots] == pytest.approx(
        [1000.0, 1000.0] + [cash_krw] * 7
    )
    assert [snapshot.equity for snapshot in artifacts.snapshots] == pytest.approx(
        [1000.0, 1000.0] + [equity_krw] * 7
    )
    assert [(position.session, position.quantity) for position in artifacts.positions] == [
        (session, str(quantity)) for session in SESSIONS[2:]
    ]
    assert not any(order.session == SESSIONS[0] for order in artifacts.orders)
    metrics = {metric.metric_id: metric.value for metric in run.result.metrics}
    assert artifacts.snapshots[0].equity == 1000.0
    assert metrics["total_return"] == pytest.approx(equity_krw / 1000.0 - 1.0)


@pytest.mark.parametrize("core", CORES)
@pytest.mark.parametrize("with_costs", [False, True])
def test_friday_decision_rotates_actual_holdings_after_reporting_weekend(
    core: ExecutionCore,
    with_costs: bool,
) -> None:
    """01-12 금요일 A→B 판단이 01-15 월요일 같은 계좌의 매도 대금으로 체결된다."""
    run = run_account_scenario(
        SyntheticAccountScenario(
            sessions=SESSIONS,
            bars=tuple(
                bar
                for index, session in enumerate(SESSIONS)
                for bar in (
                    account_bar(
                        session, A, 110.0 if index >= 7 else 100.0, 110.0 if index >= 7 else 100.0
                    ),
                    account_bar(session, B, 100.0, 110.0 if index == 8 else 100.0),
                )
            ),
            observations=account_observations(
                SESSIONS,
                tuple(
                    ((A, 2.0 if index < 7 else 1.0), (B, 1.0 if index < 7 else 2.0))
                    for index in range(len(SESSIONS))
                ),
            ),
            memberships=tuple(
                UniverseMembershipRecord(sid, SESSIONS[0], SESSIONS[-1]) for sid in (A, B)
            ),
            metric_windows=(
                MetricWindow(MetricScope.WINDOW, SESSIONS[0], SESSIONS[7], "before"),
                MetricWindow(MetricScope.WINDOW, SESSIONS[8], SESSIONS[8], "after"),
            ),
            fee_bps=100.0 if with_costs else 0.0,
            sell_tax_bps=200.0 if with_costs else 0.0,
        ),
        core,
    )
    assert [(frame.signal_as_of, frame.execution_on) for frame in run.tape.frames] == [
        (SESSIONS[1], SESSIONS[2]),
        (SESSIONS[3], SESSIONS[4]),
        (SESSIONS[5], SESSIONS[6]),
        (SESSIONS[7], SESSIONS[8]),
    ]
    assert [[target.security_id for target in frame.targets] for frame in run.tape.frames] == [
        [A],
        [A],
        [A],
        [B],
    ]
    artifacts = run.result.artifacts
    # 비용 있음: 첫 매수 9주/잔금91; 경계 자산1081; A매도990−9.9−19.8로 현금1051.3.
    # B목표 floor(1081/100)=10주, 매수1000+10 후 잔금41.3, 종가평가1100으로 자산1141.3.
    # 비용 없음: A10주→1100원→B11주, 최종1210원이며 현금은0원이다.
    held_a, bought_b = (9, 10) if with_costs else (10, 11)
    expected_fees = [9.0, 9.9, 10.0] if with_costs else [0.0, 0.0, 0.0]
    expected_cash = [1000, 1000] + ([91.0] * 6 + [41.3] if with_costs else [0.0] * 7)
    expected_equity = (
        [1000, 1000] + [991.0] * 5 + [1081.0, 1141.3] if with_costs else [1000] * 7 + [1100, 1210]
    )
    assert [
        (order.session, order.security_id, order.side, order.quantity) for order in artifacts.orders
    ] == [
        (SESSIONS[1], A, "buy", "10"),
        (SESSIONS[7], B, "buy", str(bought_b)),
        (SESSIONS[7], A, "sell", str(held_a)),
    ]
    assert [
        (fill.session, fill.security_id, fill.side, fill.quantity, fill.price)
        for fill in artifacts.fills
    ] == [
        (SESSIONS[2], A, "buy", str(held_a), 100.0),
        (SESSIONS[8], A, "sell", str(held_a), 110.0),
        (SESSIONS[8], B, "buy", str(bought_b), 100.0),
    ]
    assert [fill.fee for fill in artifacts.fills] == pytest.approx(expected_fees)
    assert [fill.sell_tax for fill in artifacts.fills] == pytest.approx(
        [0.0, 19.8 if with_costs else 0.0, 0.0]
    )
    assert [snapshot.cash for snapshot in artifacts.snapshots] == pytest.approx(expected_cash)
    assert [snapshot.equity for snapshot in artifacts.snapshots] == pytest.approx(expected_equity)
    assert [
        (position.session, position.security_id, position.quantity)
        for position in artifacts.positions
    ] == [
        *((session, A, str(held_a)) for session in SESSIONS[2:8]),
        (SESSIONS[8], B, str(bought_b)),
    ]
    assert sum(cost.amount for cost in artifacts.costs) == pytest.approx(19.8 if with_costs else 0)
    assert artifacts.snapshots[0].cash == artifacts.snapshots[0].equity == 1000.0
    scoped_metrics = {
        (metric.scope, metric.scope_label, metric.metric_id): metric.value
        for metric in run.result.metrics
    }
    # 뒤 창은 월요일 첫 점 대신 금요일 자산1081(비용0:1100)을 분모로 삼아 경계 비용을 포함한다.
    before_return = 0.081 if with_costs else 0.1
    after_return = 1141.3 / 1081.0 - 1.0 if with_costs else 0.1
    full_return = 0.1413 if with_costs else 0.21
    assert scoped_metrics[(MetricScope.FULL, None, "total_return")] == pytest.approx(full_return)
    assert scoped_metrics[(MetricScope.WINDOW, "before", "total_return")] == pytest.approx(
        before_return
    )
    assert scoped_metrics[(MetricScope.WINDOW, "after", "total_return")] == pytest.approx(
        after_return
    )
    actual_before = scoped_metrics[(MetricScope.WINDOW, "before", "total_return")]
    actual_after = scoped_metrics[(MetricScope.WINDOW, "after", "total_return")]
    actual_full = scoped_metrics[(MetricScope.FULL, None, "total_return")]
    assert actual_before is not None
    assert actual_after is not None
    assert actual_full is not None
    assert (1 + actual_before) * (1 + actual_after) == pytest.approx(1 + actual_full)
    for label, fees_krw, taxes_krw in (
        ("before", 9.0 if with_costs else 0.0, 0.0),
        ("after", 19.9 if with_costs else 0.0, 19.8 if with_costs else 0.0),
    ):
        assert scoped_metrics[(MetricScope.WINDOW, label, "total_fees")] == pytest.approx(fees_krw)
        assert scoped_metrics[(MetricScope.WINDOW, label, "total_taxes")] == pytest.approx(
            taxes_krw
        )


@pytest.mark.parametrize("core", CORES)
@pytest.mark.parametrize("empty_execution_session", [False, True])
def test_day_remainder_expires_and_new_target_uses_actual_holdings(
    core: ExecutionCore,
    empty_execution_session: bool,
) -> None:
    """01-05/08 보고 경계: 금요일 DAY 잔량은 월요일 재체결되지 않는다."""
    sessions = SESSIONS[:6]
    run = run_account_scenario(
        SyntheticAccountScenario(
            sessions=sessions,
            bars=tuple(
                account_bar(session, A, 100.0, 100.0, 20 if index == 2 else 10_000)
                for index, session in enumerate(sessions)
                if not (empty_execution_session and index == 2)
            ),
            observations=account_observations(sessions, (((A, 1.0),),) * len(sessions)),
            memberships=(UniverseMembershipRecord(A, sessions[0], sessions[-1]),),
        ),
        core,
    )
    artifacts = run.result.artifacts
    first_quantity = 0 if empty_execution_session else 2
    # 20주 거래량×참여율10%=2주; 금요일 잔량8주는 소멸하고 월요일 새 목표는 실제2주를 뺀다.
    # 모든 bar가 없는 금요일에는 0주 체결·현금1000 유지, 월요일 새 목표만10주를 주문한다.
    assert [(order.session, order.quantity, order.time_in_force) for order in artifacts.orders] == [
        (sessions[1], "10", "day"),
        (sessions[3], str(10 - first_quantity), "day"),
    ]
    expected_fills = ([] if empty_execution_session else [(sessions[2], "2")]) + [
        (sessions[4], str(10 - first_quantity))
    ]
    assert [(fill.session, fill.quantity) for fill in artifacts.fills] == expected_fills
    assert [snapshot.cash for snapshot in artifacts.snapshots] == pytest.approx(
        [1000, 1000, 1000 - first_quantity * 100, 1000 - first_quantity * 100, 0, 0]
    )
    assert [snapshot.equity for snapshot in artifacts.snapshots] == pytest.approx([1000] * 6)
    assert artifacts.order_updates is not None
    first_order = artifacts.orders[0]
    assert [
        (update.session, update.status)
        for update in artifacts.order_updates
        if update.order_id == first_order.order_id
    ][-1] == (sessions[2], "cancelled")
    assert [(position.session, position.quantity) for position in artifacts.positions] == [
        *([] if empty_execution_session else [(sessions[2], "2"), (sessions[3], "2")]),
        (sessions[4], "10"),
        (sessions[5], "10"),
    ]
