"""Outgoing ports: 원장 동기화 도구, 진행 중 작업 수, 앱 반영.

동기화 도구(`ledger_sync`)의 기계용 출력(`plan --json`·`--progress-file`·`last_run.json`)을 값으로
옮겨 받는다. 사람용 로그 문장을 해석하지 않는다. 서버 주소·계정은 어떤 값에도 싣지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

__all__ = [
    "ActiveWork",
    "ActiveWorkPort",
    "ApplyDataPort",
    "LedgerSyncPort",
    "SyncFailureKind",
    "SyncJob",
    "SyncLock",
    "SyncOutcome",
    "SyncPlan",
    "SyncProgress",
    "SyncStage",
]


class SyncStage(StrEnum):
    DOWNLOAD = "download"
    CATALOG = "catalog"
    VERIFY = "verify"
    DONE = "done"


class SyncFailureKind(StrEnum):
    """`last_run.json` 의 `error_kind`. 모르는 값은 `UNEXPECTED` 로 접는다."""

    CONNECT = "connect"
    TRANSFER = "transfer"
    DISK = "disk"
    LOCKED = "locked"
    UNEXPECTED = "unexpected"


@dataclass(frozen=True)
class SyncLock:
    """다른 sync 가 잡은 `_sync/lock`."""

    started_at: datetime | None
    stale: bool


@dataclass(frozen=True)
class SyncPlan:
    """`plan --json`: 받을 새 판본 표 수와 바이트. 접속하지 못하면 `failure` 가 있다."""

    new_tables: int
    download_bytes: int
    in_build_window: bool
    failure: SyncFailureKind | None = None


@dataclass(frozen=True)
class SyncProgress:
    stage: SyncStage
    stage_started_at: datetime | None
    tables_total: int | None
    tables_done: int
    bytes_total: int | None
    bytes_done: int


@dataclass(frozen=True)
class SyncOutcome:
    """끝난 sync 하나. `pulled` 는 표 판본을 하나라도 바꿨는가(받기 단계를 지났는가)다."""

    exit_code: int
    failure: SyncFailureKind | None
    pulled: bool
    tables_failed: int
    catalog_failed: bool
    verify_mismatch: bool
    drifted: bool


class SyncJob(Protocol):
    def progress(self) -> SyncProgress | None:
        """마지막으로 읽은 진행. 아직 아무것도 쓰지 않았으면 None."""
        ...

    def wait(self, timeout_s: float) -> SyncOutcome | None:
        """끝나면 결과, `timeout_s` 안에 끝나지 않으면 None."""
        ...

    def cancel(self) -> None:
        """받기 단계에서만 의미가 있다. 이미 받은 표는 새 판으로 남는다."""
        ...


class LedgerSyncPort(Protocol):
    def lock(self) -> SyncLock | None: ...

    def plan(self) -> SyncPlan: ...

    def start(self) -> SyncJob: ...


@dataclass(frozen=True)
class ActiveWork:
    backtests: int
    experiments: int
    evaluations: int

    @property
    def any(self) -> bool:
        return (self.backtests + self.experiments + self.evaluations) > 0


class ActiveWorkPort(Protocol):
    def active(self) -> ActiveWork:
        """지금 앱이 쥔 계산: 끝나지 않은 백테스트(대기 포함)·실험(대기 포함)·연속 평가."""
        ...


class ApplyDataPort(Protocol):
    def apply(self) -> None:
        """받은 원장으로 앱을 다시 세운다. 실패하면 예외 — 옛 앱이 그대로 남는다."""
        ...
