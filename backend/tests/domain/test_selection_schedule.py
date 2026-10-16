"""달력과 후보를 손으로 정해 신호·적용 시점과 차단 의미를 검증한다."""

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime
from typing import cast

import pytest

from strategy_workbench.domain.experiment.facade.design import WalkForwardWindow
from strategy_workbench.domain.experiment.facade.selection_schedule import (
    SelectionSchedule,
    SelectionScheduleBlockReason,
    SelectionScheduleStatus,
    WindowSelection,
)


def day(number: int) -> date:
    return date(2026, 10, number)


def selection(
    *, index: int = 0, start: int = 5, end: int = 9, candidate: str | None = "candidate-a"
) -> WindowSelection:
    return WindowSelection(
        window_index=index,
        window=WalkForwardWindow(day(1), day(2), day(start), day(end)),
        candidate_id=candidate,
        known_as_of=day(2),
    )


def test_friday_training_and_weekend_boundary_wait_for_monday_close_then_tuesday_open() -> None:
    chosen = replace(selection(), window=WalkForwardWindow(day(1), day(2), day(3), day(9)))
    schedule = SelectionSchedule(selections=(chosen,), sessions=(day(5), day(6), day(9)))
    assert schedule.status is SelectionScheduleStatus.READY
    assert schedule.transitions[0].signal_as_of == day(5)
    assert schedule.transitions[0].execution_on == day(6)
    assert schedule.candidate_at(day(5)) is None
    assert schedule.candidate_at(day(6)) == "candidate-a"
    assert schedule.candidate_at(day(9)) == "candidate-a"


@pytest.mark.parametrize("candidate,changed", [("candidate-a", False), ("candidate-b", True)])
def test_next_window_activates_only_at_execution(candidate: str, changed: bool) -> None:
    schedule = SelectionSchedule(
        selections=(selection(), selection(index=1, start=10, end=16, candidate=candidate)),
        sessions=(day(5), day(6), day(9), day(12), day(13), day(16)),
    )
    assert schedule.candidate_at(day(9)) == "candidate-a"
    assert schedule.candidate_at(day(12)) == "candidate-a"
    assert schedule.candidate_at(day(13)) == candidate
    assert schedule.transitions[1].candidate_changed is changed
    assert schedule.transitions[1].signal_as_of == day(12)
    assert schedule.transitions[1].execution_on == day(13)


@pytest.mark.parametrize("empty_index", [0, 1])
def test_no_cell_blocks_entire_schedule_without_losing_frozen_choices(empty_index: int) -> None:
    selections = (selection(), selection(index=1, start=10, end=16))
    selections = tuple(
        replace(item, candidate_id=None) if index == empty_index else item
        for index, item in enumerate(selections)
    )
    schedule = SelectionSchedule(selections=selections, sessions=(day(5), day(6), day(12), day(13)))
    assert schedule.status is SelectionScheduleStatus.BLOCKED
    assert schedule.transitions == ()
    assert schedule.selections == selections
    assert schedule.blocks[0].window_index == empty_index
    assert schedule.blocks[0].reason is SelectionScheduleBlockReason.NO_CELL
    with pytest.raises(ValueError, match="blocked"):
        schedule.candidate_at(day(6))


@pytest.mark.parametrize(
    "known,sessions,reason",
    [
        (10, (5, 6, 9), SelectionScheduleBlockReason.SELECTION_TOO_LATE),
        (9, (5, 6, 9), SelectionScheduleBlockReason.NO_EXECUTABLE_SESSION),
        (2, (9,), SelectionScheduleBlockReason.NO_EXECUTABLE_SESSION),
        (2, (), SelectionScheduleBlockReason.NO_EXECUTABLE_SESSION),
    ],
)
def test_expected_unavailable_cases_are_explicit_results(
    known: int, sessions: tuple[int, ...], reason: SelectionScheduleBlockReason
) -> None:
    schedule = SelectionSchedule(
        selections=(replace(selection(), known_as_of=day(known)),),
        sessions=tuple(day(number) for number in sessions),
    )
    assert schedule.status is SelectionScheduleStatus.BLOCKED
    assert schedule.blocks[0].reason is reason
    assert not schedule.transitions
    assert len(schedule.schedule_hash) == 64


def test_late_but_usable_selection_uses_its_close_and_next_open() -> None:
    schedule = SelectionSchedule(
        selections=(replace(selection(), known_as_of=day(6)),),
        sessions=(day(5), day(6), day(9)),
    )
    assert schedule.candidate_at(day(6)) is None
    assert schedule.candidate_at(day(9)) == "candidate-a"


@pytest.mark.parametrize("sessions", [(6, 5), (5, 5), (2, 5, 6), (5, 6, 12)])
def test_calendar_is_neither_sorted_deduplicated_nor_silently_clipped(
    sessions: tuple[int, ...],
) -> None:
    with pytest.raises(ValueError, match="sessions"):
        SelectionSchedule(selections=(selection(),), sessions=tuple(day(n) for n in sessions))


@pytest.mark.parametrize("start", [9, 11])
def test_test_window_overlap_or_gap_is_rejected(start: int) -> None:
    with pytest.raises(ValueError, match="adjacent"):
        SelectionSchedule(
            selections=(selection(), selection(index=1, start=start, end=16)),
            sessions=(day(5), day(6), day(12), day(13)),
        )


@pytest.mark.parametrize("candidate", ["", " ", "candidate a", "candidate-a\n"])
def test_candidate_identity_cannot_be_blank_or_contain_whitespace(candidate: str) -> None:
    with pytest.raises(ValueError, match="candidate_id"):
        SelectionSchedule(selections=(selection(candidate=candidate),), sessions=(day(5), day(6)))


def test_known_before_training_and_overlapping_training_are_rejected() -> None:
    with pytest.raises(ValueError, match="before training"):
        SelectionSchedule(
            selections=(replace(selection(), known_as_of=day(1)),), sessions=(day(5), day(6))
        )
    with pytest.raises(ValueError, match="boundaries"):
        SelectionSchedule(
            selections=(
                replace(selection(), window=WalkForwardWindow(day(1), day(5), day(5), day(9))),
            ),
            sessions=(day(5), day(6)),
        )


def test_indices_preserve_order_and_datetime_is_not_a_close_date() -> None:
    with pytest.raises(ValueError, match="indices"):
        SelectionSchedule(selections=(selection(index=1),), sessions=(day(5), day(6)))
    with pytest.raises(ValueError, match="requires a date"):
        SelectionSchedule(
            selections=(replace(selection(), known_as_of=datetime(2026, 10, 2, 12)),),
            sessions=(day(5), day(6)),
        )


def test_identity_is_stable_including_blocked_and_changes_with_semantic_inputs() -> None:
    base = SelectionSchedule(selections=(selection(),), sessions=(day(5), day(6), day(9)))
    same = replace(base)
    assert same.schedule_hash == base.schedule_hash
    variants = (
        replace(base, selections=(selection(candidate="candidate-b"),)),
        replace(base, selections=(replace(selection(), known_as_of=day(3)),)),
        replace(base, selections=(selection(end=8),), sessions=(day(5), day(6))),
        replace(base, sessions=(day(5), day(6))),
        replace(base, selections=(selection(candidate=None),)),
    )
    assert len({base.schedule_hash, *(item.schedule_hash for item in variants)}) == 6
    blocked = variants[-1]
    assert replace(blocked).schedule_hash == blocked.schedule_hash
    assert (base.policy_version, base.execution_timing, base.time_in_force, base.target_scope) == (
        "selection-schedule-v1",
        "next_open",
        "day",
        "replace",
    )
    attribute_name = "status"
    with pytest.raises(FrozenInstanceError):
        setattr(base, attribute_name, SelectionScheduleStatus.BLOCKED)
    with pytest.raises(ValueError, match="init=False"):
        replace(base, schedule_hash="forged")


def test_active_query_requires_a_measurement_session() -> None:
    schedule = SelectionSchedule(selections=(selection(),), sessions=(day(5), day(6), day(9)))
    for session in (day(4), day(7), day(10)):
        with pytest.raises(ValueError, match="outside"):
            schedule.candidate_at(session)


@pytest.mark.parametrize("items", [[], (), [selection()], ({"candidate_id": "a"},)])
def test_selection_container_and_elements_must_be_immutable_values(items: object) -> None:
    with pytest.raises(ValueError, match="immutable"):
        SelectionSchedule(
            selections=cast(tuple[WindowSelection, ...], items), sessions=(day(5), day(6))
        )


def test_window_and_calendar_must_have_the_declared_immutable_types() -> None:
    with pytest.raises(ValueError, match="WalkForwardWindow"):
        SelectionSchedule(
            selections=(replace(selection(), window=cast(WalkForwardWindow, {})),),
            sessions=(day(5), day(6)),
        )
    with pytest.raises(ValueError, match="immutable sessions"):
        SelectionSchedule(
            selections=(selection(),), sessions=cast(tuple[date, ...], [day(5), day(6)])
        )
