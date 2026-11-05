"""홈 "DB 새로고침" API와, 계산을 시작하는 라우트가 다는 의존성(`STARTS_WORK`).

상태 판정과 문장은 `application.ledger_refresh`가 정한다. 여기서는 응답 모양으로 옮긴다. 계산을
시작하는 라우트(백테스트 시작·실험 만들기·재시도·연속 평가 등)는 정의에 `STARTS_WORK` 를 달아
요청을 처리하는 동안 자리를 쥐고(`admit_work`), 받는 동안에는 409 `data.refreshing` 으로 돌아간다 —
앱을 새 데이터로 다시 세울 때 옛 앱이 쥔 계산이 없어야 판본이 섞이지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import Field

from strategy_workbench.application.ledger_refresh.facade.refresh import (
    DataRefreshingError,
    LedgerRefreshService,
    RefreshCode,
    RefreshFailure,
    RefreshOutcome,
    RefreshRejectedError,
    RefreshStatus,
    RemoteState,
    SyncStage,
    UnavailableReason,
)

LEDGER_REFRESH_PATH = "/api/v1/ledger-refresh"


def _admit_work(request: Request) -> Iterator[None]:
    """계산을 시작하는 요청을 처리하는 동안 자리를 쥔다. 본문을 검증하기 전에 판정한다(깨진 JSON
    은 받는 중에도 422 다).

    미리보기·추적·조회에는 달지 않는다 — 부팅 때 고정한 판본만 읽는다.
    """
    service: LedgerRefreshService | None = request.app.state.ledger_refresh
    if service is None:
        yield
        return
    try:
        with service.admit_work():
            yield
    except DataRefreshingError as error:  # 자리를 쥐기 전에만 난다
        raise HTTPException(
            status_code=409, detail={"code": error.code, "message": error.message}
        ) from error


# 계산을 시작하는 라우트가 `dependencies=[STARTS_WORK]` 로 단다.
STARTS_WORK = Depends(_admit_work)


@dataclass(frozen=True)
class RemoteCheckBody:
    state: RemoteState
    new_tables: Annotated[int, Field(description="서버에 새 판본이 있는 데이터 수.")]
    download_bytes: int
    in_build_window: Annotated[bool, Field(description="서버가 지금 데이터를 만드는 시간인가.")]
    checked_at: datetime | None


@dataclass(frozen=True)
class SyncProgressBody:
    stage: SyncStage
    stage_started_at: datetime | None
    tables_total: Annotated[int | None, Field(description="받을 데이터 수. 받기 단계만 안다.")]
    tables_done: int
    bytes_total: int | None
    bytes_done: int


@dataclass(frozen=True)
class ActiveWorkBody:
    backtests: int
    experiments: int
    evaluations: int


@dataclass(frozen=True)
class LedgerRefreshBody:
    code: RefreshCode
    message: Annotated[str, Field(description="지금 상태를 말하는 한 문장(backend 가 완성).")]
    can_start: bool
    refreshing: Annotated[
        bool, Field(description="받거나 반영하는 중인가. 이 동안 계산 시작은 409 data.refreshing.")
    ]
    last_outcome: Annotated[
        RefreshOutcome | None,
        Field(
            description="마지막으로 끝난 받기의 결과(finished_at 과 짝). 지금 code 와 다를 수 있다."
        ),
    ]
    outcome_message: Annotated[
        str | None,
        Field(description="그 결과의 문장(한글). 지금 상태 문장 message 와 다를 수 있다."),
    ]
    reason: UnavailableReason | None
    failure: RefreshFailure | None
    needs_check: Annotated[bool, Field(description="서버 판 확인이 오래돼 다시 확인할 때다.")]
    remote: RemoteCheckBody
    progress: SyncProgressBody | None
    active_work: ActiveWorkBody | None
    started_at: datetime | None
    finished_at: datetime | None


@dataclass(frozen=True)
class LedgerRefreshErrorDetail:
    code: Literal["ledger_refresh.rejected", "data.refreshing"]
    message: str


@dataclass(frozen=True)
class LedgerRefreshErrorResponse:
    detail: LedgerRefreshErrorDetail


def _body(status: RefreshStatus) -> LedgerRefreshBody:
    remote, progress, work = status.remote, status.progress, status.active_work
    return LedgerRefreshBody(
        code=status.code,
        message=status.message,
        can_start=status.can_start,
        refreshing=status.refreshing,
        last_outcome=status.last_outcome,
        outcome_message=status.outcome_message,
        reason=status.reason,
        failure=status.failure,
        needs_check=status.needs_check,
        remote=RemoteCheckBody(
            remote.state,
            remote.new_tables,
            remote.download_bytes,
            remote.in_build_window,
            remote.checked_at,
        ),
        progress=None
        if progress is None
        else SyncProgressBody(
            progress.stage,
            progress.stage_started_at,
            progress.tables_total,
            progress.tables_done,
            progress.bytes_total,
            progress.bytes_done,
        ),
        active_work=None
        if work is None
        else ActiveWorkBody(work.backtests, work.experiments, work.evaluations),
        started_at=status.started_at,
        finished_at=status.finished_at,
    )


def register_ledger_refresh_routes(app: FastAPI, service: LedgerRefreshService) -> None:
    @app.get(LEDGER_REFRESH_PATH, operation_id="getLedgerRefresh")
    def get_status() -> LedgerRefreshBody:
        return _body(service.status())

    @app.post(f"{LEDGER_REFRESH_PATH}/check", operation_id="checkLedgerRefresh")
    def check() -> LedgerRefreshBody:
        """서버 판을 다시 확인한다(원격 MANIFEST 만 읽는다). 받는 중이면 지금 상태만 돌려준다."""
        return _body(service.check())

    @app.post(
        LEDGER_REFRESH_PATH,
        operation_id="startLedgerRefresh",
        status_code=202,
        responses={409: {"model": LedgerRefreshErrorResponse}},
    )
    def start() -> LedgerRefreshBody:
        try:
            return _body(service.start())
        except RefreshRejectedError as error:
            raise HTTPException(
                409,
                detail={"code": "ledger_refresh.rejected", "message": error.status.message},
            ) from error
