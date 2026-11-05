"""홈 "DB 새로고침": 원장을 받고(받기 → 정리하기 → 확인하기) 끝나면 앱에 반영한다.

판본을 섞지 않는 것이 이 유스케이스의 약속이다.

- 진행 중인 백테스트·실험·연속 평가가 있으면 시작하지 않는다. 계산을 시작하는 요청은 처리하는
  동안 `admit_work` 로 자리를 쥐고, 받거나 반영하는 동안에는 거절된다. 받기 시작은 쥔 자리가 없을
  때만 한다 — 같은 잠금 안에서 판정하므로, 판정과 접수 사이에 계산이 끼어들지 않는다. 그래서 앱을
  다시 세울 때 옛 앱이 쥔 계산이 없다.
- 앱 반영은 새 컨테이너로 통째 바꾸는 것이다(`ApplyDataPort`). 한 컨테이너 안에서 판본이 바뀌지
  않는다.
- 정리하기가 카탈로그를 다시 만들었으면 옛 앱은 정리된 데이터를 못 읽으므로, 확인하기가 판을
  막지 않는 한 반영한다(받을 새 판이 없었어도). 받기가 표 일부만 바꾸거나 정리하기가 실패하면
  반영하지 않는다. 옛 앱은 부팅 때 고정한 판본으로 계속 돈다. 그 상태로 서버를 다시 띄우면
  백테스트가 멈추므로 문장이 그렇게 말한다.

화면 문장은 여기서 한글로 완성해 코드와 함께 보낸다. 서버 주소·계정은 어디에도 싣지 않는다.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from strategy_workbench.application.ledger_refresh.ports.outgoing.ledger_sync import (
    ActiveWork,
    ActiveWorkPort,
    ApplyDataPort,
    LedgerSyncPort,
    SyncFailureKind,
    SyncJob,
    SyncOutcome,
    SyncProgress,
)

logger = logging.getLogger(__name__)

# 홈을 열 때 서버 판을 다시 확인할 간격. 확인은 원격 MANIFEST 만 읽어 싸지만 접속 한 번이다.
REMOTE_CHECK_TTL = timedelta(minutes=10)
_POLL_S = 1.0


class RefreshCode(StrEnum):
    UNAVAILABLE = "unavailable"
    LOCKED = "locked"
    BUSY_WORK = "busy_work"
    IDLE = "idle"
    RUNNING = "running"
    APPLYING = "applying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"


class UnavailableReason(StrEnum):
    PRACTICE_DATA = "practice_data"
    NOT_CONFIGURED = "not_configured"
    ROOT_MISMATCH = "root_mismatch"
    SYNC_TOOL_MISSING = "sync_tool_missing"


class RemoteState(StrEnum):
    BEHIND = "behind"
    UP_TO_DATE = "up_to_date"
    UNKNOWN = "unknown"


class RefreshFailure(StrEnum):
    """`failed`(아무것도 반영하지 않음)·`partial`(일부만 바뀜)의 갈래."""

    CONNECT = "connect"
    TRANSFER = "transfer"
    DISK = "disk"
    LOCKED = "locked"
    UNEXPECTED = "unexpected"
    MISMATCH = "mismatch"
    UNVERIFIED = "unverified"
    APPLY = "apply"
    TABLES_FAILED = "tables_failed"
    CATALOG = "catalog"


UNAVAILABLE_MESSAGES: dict[UnavailableReason, str] = {
    UnavailableReason.PRACTICE_DATA: "연습용 데이터로 켜져 있어 DB 새로고침을 쓸 수 없어요.",
    UnavailableReason.NOT_CONFIGURED: (
        "데이터 서버가 설정되지 않았어요. 환경 변수 QL_SYNC_HOST·QL_SYNC_USER를 설정한 뒤 "
        "새 터미널에서 앱을 다시 띄워 주세요."
    ),
    UnavailableReason.ROOT_MISMATCH: (
        "앱이 읽는 데이터 폴더(연구 보존본이거나 이름이 equity가 아닌 폴더)는 앱에서 새로 받을 수 "
        "없어요."
    ),
    UnavailableReason.SYNC_TOOL_MISSING: (
        "동기화 도구(uv와 저장소의 database 폴더)를 찾지 못해 앱에서 새로 받을 수 없어요."
    ),
}
FAILURE_MESSAGES: dict[RefreshFailure, str] = {
    RefreshFailure.CONNECT: (
        "데이터 서버에 접속하지 못했어요. 네트워크나 접속 키를 확인해 주세요. "
        "지금 데이터는 그대로 써요."
    ),
    RefreshFailure.TRANSFER: (
        "받는 도중 연결이 끊겼어요. 다시 시도해 주세요. 지금 데이터는 그대로 써요."
    ),
    RefreshFailure.DISK: (
        "디스크 공간이 모자라 받지 못했어요. 공간을 비운 뒤 다시 시도해 주세요. "
        "지금 데이터는 그대로 써요."
    ),
    RefreshFailure.LOCKED: (
        "다른 곳(예약 작업·터미널)에서 데이터를 받는 중이라 시작하지 못했어요. "
        "끝난 뒤 다시 시도해 주세요."
    ),
    RefreshFailure.UNEXPECTED: (
        "받기가 예상하지 못한 오류로 멈췄어요. 동기화 기록(_sync/logs)을 확인해 주세요."
    ),
    RefreshFailure.MISMATCH: (
        "받은 파일이 서버와 달라 앱에 반영하지 않았어요. 다시 받아 주세요. 다시 받기 전까지 "
        "재무·수정주가처럼 정리된 데이터를 읽는 계산은 멈춰요."
    ),
    RefreshFailure.UNVERIFIED: (
        "받기는 끝났지만 확인하기를 마치지 못해 앱에 반영하지 않았어요. 다시 받아 주세요. "
        "다시 받기 전까지 재무·수정주가처럼 정리된 데이터를 읽는 계산은 멈춰요."
    ),
    RefreshFailure.APPLY: ("새 데이터로 앱을 다시 세우지 못했어요. 서버를 다시 시작해 주세요."),
    RefreshFailure.TABLES_FAILED: (
        "데이터 일부만 받았어요. 다시 받아 주세요. 그 전까지 앱은 지금 데이터로 계속 돌아요. "
        "앱을 다시 시작하지 마세요 — 지금 상태로 다시 시작하면 백테스트가 멈춰요."
    ),
    RefreshFailure.CATALOG: (
        "새 데이터는 받았지만 정리하기(카탈로그)를 마치지 못해 앱에 반영하지 않았어요. 과거 값이 "
        "이전 판과 달라져 승인이 필요하거나 정리 중 오류가 났어요(사용 설명서 '데이터 받기'). "
        "그 전까지 앱은 지금 데이터로 계속 돌아요. 앱을 다시 시작하지 마세요 — 지금 상태로 다시 "
        "시작하면 백테스트가 멈춰요."
    ),
}
_PARTIAL = frozenset({RefreshFailure.TABLES_FAILED, RefreshFailure.CATALOG})
RUNNING_MESSAGE = "데이터를 받는 중이에요. 이 탭을 닫아도 계속돼요. 앱을 끄면 멈춰요."
DATA_REFRESHING_MESSAGE = (
    "데이터를 새로 받는 중이에요. 받기가 끝나고 앱에 반영된 뒤 다시 시작해 주세요."
)
ADMITTING_MESSAGE = "계산을 시작하는 요청을 처리하는 중이에요. 잠시 뒤 다시 시도해 주세요."
_BUSY = frozenset({RefreshCode.RUNNING, RefreshCode.APPLYING})
APPLYING_MESSAGE = "받은 데이터로 앱을 다시 세우는 중이에요. 잠시 뒤 저절로 다시 연결돼요."
SUCCEEDED_MESSAGE = "새 데이터를 앱에 반영했어요."
DRIFTED_NOTE = " 받는 동안 서버가 데이터를 또 만들어 일부는 다음 받기에서 맞춰져요."
BUILD_WINDOW_NOTE = (
    "서버가 지금 데이터를 만드는 중일 수 있어요. 받는 도중 바뀌면 일부는 다음 받기에서 맞춰져요."
)
REMOTE_MESSAGES: dict[RemoteState, str] = {
    RemoteState.BEHIND: "서버에 받을 새 데이터가 있어요.",
    RemoteState.UP_TO_DATE: "서버에 있는 최신 데이터와 같아요.",
    RemoteState.UNKNOWN: "서버에 새 데이터가 있는지 아직 확인하지 못했어요.",
}


@dataclass(frozen=True)
class RemoteCheck:
    state: RemoteState
    new_tables: int
    download_bytes: int
    in_build_window: bool
    checked_at: datetime | None


@dataclass(frozen=True)
class RefreshStatus:
    code: RefreshCode
    message: str
    can_start: bool
    # 받거나 반영하는 중인가 — 이 동안 계산 시작은 409 `data.refreshing` 으로 돌아간다.
    refreshing: bool
    reason: UnavailableReason | None
    failure: RefreshFailure | None
    remote: RemoteCheck
    needs_check: bool
    progress: SyncProgress | None
    active_work: ActiveWork | None
    started_at: datetime | None
    finished_at: datetime | None


class RefreshRejectedError(Exception):
    """지금은 시작할 수 없다. `status` 가 이유를 말한다."""

    def __init__(self, status: RefreshStatus) -> None:
        super().__init__(f"ledger refresh rejected — code={status.code} reason={status.reason}")
        self.status = status


class DataRefreshingError(Exception):
    """데이터를 받거나 반영하는 중이라 계산을 시작하지 않는다. 화면 문장은 `message` 다."""

    code = "data.refreshing"
    message = DATA_REFRESHING_MESSAGE

    def __init__(self) -> None:
        super().__init__("ledger refresh in progress — new work is refused until it is applied")


def classify(outcome: SyncOutcome) -> RefreshFailure | None:
    """끝난 sync 하나를 반영할지 가른다. None 이면 반영한다.

    카탈로그를 다시 만들었으면(받을 새 판이 없었어도 다시 만든다) 반영한다. 다만 확인하기를 마치지
    못했거나 받은 파일이 서버와 다르면 반영하지 않는다 — 확인하지 않은 판으로 앱을 다시 세우지
    않는다. 받는 도중 서버 판이 바뀐 차이(drifted)는 판이 틀린 것이 아니라 반영한다. 다시 받지 않는
    것은 그 받기가 옛 앱이 고정한 판을 지울 수 있어서다(`ledger_sync` gc). 카탈로그를 다시 만들기
    전에 멈췄으면 표 일부만 바뀌었거나(`partial`) 그 실패 갈래 그대로다.
    """
    if outcome.catalog_rebuilt:
        if outcome.failure is not None:
            return RefreshFailure.UNVERIFIED
        if outcome.verify_mismatch and not outcome.drifted:
            return RefreshFailure.MISMATCH
        return None
    if outcome.tables_failed:
        return RefreshFailure.TABLES_FAILED
    if outcome.catalog_failed:
        return RefreshFailure.CATALOG
    return RefreshFailure(outcome.failure or SyncFailureKind.UNEXPECTED)


@dataclass
class _State:
    code: RefreshCode = RefreshCode.IDLE
    failure: RefreshFailure | None = None
    drifted: bool = False
    progress: SyncProgress | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    remote: RemoteCheck = RemoteCheck(RemoteState.UNKNOWN, 0, 0, False, None)


def _start_daemon(target: Callable[[], None]) -> None:
    threading.Thread(target=target, name="ledger-refresh", daemon=True).start()


class LedgerRefreshService:
    """앱 프로세스 하나에 하나. 앱 반영(컨테이너 교체)을 넘어 살아 있다."""

    def __init__(
        self,
        sync: LedgerSyncPort | None,
        active_work: ActiveWorkPort,
        apply: ApplyDataPort,
        *,
        unavailable: UnavailableReason | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        background: Callable[[Callable[[], None]], None] = _start_daemon,
    ) -> None:
        if (sync is None) == (unavailable is None):
            raise ValueError(
                "ledger refresh needs exactly one of a sync port or an unavailable reason — "
                f"sync={'set' if sync else 'none'} unavailable={unavailable}"
            )
        self._sync = sync
        self._active_work = active_work
        self._apply = apply
        self._unavailable = unavailable
        self._now = now
        self._background = background
        self._lock = threading.Lock()
        self._state = _State()
        self._admitting = 0  # 처리 중인 계산 시작 요청 수
        self._job: SyncJob | None = None

    # ── 조회 ──────────────────────────────────────────────────────────────────

    def status(self) -> RefreshStatus:
        with self._lock:
            state = replace(self._state)
        return self._status(state)

    def blocks_new_work(self) -> bool:
        """받거나 반영하는 동안에는 새 계산을 시작하지 않는다(교체할 때 옛 앱이 쥔 계산이 없게)."""
        with self._lock:
            return self._state.code in _BUSY

    @contextmanager
    def admit_work(self) -> Iterator[None]:
        """계산을 시작하는 요청 하나를 처리하는 동안 자리를 쥔다.

        받거나 반영하는 중이면 `DataRefreshingError`. 요청이 계산을 접수하고 끝날 때까지 쥐므로,
        자리를 놓은 뒤에는 그 계산이 `ActiveWorkPort` 에 보인다.
        """
        with self._lock:
            if self._state.code in _BUSY:
                raise DataRefreshingError
            self._admitting += 1
        try:
            yield
        finally:
            with self._lock:
                self._admitting -= 1

    # ── 명령 ──────────────────────────────────────────────────────────────────

    def check(self) -> RefreshStatus:
        """서버 판을 다시 확인한다. 받는 중이면 확인하지 않고 지금 상태를 돌려준다."""
        if self._sync is None or self.blocks_new_work():
            return self.status()
        plan = self._sync.plan()
        if plan.failure is not None:
            state = RemoteState.UNKNOWN
        else:
            state = RemoteState.BEHIND if plan.new_tables > 0 else RemoteState.UP_TO_DATE
        remote = RemoteCheck(
            state, plan.new_tables, plan.download_bytes, plan.in_build_window, self._now()
        )
        with self._lock:
            self._state.remote = remote
        return self.status()

    def start(self) -> RefreshStatus:
        status = self.status()
        if not status.can_start or self._sync is None:
            raise RefreshRejectedError(status)
        with self._lock:
            refused = self._state.code in _BUSY or self._admitting > 0
            previous = self._state
            if not refused:
                self._state = replace(
                    previous,
                    code=RefreshCode.RUNNING,
                    failure=None,
                    drifted=False,
                    progress=None,
                    started_at=self._now(),
                    finished_at=None,
                )
        if refused:
            rejected = self._status(replace(previous))
            if previous.code not in _BUSY:  # 계산을 시작하는 요청이 처리되는 중이다
                rejected = replace(
                    rejected, code=RefreshCode.BUSY_WORK, can_start=False, message=ADMITTING_MESSAGE
                )
            raise RefreshRejectedError(rejected)
        # RUNNING 앞에 접수를 끝낸 계산을 센다. 이제 새 계산은 `admit_work` 에서 막힌다.
        if self._active_work.active().any:
            with self._lock:
                self._state = previous
            raise RefreshRejectedError(self.status())
        try:
            self._background(self._run)
        except BaseException:
            with self._lock:
                self._state = previous
            raise
        return self.status()

    def stop(self) -> None:
        """서버가 끝난다 — 받는 중이면 동기화 도구를 끊는다(앱을 끄면 받기도 멈춘다)."""
        with self._lock:
            job = self._job
        if job is not None:
            job.cancel()

    # ── 실행 ──────────────────────────────────────────────────────────────────

    def _run(self) -> None:
        sync = self._sync
        if sync is None:
            return
        applying = False
        try:
            outcome = self._sync_once(sync)
            failure = classify(outcome)
            if failure is not None:
                self._finish(failure)
                return
            with self._lock:
                self._state.drifted = outcome.drifted
                self._state.code = RefreshCode.APPLYING
            applying = True
            self._apply.apply()
        except BaseException as error:
            logger.exception("ledger refresh stopped — applying=%s", applying)
            self._finish(RefreshFailure.APPLY if applying else RefreshFailure.UNEXPECTED)
            if not isinstance(error, Exception):
                raise
            return
        now = self._now()
        with self._lock:
            self._state.code = RefreshCode.SUCCEEDED
            self._state.finished_at = now
            # 받는 동안 서버 판이 바뀌었으면 받을 것이 남았다 — 다음 조회가 다시 확인한다.
            self._state.remote = (
                RemoteCheck(RemoteState.UNKNOWN, 0, 0, False, None)
                if self._state.drifted
                else RemoteCheck(RemoteState.UP_TO_DATE, 0, 0, False, now)
            )

    def _sync_once(self, sync: LedgerSyncPort) -> SyncOutcome:
        job = sync.start()
        with self._lock:
            self._job = job
        try:
            while True:
                outcome = job.wait(_POLL_S)
                progress = job.progress()
                if progress is not None:
                    with self._lock:
                        self._state.progress = progress
                if outcome is not None:
                    return outcome
        finally:
            with self._lock:
                self._job = None

    def _finish(self, failure: RefreshFailure) -> None:
        logger.warning("ledger refresh ended without applying — failure=%s", failure.value)
        now = self._now()
        with self._lock:
            self._state.code = RefreshCode.PARTIAL if failure in _PARTIAL else RefreshCode.FAILED
            self._state.failure = failure
            self._state.finished_at = now

    # ── 상태 문장 ─────────────────────────────────────────────────────────────

    def _status(self, state: _State) -> RefreshStatus:
        stale = state.remote.checked_at is None or (
            self._now() - state.remote.checked_at > REMOTE_CHECK_TTL
        )
        base = RefreshStatus(
            code=state.code,
            message="",
            can_start=False,
            refreshing=state.code in _BUSY,
            reason=self._unavailable,
            failure=state.failure,
            remote=state.remote,
            needs_check=False,
            progress=state.progress,
            active_work=None,
            started_at=state.started_at,
            finished_at=state.finished_at,
        )
        if self._unavailable is not None or self._sync is None:
            reason = self._unavailable or UnavailableReason.PRACTICE_DATA
            return replace(base, code=RefreshCode.UNAVAILABLE, message=UNAVAILABLE_MESSAGES[reason])
        if state.code is RefreshCode.RUNNING:
            return replace(base, message=RUNNING_MESSAGE)
        if state.code is RefreshCode.APPLYING:
            return replace(base, message=APPLYING_MESSAGE)
        lock = self._sync.lock()
        if lock is not None:
            message = FAILURE_MESSAGES[RefreshFailure.LOCKED]
            if lock.stale:
                message += (
                    " 그 작업이 3시간 넘게 끝나지 않아 멈춘 것 같아요(사용 설명서 '데이터 받기')."
                )
            return replace(base, code=RefreshCode.LOCKED, message=message)
        work = self._active_work.active()
        settled = replace(base, active_work=work, needs_check=stale)
        if work.any:
            # 마지막 결과(failure·finished_at)는 그대로 실어 화면이 따로 알릴 수 있다.
            return replace(settled, code=RefreshCode.BUSY_WORK, message=_busy_message(work))
        if state.code is RefreshCode.SUCCEEDED:
            message = SUCCEEDED_MESSAGE + (DRIFTED_NOTE if state.drifted else "")
        elif state.failure is not None:
            message = FAILURE_MESSAGES[state.failure]
        else:
            message = REMOTE_MESSAGES[state.remote.state]
            if state.remote.in_build_window:
                message += " " + BUILD_WINDOW_NOTE
        return replace(settled, message=message, can_start=True)


def _busy_message(work: ActiveWork) -> str:
    parts = [
        f"{label} {count}{unit}"
        for label, count, unit in (
            ("백테스트", work.backtests, "건"),
            ("실험", work.experiments, "개"),
            ("연속 평가", work.evaluations, "건"),
        )
        if count
    ]
    # 대기·일시정지한 실험도 끝나지 않은 실험이다 — 그냥 두면 계속 막힌다.
    paused = " 일시정지한 실험도 끝내거나 취소해야 해요." if work.experiments else ""
    return (
        f"진행 중인 {'·'.join(parts)}이 끝나야 받을 수 있어요. 받으면 앱을 새 데이터로 다시 "
        f"세워야 해서요.{paused}"
    )
