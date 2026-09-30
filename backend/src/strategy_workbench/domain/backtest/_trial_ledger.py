"""계열 시도 원장의 집계 — 실행을 시도 키로 묶고 계열 시도 수 N 을 센다(검증 랩 spec D2, V1-05).

- 시도 하나는 결과가 나온 실행이 있을 때만 N 에 한 번 든다. 결과가 나온 실행은 완료된 실행과
  파산(`backtest.run.equity_wiped_out`)으로 끝난 실행이다 — 파산도 연구자가 보고 버린 선택지다
  (#383). 처음 결과가 나온 실행이 대표이고 그 실행의 전체 구간 세션 샤프(연율화 전)가 대표
  샤프다(파산이면 없다). 끝난 순서로 고르므로 나중 결과가 대표를 바꾸지 않는다.
- 같은 시도에서 나중에 결과가 나온 실행은 재확인이고, 결과 없이 끝난 실행(시작 전 취소·파산
  아닌 실패·중단)은 N 에서 빠진다.
- 봉인 겹침으로 거절한 요청은 봉인 원장이 정본이고(spec D11) 여기서는 N 제외로 보이기만 한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from strategy_workbench.domain.analytics.facade.metrics import MetricScope, session_sharpe

from ._models import BacktestRunResult, RunFailureCode, RunStatus


class TrialRunRole(StrEnum):
    # 이 시도에서 처음 결과가 나온 실행. 시도를 N 에 한 번 세고 대표 샤프를 낸다.
    COUNTED = "counted"
    # 같은 시도에서 나중에 결과가 나온 재확인 실행. N 에 다시 세지 않는다.
    RECHECK = "recheck"
    # 아직 끝나지 않은 실행.
    PENDING = "pending"
    # 결과 없이 끝난 실행(시작 전 취소·파산 아닌 실패·재시작 중단). N 에서 빠진다.
    NO_RESULT = "no_result"


@dataclass(frozen=True)
class TrialLedgerEntry:
    """원장에 적힌 실행 하나. 세션 샤프·판본은 완료된 실행에만 있다."""

    run_id: str
    trial_key: str
    status: RunStatus
    created_at: datetime
    updated_at: datetime
    session_sharpe: float | None = None
    metric_registry_version: str | None = None
    # 실패한 실행의 분류. 파산이면 결과가 나온 실행으로 센다.
    error_code: RunFailureCode | None = None


@dataclass(frozen=True)
class BlockedTrialAttempt:
    """봉인 겹침으로 거절한 실행 요청(봉인 원장의 "차단한 시도"). 결과가 없어 N 에 들지 않는다."""

    blocked_at: datetime
    lineage_id: str | None
    trial_key: str
    spec_hash: str
    start: date


@dataclass(frozen=True)
class TrialRun:
    run_id: str
    status: RunStatus
    created_at: datetime
    role: TrialRunRole
    # 완료된 실행의 전체 구간 세션 샤프(연율화 전). 워크포워드 창 고르기의 학습 점수다.
    session_sharpe: float | None = None


@dataclass(frozen=True)
class TrialGroup:
    """같은 시도 키의 실행들. 대표 실행이 없으면 이 시도는 N 에 들지 않는다."""

    trial_key: str
    runs: tuple[TrialRun, ...]
    representative_run_id: str | None = None
    representative_sharpe: float | None = None
    metric_registry_version: str | None = None


@dataclass(frozen=True)
class TrialLedger:
    """계열 하나의 원장.

    `lineage_id` 는 합친 뒤 남은 계열이고 `merged_lineage_ids` 는 거기 합쳐진 계열이다.
    """

    lineage_id: str
    merged_lineage_ids: tuple[str, ...]
    trial_count: int
    trials: tuple[TrialGroup, ...]
    blocked: tuple[BlockedTrialAttempt, ...]


class TrialPreviewReason(StrEnum):
    # 결과가 나오면 계열에 없던 시도가 하나 는다.
    NEW_TRIAL = "new_trial"
    # 이미 센 시도의 재확인이라 N 이 그대로다.
    RECHECK = "recheck"
    # 계열이 없는 초안이라 N 에 들지 않는다.
    NO_LINEAGE = "no_lineage"


@dataclass(frozen=True)
class TrialPreview:
    """실행 전 미리 계산 — 이 요청이 결과를 내면 N 에 새로 드는가.

    계열이 없으면 N 에 들지 않는다.
    """

    lineage_id: str | None
    trial_key: str
    trial_count: int
    new_trial: bool
    trial_count_after: int
    reason: TrialPreviewReason


def summarize_trial_ledger(
    lineage_id: str,
    merged_lineage_ids: tuple[str, ...],
    entries: Sequence[TrialLedgerEntry],
    blocked: tuple[BlockedTrialAttempt, ...],
) -> TrialLedger:
    """접수 순 원장 행을 시도로 묶는다. 시도 순서는 그 시도의 첫 접수 순이다."""
    groups: dict[str, list[TrialLedgerEntry]] = {}
    for entry in entries:
        groups.setdefault(entry.trial_key, []).append(entry)
    trials = tuple(_group(key, members) for key, members in groups.items())
    return TrialLedger(
        lineage_id=lineage_id,
        merged_lineage_ids=merged_lineage_ids,
        trial_count=sum(trial.representative_run_id is not None for trial in trials),
        trials=trials,
        blocked=blocked,
    )


def preview_trial(ledger: TrialLedger | None, trial_key: str) -> TrialPreview:
    if ledger is None:
        return TrialPreview(None, trial_key, 0, False, 0, TrialPreviewReason.NO_LINEAGE)
    counted = {trial.trial_key for trial in ledger.trials if trial.representative_run_id}
    new_trial = trial_key not in counted
    return TrialPreview(
        ledger.lineage_id,
        trial_key,
        ledger.trial_count,
        new_trial,
        ledger.trial_count + new_trial,
        TrialPreviewReason.NEW_TRIAL if new_trial else TrialPreviewReason.RECHECK,
    )


def representative_sharpe(result: BacktestRunResult) -> float | None:
    """완료된 실행의 전체 구간 세션 샤프(연율화 전). 샤프가 비었으면 None."""
    sharpe = next(
        (
            metric.value
            for metric in result.metrics
            if metric.metric_id == "sharpe" and metric.scope is MetricScope.FULL
        ),
        None,
    )
    return None if sharpe is None else session_sharpe(sharpe, result.manifest.annualization_days)


def _has_result(entry: TrialLedgerEntry) -> bool:
    """완료됐거나 파산으로 끝난 실행 — 연구자가 결과를 본 실행이다."""
    return entry.status is RunStatus.COMPLETED or (
        entry.status is RunStatus.FAILED and entry.error_code == "backtest.run.equity_wiped_out"
    )


def _score(entry: TrialLedgerEntry) -> float | None:
    """실행의 세션 샤프. 완료된 실행만 있다 — 파산은 결과지만 샤프가 없고, 커밋 뒤 취소·완료 저장
    실패로 끝난 실행에 적힌 값은 세지 않은 결과라 뺀다."""
    return entry.session_sharpe if entry.status is RunStatus.COMPLETED else None


def _group(trial_key: str, entries: list[TrialLedgerEntry]) -> TrialGroup:
    # `min` 은 같은 값이면 앞(먼저 접수된) 행을 고른다.
    first = min(
        (entry for entry in entries if _has_result(entry)),
        key=lambda entry: entry.updated_at,
        default=None,
    )
    return TrialGroup(
        trial_key=trial_key,
        runs=tuple(
            TrialRun(
                entry.run_id,
                entry.status,
                entry.created_at,
                _role(entry, first),
                _score(entry),
            )
            for entry in entries
        ),
        representative_run_id=None if first is None else first.run_id,
        representative_sharpe=None if first is None else _score(first),
        metric_registry_version=None if first is None else first.metric_registry_version,
    )


def _role(entry: TrialLedgerEntry, first: TrialLedgerEntry | None) -> TrialRunRole:
    if entry is first:
        return TrialRunRole.COUNTED
    if _has_result(entry):
        return TrialRunRole.RECHECK
    if entry.status in (RunStatus.CANCELLED, RunStatus.FAILED):
        return TrialRunRole.NO_RESULT
    return TrialRunRole.PENDING
