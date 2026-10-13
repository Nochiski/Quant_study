"""실험 감사 기록(로드맵 M6) — experiment·trial·attempt 에 일어난 일을 시각 순으로 편다.

새로 적는 것은 조작 이력(`ControlChange`)뿐이다. 나머지는 지우지 않고 쌓는 기록(실험 생성·취소 시각,
attempt, 창 선택, 후보 선택)과 실행 상태(시작·끝 시각)에서 읽는다 — 같은 사실을 두 곳에 적지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from strategy_workbench.domain.backtest.facade.runs import BacktestRunState, RunStatus

from .ports.outgoing.experiment_repository import (
    ControlChange,
    ExperimentRecord,
    ExperimentSelection,
    TrialAttempt,
    WindowPick,
)


class ExperimentAuditKind(StrEnum):
    CREATED = "created"
    # 일시정지·재개·우선순위를 바꿨다(바꾼 뒤 값).
    CONTROLS_CHANGED = "controls_changed"
    # trial attempt 가 실행을 받았다 / 접수가 거절됐다.
    TRIAL_SUBMITTED = "trial_submitted"
    TRIAL_REJECTED = "trial_rejected"
    # 창마다 학습 점수로 칸을 골랐다(고를 칸이 없거나 검증 실행 접수가 거절된 경우 포함).
    WINDOW_PICKED = "window_picked"
    # attempt·창 선택이 받은 실행이 시작했다 / 끝났다(끝난 상태·실패 코드).
    RUN_STARTED = "run_started"
    RUN_FINISHED = "run_finished"
    CANDIDATE_SELECTED = "candidate_selected"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ExperimentAuditEvent:
    occurred_at: datetime
    kind: ExperimentAuditKind
    trial_index: int | None = None
    window_index: int | None = None
    attempt: int | None = None
    run_id: str | None = None
    # 끝난 실행의 상태(`run_finished`).
    run_status: RunStatus | None = None
    # 접수 거절 코드(`AdmissionRejectionCode`) 또는 실행 실패 코드(`RunFailureCode`).
    code: str | None = None
    # 바꾼 뒤 조작(`controls_changed`).
    paused: bool | None = None
    priority: int | None = None
    # 후보를 고른 이유(`candidate_selected`).
    reason: str | None = None


def audit_trail(
    record: ExperimentRecord,
    attempts: Iterable[TrialAttempt],
    picks: Iterable[WindowPick],
    selections: Iterable[ExperimentSelection],
    changes: Iterable[ControlChange],
    runs: Mapping[str, BacktestRunState],
) -> tuple[ExperimentAuditEvent, ...]:
    """시각 순 감사 기록. 같은 시각이면 기록 종류 순(생성 → 조작 → attempt → 창 → 후보 →
    취소)이다."""
    events = [ExperimentAuditEvent(record.created_at, ExperimentAuditKind.CREATED)]
    events += (
        ExperimentAuditEvent(
            change.changed_at,
            ExperimentAuditKind.CONTROLS_CHANGED,
            paused=change.controls.paused,
            priority=change.controls.priority,
        )
        for change in changes
    )
    for attempt in attempts:
        kind = (
            ExperimentAuditKind.TRIAL_REJECTED
            if attempt.run_id is None
            else ExperimentAuditKind.TRIAL_SUBMITTED
        )
        events.append(
            ExperimentAuditEvent(
                attempt.created_at,
                kind,
                trial_index=attempt.trial_index,
                attempt=attempt.attempt,
                run_id=attempt.run_id,
                code=attempt.error_code,
            )
        )
        events += _run_events(runs, attempt.run_id, trial_index=attempt.trial_index)
    for pick in picks:
        events.append(
            ExperimentAuditEvent(
                pick.created_at,
                ExperimentAuditKind.WINDOW_PICKED,
                trial_index=pick.trial_index,
                window_index=pick.window_index,
                attempt=pick.attempt,
                run_id=pick.run_id,
                code=pick.error_code,
            )
        )
        events += _run_events(runs, pick.run_id, window_index=pick.window_index)
    events += (
        ExperimentAuditEvent(
            selection.selected_at,
            ExperimentAuditKind.CANDIDATE_SELECTED,
            trial_index=selection.trial_index,
            reason=selection.reason,
        )
        for selection in selections
    )
    if record.cancelled_at is not None:
        events.append(ExperimentAuditEvent(record.cancelled_at, ExperimentAuditKind.CANCELLED))
    # 정렬은 안정적이라 같은 시각이면 위에서 더한 순서가 남는다.
    return tuple(sorted(events, key=lambda event: event.occurred_at))


def _run_events(
    runs: Mapping[str, BacktestRunState],
    run_id: str | None,
    *,
    trial_index: int | None = None,
    window_index: int | None = None,
) -> list[ExperimentAuditEvent]:
    """실행의 시작·끝. 시작 시각은 연구 DB v6 이후 실행에만 있다."""
    run = runs.get(run_id) if run_id is not None else None
    if run is None:
        return []
    events: list[ExperimentAuditEvent] = []
    if run.started_at is not None:
        events.append(
            ExperimentAuditEvent(
                run.started_at,
                ExperimentAuditKind.RUN_STARTED,
                trial_index=trial_index,
                window_index=window_index,
                run_id=run.run_id,
            )
        )
    if run.status.is_terminal:
        events.append(
            ExperimentAuditEvent(
                run.updated_at,
                ExperimentAuditKind.RUN_FINISHED,
                trial_index=trial_index,
                window_index=window_index,
                run_id=run.run_id,
                run_status=run.status,
                code=run.error_code,
            )
        )
    return events
