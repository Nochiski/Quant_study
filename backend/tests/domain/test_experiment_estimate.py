"""실험 시간 어림(로드맵 M6) — 계열의 최근 완료 실행 시간 × 실행 묶음 수."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from strategy_workbench.domain.backtest.facade.runs import RunStatus
from strategy_workbench.domain.backtest.facade.trials import (
    TrialLedgerEntry,
    summarize_trial_ledger,
)
from strategy_workbench.domain.experiment.facade.design import (
    RECENT_RUNS,
    ExperimentDesign,
    WalkForwardWindow,
    build_search_spec,
    estimate_time,
)
from strategy_workbench.domain.strategy.facade.specification import IntegerParameter

T0 = datetime(2026, 10, 1, tzinfo=UTC)
FAST = IntegerParameter(
    parameter_id="fast", default=10, minimum=10, maximum=30, kind="integer", step=10
)
WINDOW = WalkForwardWindow(date(2021, 1, 4), date(2022, 1, 3), date(2022, 1, 4), date(2023, 1, 3))


def _entry(
    index: int, seconds: float | None, status: RunStatus = RunStatus.COMPLETED
) -> TrialLedgerEntry:
    """접수 순서 `index` 의 실행. 접수 1분 뒤 시작해 `seconds` 동안 돈다(None 이면 시작 기록
    없음)."""
    created = T0 + timedelta(hours=index)
    started = None if seconds is None else created + timedelta(minutes=1)
    return TrialLedgerEntry(
        run_id=f"run-{index}",
        trial_key=f"{index:064x}",
        status=status,
        created_at=created,
        updated_at=(started or created) + timedelta(seconds=seconds or 0),
        started_at=started,
    )


def _design(windows: int, *, measured: bool = True) -> ExperimentDesign:
    return ExperimentDesign(
        search=build_search_spec((FAST,), {"fast": None}),
        parameter_values={"fast": 10},
        windows=(WINDOW,) * windows,
        measured=measured,
    )


def test_the_longest_of_the_recent_runs_times_the_waves_of_each_phase() -> None:
    # 오래된 120초 실행은 최근 다섯 개 밖이라 빠진다. 대기(접수~시작)는 시간에 들지 않는다.
    entries = [_entry(0, 120.0), *(_entry(i, 10.0 * i) for i in range(1, RECENT_RUNS + 1))]
    ledger = summarize_trial_ledger("s1", (), entries, ())

    # 칸 3개 × 창 2개 = 학습 6회(슬롯 4개로 2묶음) + 창마다 검증 2회(1묶음).
    estimate = estimate_time(ledger, _design(2), slots=4)

    assert estimate is not None
    assert (estimate.run_seconds, estimate.sample_count, estimate.slots) == (50.0, 5, 4)
    assert estimate.total_seconds == 3 * 50.0


def test_a_design_without_walk_forward_has_no_validation_phase() -> None:
    ledger = summarize_trial_ledger("s1", (), [_entry(0, 30.0)], ())
    estimate = estimate_time(ledger, _design(2, measured=False), slots=4)
    assert estimate is not None and estimate.total_seconds == 2 * 30.0


def test_no_timed_completed_run_means_no_estimate() -> None:
    # 시작 기록이 없는 완료 실행·실패한 실행은 근거가 아니다.
    entries = [_entry(0, None), _entry(1, 40.0, RunStatus.FAILED)]
    ledger = summarize_trial_ledger("s1", (), entries, ())
    assert estimate_time(ledger, _design(1), slots=1) is None


def test_slots_must_be_positive() -> None:
    ledger = summarize_trial_ledger("s1", (), [_entry(0, 30.0)], ())
    with pytest.raises(ValueError, match="slots"):
        estimate_time(ledger, _design(1), slots=0)
