"""홈 "DB 새로고침": 진행 중 작업이 있으면 시작하지 않고, 판본이 일부만 바뀌면 반영하지 않는다."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from strategy_workbench.application.ledger_refresh.facade.refresh import (
    REMOTE_CHECK_TTL,
    ActiveWork,
    LedgerRefreshService,
    RefreshCode,
    RefreshFailure,
    RefreshRejectedError,
    RemoteState,
    SyncFailureKind,
    SyncLock,
    SyncOutcome,
    SyncPlan,
    SyncProgress,
    SyncStage,
    UnavailableReason,
    classify,
)

NOW = datetime(2026, 11, 5, 3, 0, tzinfo=UTC)
OK = SyncOutcome(0, None, True, 0, False, False, False)
DRIFTED = SyncOutcome(3, None, True, 0, False, False, True)


@dataclass
class FakeJob:
    outcome: SyncOutcome
    seen_blocking: list[bool]
    service: LedgerRefreshService | None = None

    def progress(self) -> SyncProgress | None:
        return SyncProgress(SyncStage.CATALOG, NOW, 3, 3, 100, 100)

    def wait(self, timeout_s: float) -> SyncOutcome | None:
        if self.service is not None:
            self.seen_blocking.append(self.service.blocks_new_work())
        return self.outcome

    def cancel(self) -> None:
        return None


@dataclass
class FakeSync:
    outcomes: list[SyncOutcome] = field(default_factory=lambda: [OK])
    plan_result: SyncPlan = SyncPlan(new_tables=3, download_bytes=1_000, in_build_window=False)
    held: SyncLock | None = None
    started: int = 0
    seen_blocking: list[bool] = field(default_factory=list)
    service: LedgerRefreshService | None = None

    def lock(self) -> SyncLock | None:
        return self.held

    def plan(self) -> SyncPlan:
        return self.plan_result

    def start(self) -> FakeJob:
        self.started += 1
        return FakeJob(self.outcomes.pop(0), self.seen_blocking, self.service)


@dataclass
class FakeWork:
    work: ActiveWork = ActiveWork(0, 0, 0)

    def active(self) -> ActiveWork:
        return self.work


@dataclass
class FakeApply:
    applied: int = 0
    fail: bool = False

    def apply(self) -> None:
        self.applied += 1
        if self.fail:
            raise RuntimeError("container build failed")


@dataclass
class Clock:
    now: datetime = NOW

    def __call__(self) -> datetime:
        return self.now


def _service(
    sync: FakeSync | None = None,
    work: FakeWork | None = None,
    apply: FakeApply | None = None,
    clock: Clock | None = None,
) -> tuple[LedgerRefreshService, FakeSync, FakeApply]:
    sync = sync or FakeSync()
    apply = apply or FakeApply()
    service = LedgerRefreshService(
        sync, work or FakeWork(), apply, now=clock or Clock(), background=lambda run: run()
    )
    sync.service = service
    return service, sync, apply


def test_unavailable_never_starts_and_says_why() -> None:
    service = LedgerRefreshService(
        None, FakeWork(), FakeApply(), unavailable=UnavailableReason.NOT_CONFIGURED
    )
    status = service.status()
    assert (status.code, status.reason, status.can_start) == (
        RefreshCode.UNAVAILABLE,
        UnavailableReason.NOT_CONFIGURED,
        False,
    )
    assert "QL_SYNC_HOST" in status.message
    with pytest.raises(RefreshRejectedError):
        service.start()


def test_check_reads_the_remote_plan_and_asks_again_after_the_ttl() -> None:
    clock = Clock()
    service, sync, _ = _service(clock=clock)
    assert service.status().needs_check
    checked = service.check()
    assert (checked.remote.state, checked.remote.new_tables, checked.needs_check) == (
        RemoteState.BEHIND,
        3,
        False,
    )
    sync.plan_result = SyncPlan(0, 0, in_build_window=True)
    assert service.check().remote.state is RemoteState.UP_TO_DATE
    assert "서버가 지금 데이터를 만드는 중" in service.status().message
    clock.now = NOW + REMOTE_CHECK_TTL + timedelta(seconds=1)
    assert service.status().needs_check
    sync.plan_result = SyncPlan(0, 0, False, failure=SyncFailureKind.CONNECT)
    assert service.check().remote.state is RemoteState.UNKNOWN


def test_active_work_blocks_the_start_and_names_what_is_running() -> None:
    service, sync, _ = _service(
        work=FakeWork(ActiveWork(backtests=1, experiments=2, evaluations=0))
    )
    status = service.status()
    assert (status.code, status.can_start) == (RefreshCode.BUSY_WORK, False)
    assert "백테스트 1건·실험 2개" in status.message
    with pytest.raises(RefreshRejectedError):
        service.start()
    assert sync.started == 0


def test_another_sync_holding_the_lock_blocks_the_start() -> None:
    service, sync, _ = _service(sync=FakeSync(held=SyncLock(started_at=NOW, stale=True)))
    status = service.status()
    assert (status.code, status.can_start) == (RefreshCode.LOCKED, False)
    assert "멈춘 것 같아요" in status.message
    with pytest.raises(RefreshRejectedError):
        service.start()


def test_a_clean_sync_blocks_new_work_then_applies_and_reports_up_to_date() -> None:
    service, sync, apply = _service()
    service.start()
    assert sync.seen_blocking == [True]  # 받는 동안 새 계산을 막는다
    status = service.status()
    assert (status.code, apply.applied, status.can_start) == (RefreshCode.SUCCEEDED, 1, True)
    assert status.remote.state is RemoteState.UP_TO_DATE
    assert status.progress is not None and status.progress.stage is SyncStage.CATALOG
    assert not service.blocks_new_work()


def test_a_drifted_sync_retries_once_then_applies_with_a_note() -> None:
    service, sync, apply = _service(sync=FakeSync(outcomes=[DRIFTED, DRIFTED]))
    service.start()
    assert (sync.started, apply.applied) == (2, 1)
    assert "다음 받기에서 맞춰져요" in service.status().message
    service, sync, apply = _service(sync=FakeSync(outcomes=[DRIFTED, OK]))
    service.start()
    assert sync.started == 2 and "다음 받기" not in service.status().message


@pytest.mark.parametrize(
    ("outcome", "failure", "code"),
    [
        (
            SyncOutcome(2, SyncFailureKind.CONNECT, False, 0, False, False, False),
            RefreshFailure.CONNECT,
            RefreshCode.FAILED,
        ),
        (
            SyncOutcome(2, None, True, 2, False, False, False),
            RefreshFailure.TABLES_FAILED,
            RefreshCode.PARTIAL,
        ),
        (
            SyncOutcome(2, None, True, 0, True, False, False),
            RefreshFailure.CATALOG,
            RefreshCode.PARTIAL,
        ),
        (
            SyncOutcome(4, None, True, 0, False, True, False),
            RefreshFailure.MISMATCH,
            RefreshCode.FAILED,
        ),
        (
            SyncOutcome(2, SyncFailureKind.TRANSFER, True, 0, False, False, False),
            RefreshFailure.UNVERIFIED,
            RefreshCode.FAILED,
        ),
    ],
)
def test_a_sync_that_did_not_finish_cleanly_is_never_applied(
    outcome: SyncOutcome, failure: RefreshFailure, code: RefreshCode
) -> None:
    assert classify(outcome) is failure
    service, _, apply = _service(sync=FakeSync(outcomes=[outcome]))
    service.start()
    status = service.status()
    assert (status.code, status.failure, apply.applied) == (code, failure, 0)
    assert status.can_start  # 다시 받기를 누를 수 있다


def test_a_failed_apply_reports_a_restart_and_keeps_serving() -> None:
    service, _, apply = _service(apply=FakeApply(fail=True))
    service.start()
    status = service.status()
    assert (status.code, status.failure, apply.applied) == (
        RefreshCode.FAILED,
        RefreshFailure.APPLY,
        1,
    )
    assert "서버를 다시 시작해 주세요" in status.message
