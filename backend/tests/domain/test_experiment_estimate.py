"""실험 시간 어림(로드맵 M6) — 근거 실행 시간을 구간 길이로 늘려 실행마다 더한다.

#519 리뷰 B1·N1 이 정한 모델이다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from strategy_workbench.domain.backtest.facade.runs import RunStatus
from strategy_workbench.domain.backtest.facade.trials import (
    TrialLedger,
    TrialLedgerEntry,
    summarize_trial_ledger,
)
from strategy_workbench.domain.experiment.facade.design import (
    RECENT_RUNS,
    ExperimentDesign,
    ExperimentKind,
    TimeEstimate,
    WalkForwardWindow,
    build_search_spec,
    estimate_time,
    planned_spans,
)
from strategy_workbench.domain.strategy.facade.specification import IntegerParameter

T0 = datetime(2026, 10, 1, tzinfo=UTC)
START = date(2021, 1, 1)
FAST = IntegerParameter(
    parameter_id="fast", default=10, minimum=10, maximum=30, kind="integer", step=10
)


def _entry(
    index: int,
    seconds: float | None,
    days: int | None = 100,
    *,
    key: int | None = None,
    status: RunStatus = RunStatus.COMPLETED,
) -> TrialLedgerEntry:
    """접수 순서 `index` 의 실행. 접수 1분 뒤 시작해 `seconds` 동안 `days` 일 구간을 돈다."""
    created = T0 + timedelta(hours=index)
    started = None if seconds is None else created + timedelta(minutes=1)
    return TrialLedgerEntry(
        run_id=f"run-{index}",
        trial_key=f"{index if key is None else key:064x}",
        status=status,
        created_at=created,
        updated_at=(started or created) + timedelta(seconds=seconds or 0),
        started_at=started,
        start=None if days is None else START,
        end=None if days is None else START + timedelta(days=days - 1),
    )


def _ledger(*entries: TrialLedgerEntry) -> TrialLedger:
    return summarize_trial_ledger("s1", (), entries, ())


def test_a_longer_run_scales_up_and_a_shorter_one_does_not_scale_down() -> None:
    ledger = _ledger(_entry(0, 60.0, 365))
    estimate = estimate_time(ledger, [730, 365, 100])
    assert estimate is not None
    # 대기(접수~시작)는 시간에 들지 않는다. 짧은 구간도 근거 실행 시간 아래로 줄이지 않는다.
    assert (estimate.total_seconds, estimate.sample_count) == (120.0 + 60.0 + 60.0, 1)


def test_each_run_takes_the_longest_scaled_sample() -> None:
    ledger = _ledger(_entry(0, 10.0, 100), _entry(1, 30.0, 1000))
    # 1000일: 10초 × 10 = 100초 > 30초. 50일: 줄이지 않아 10초 < 30초.
    assert estimate_time(ledger, [1000, 50]) == TimeEstimate(
        total_seconds=100.0 + 30.0, sample_count=2
    )


def test_only_the_most_recently_accepted_runs_are_samples() -> None:
    # 가장 오래된 400초 실행은 원장에서 뒤 시도(키 99)에 묶여 있어도 접수 순으로 최근 다섯 밖이다.
    recent = [_entry(i, float(i), key=i) for i in range(1, RECENT_RUNS + 1)]
    ledger = _ledger(*recent, _entry(0, 400.0, key=99))
    estimate = estimate_time(ledger, [100])
    assert estimate is not None
    assert (estimate.total_seconds, estimate.sample_count) == (float(RECENT_RUNS), RECENT_RUNS)


def test_runs_without_time_or_period_are_not_samples() -> None:
    ledger = _ledger(
        _entry(0, None),
        _entry(1, 40.0, None),
        _entry(2, 40.0, status=RunStatus.FAILED),
    )
    assert estimate_time(ledger, [100]) is None


def test_planned_spans_cover_training_trials_and_validation_runs() -> None:
    window = WalkForwardWindow(
        date(2021, 1, 1), date(2021, 1, 10), date(2021, 1, 11), date(2021, 1, 15)
    )
    search = build_search_spec((FAST,), {"fast": None})
    walk = ExperimentDesign(search, {"fast": 10}, (window, window), measured=True)
    sweep = ExperimentDesign(
        search, {"fast": 10}, (), kind=ExperimentKind.CAPACITY_SWEEP, initial_cash=(1e8, 1e9)
    )

    # 칸 3개 × 창 2개 학습(10일) + 창마다 검증(5일). 용량 스윕은 금액마다 기반 구간 전체다.
    assert planned_spans(walk, START, date(2021, 12, 31)) == (10,) * 6 + (5,) * 2
    assert planned_spans(sweep, START, date(2021, 12, 31)) == (365, 365)
