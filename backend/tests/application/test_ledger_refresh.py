"""홈 "DB 새로고침": 진행 중 작업이 있으면 시작하지 않고, 판본이 일부만 바뀌면 반영하지 않는다."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from strategy_workbench.application.ledger_refresh.facade.refresh import (
    REMOTE_CHECK_TTL,
    ActiveWork,
    DataRefreshingError,
    LedgerRefreshService,
    RefreshCode,
    RefreshFailure,
    RefreshOutcome,
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
# (failure, tables_failed, catalog_rebuilt, catalog_failed, verify_mismatch, drifted)
OK = SyncOutcome(None, 0, True, False, False, False)
# 받는 도중 서버 판이 바뀌었다 — 그 차이(서버가 앞섬)는 어댑터가 불일치에서 뺀다
DRIFTED = SyncOutcome(None, 0, True, False, False, True)


@dataclass
class FakeJob:
    outcome: SyncOutcome
    seen_blocking: list[bool]
    service: LedgerRefreshService | None = None

    def progress(self) -> SyncProgress | None:
        return SyncProgress(SyncStage.CATALOG, NOW, 3, 3, 100, 100)

    def wait(self, timeout_s: float) -> SyncOutcome | None:
        if self.service is not None:
            self.seen_blocking.append(self.service.status().refreshing)
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
    service = LedgerRefreshService(UnavailableReason.NOT_CONFIGURED, FakeWork(), FakeApply())
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
    assert "일시정지한 실험이 있으면" in status.message  # 멈춘 실험도 새로고침을 막는다
    with pytest.raises(RefreshRejectedError):
        service.start()
    assert sync.started == 0


def test_another_sync_holding_the_lock_blocks_the_start_even_when_stale() -> None:
    service, sync, _ = _service(sync=FakeSync(held=SyncLock(started_at=NOW, stale=False)))
    status = service.status()
    assert (status.code, status.can_start) == (RefreshCode.LOCKED, False)
    with pytest.raises(RefreshRejectedError):
        service.start()
    # 낡은 잠금도 막는다(도구는 살아 있는 실행의 낡은 잠금도 치워 두 받기가 겹칠 수 있다)
    sync.held = SyncLock(started_at=NOW, stale=True)
    stale = service.status()
    assert (stale.code, stale.can_start) == (RefreshCode.LOCKED, False)
    assert "데이터 받기" in stale.message


def test_a_clean_sync_blocks_new_work_then_applies_and_reports_up_to_date() -> None:
    service, sync, apply = _service()
    assert service.status().last_outcome is None
    service.start()
    assert sync.seen_blocking == [True]  # 받는 동안 새 계산을 막는다
    status = service.status()
    assert (status.code, apply.applied, status.can_start) == (RefreshCode.SUCCEEDED, 1, True)
    assert status.remote.state is RemoteState.UP_TO_DATE
    assert status.progress is not None and status.progress.stage is SyncStage.CATALOG
    assert (status.refreshing, status.last_outcome) == (False, RefreshOutcome.SUCCEEDED)


def test_a_drifted_sync_applies_once_with_a_note_and_asks_the_server_again() -> None:
    # 다시 받지 않는다 — 다시 받으면 gc 가 옛 앱이 고정한 판을 지울 수 있다
    service, sync, apply = _service(sync=FakeSync(outcomes=[DRIFTED]))
    service.start()
    status = service.status()
    assert (sync.started, apply.applied, status.code) == (1, 1, RefreshCode.SUCCEEDED)
    assert "다음 받기에서 맞춰져요" in status.message
    assert (status.remote.state, status.needs_check) == (RemoteState.UNKNOWN, True)


@pytest.mark.parametrize(
    ("outcome", "failure", "code"),
    [
        (
            SyncOutcome(SyncFailureKind.CONNECT, 0, False, False, False, False),
            RefreshFailure.CONNECT,
            RefreshCode.FAILED,
        ),
        # 도구가 기록을 남기지 못했다
        (
            SyncOutcome(SyncFailureKind.UNEXPECTED, 0, False, False, False, False),
            RefreshFailure.UNEXPECTED,
            RefreshCode.FAILED,
        ),
        (
            SyncOutcome(None, 2, False, False, False, False),
            RefreshFailure.TABLES_FAILED,
            RefreshCode.PARTIAL,
        ),
        (
            SyncOutcome(None, 0, False, True, False, False),
            RefreshFailure.CATALOG,
            RefreshCode.PARTIAL,
        ),
        (
            SyncOutcome(None, 0, True, False, True, False),
            RefreshFailure.MISMATCH,
            RefreshCode.FAILED,
        ),
        # 카탈로그는 다시 만들었는데 확인하기 도중 끊겼다
        (
            SyncOutcome(SyncFailureKind.TRANSFER, 0, True, False, False, True),
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


def test_work_admitted_before_the_start_blocks_it_and_work_after_it_is_refused() -> None:
    service, sync, _ = _service(sync=FakeSync(outcomes=[OK]))
    with service.admit_work():
        # 접수 중인 계산이 있으면 받기를 시작하지 않는다 — 판정과 접수 사이에 끼어들지 않게
        with pytest.raises(RefreshRejectedError) as rejected:
            service.start()
        assert (rejected.value.status.code, sync.started) == (RefreshCode.BUSY_WORK, 0)
    held: list[bool] = []

    def run_later(run: Callable[[], None]) -> None:
        # 받는 중에 들어온 계산 시작은 자리를 쥐지 못한다
        with pytest.raises(DataRefreshingError):
            with service.admit_work():
                pass
        held.append(True)
        run()

    service._background = run_later  # noqa: SLF001  # reason: 받는 중의 한 순간을 붙잡는다
    service.start()
    assert held == [True] and service.status().code is RefreshCode.SUCCEEDED
    with service.admit_work():  # 끝나면 다시 받는다
        pass


@dataclass
class HeldJob:
    """`cancel` 이 오기 전까지 끝나지 않는 받기."""

    cancelled: threading.Event = field(default_factory=threading.Event)

    def progress(self) -> SyncProgress | None:
        return None

    def wait(self, timeout_s: float) -> SyncOutcome | None:
        if not self.cancelled.wait(timeout_s):
            return None
        return SyncOutcome(SyncFailureKind.UNEXPECTED, 0, False, False, False, False)

    def cancel(self) -> None:
        self.cancelled.set()


def test_stopping_the_server_cancels_the_running_sync() -> None:
    job = HeldJob()
    sync = FakeSync()
    sync.start = lambda: job  # pyright: ignore[reportAttributeAccessIssue]  # reason: 이 테스트만 멈추는 받기를 꽂는다
    threads: list[threading.Thread] = []

    def background(run: Callable[[], None]) -> None:
        threads.append(threading.Thread(target=run))
        threads[0].start()

    service = LedgerRefreshService(
        sync, FakeWork(), FakeApply(), now=Clock(), background=background
    )
    service.start()
    deadline = time.monotonic() + 10
    while service._job is None and time.monotonic() < deadline:  # noqa: SLF001  # reason: 받기가 도구를 띄운 순간을 기다린다
        time.sleep(0.01)
    service.stop()
    threads[0].join(10)
    assert job.cancelled.is_set()
    assert service.status().failure is RefreshFailure.UNEXPECTED  # 반영하지 않고 끝났다


@pytest.mark.parametrize("kind", list(SyncFailureKind))
def test_every_sync_failure_kind_has_a_refresh_failure(kind: SyncFailureKind) -> None:
    # 포트에 갈래를 더하고 결과 어휘에 빠뜨리면 "예상 밖"으로 접혀 원인이 사라진다
    assert classify(SyncOutcome(kind, 0, False, False, False, False)) == RefreshFailure(kind.value)


def test_a_partial_result_stays_partial_after_new_work_starts() -> None:
    work = FakeWork()
    service, _, _ = _service(
        sync=FakeSync(outcomes=[SyncOutcome(None, 2, False, False, False, False)]), work=work
    )
    service.start()
    work.work = ActiveWork(backtests=1, experiments=0, evaluations=0)
    status = service.status()
    # 지금 상태는 "진행 중인 백테스트"이지만 끝난 받기의 결과는 그대로 "일부만 받음"이다
    assert (status.code, status.last_outcome, status.failure) == (
        RefreshCode.BUSY_WORK,
        RefreshOutcome.PARTIAL,
        RefreshFailure.TABLES_FAILED,
    )
    # 결과 문장(다시 시작하지 말라는 경고)도 지금 상태 문장과 따로 남는다
    assert status.outcome_message is not None and "다시 시작하지 마세요" in status.outcome_message
    assert "다시 시작하지 마세요" not in status.message


@dataclass
class FlakyWork:
    """두 번째 세기(RUNNING 뒤)에서 실험 DB 읽기가 실패한다."""

    reads: int = 0

    def active(self) -> ActiveWork:
        self.reads += 1
        if self.reads == 2:
            raise RuntimeError("database is locked")
        return ActiveWork(0, 0, 0)


def test_a_failed_work_count_after_running_does_not_leave_the_refresh_stuck() -> None:
    service, sync, _ = _service(work=FlakyWork())  # pyright: ignore[reportArgumentType]  # reason: ActiveWorkPort 대역
    with pytest.raises(RuntimeError):
        service.start()
    status = service.status()
    assert (status.refreshing, sync.started) == (False, 0)
    with service.admit_work():  # 계산 시작이 막혀 있지 않다
        pass


def test_a_stop_while_the_tool_is_starting_cancels_it_right_after() -> None:
    job = HeldJob()
    sync = FakeSync()
    service = LedgerRefreshService(
        sync, FakeWork(), FakeApply(), now=Clock(), background=lambda run: run()
    )

    def start_after_stop() -> HeldJob:
        service.stop()  # 도구를 띄우는 사이에 서버가 끝나기 시작했다
        return job

    sync.start = start_after_stop  # pyright: ignore[reportAttributeAccessIssue]  # reason: 띄우는 순간을 붙잡는다
    service.start()
    assert job.cancelled.is_set()


def test_local_mismatch_is_never_applied_even_when_the_server_also_moved_on() -> None:
    # 어댑터가 서버가 앞선 차이를 뺀 뒤에도 남은 불일치는 drift 와 함께 와도 반영하지 않는다
    outcome = SyncOutcome(None, 0, True, False, True, True)
    assert classify(outcome) is RefreshFailure.MISMATCH
