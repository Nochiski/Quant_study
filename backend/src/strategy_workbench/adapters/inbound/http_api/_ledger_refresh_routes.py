"""홈 "DB 새로고침" API와, 받는 동안 계산을 시작하는 요청을 막는 가드.

상태 판정과 문장은 `application.ledger_refresh`가 정한다. 여기서는 응답 모양으로 옮기고, 받는 동안
(`blocks_new_work`) 계산을 시작하는 경로를 409 `data.refreshing`으로 돌려보낸다 — 앱을 새 데이터로
다시 세울 때 옛 앱이 쥔 계산이 없어야 판본이 섞이지 않는다.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import Field

from strategy_workbench.application.ledger_refresh.facade.refresh import (
    LedgerRefreshService,
    RefreshCode,
    RefreshFailure,
    RefreshRejectedError,
    RefreshStatus,
    RemoteState,
    SyncStage,
    UnavailableReason,
)

LEDGER_REFRESH_PATH = "/api/v1/ledger-refresh"
DATA_REFRESHING_MESSAGE = (
    "데이터를 새로 받는 중이에요. 받기가 끝나고 앱에 반영된 뒤 다시 시작해 주세요."
)
# 계산을 시작하는 요청(POST). 미리보기·추적·조회는 막지 않는다 — 부팅 때 고정한 판본만 읽는다.
_COMPUTE_STARTS = tuple(
    re.compile(pattern)
    for pattern in (
        r"^/api/v1/backtests$",
        r"^/api/v1/backtests/[^/]+/robustness-check$",
        r"^/api/v1/experiments$",
        r"^/api/v1/experiments/capacity$",
        r"^/api/v1/experiments/[^/]+/trials/[^/]+/retry$",
        r"^/api/v1/experiments/[^/]+/continuous-account$",
    )
)


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
    @app.middleware("http")
    async def refuse_compute_while_refreshing(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if (
            request.method == "POST"
            and any(pattern.match(request.url.path) for pattern in _COMPUTE_STARTS)
            and service.blocks_new_work()
        ):
            return JSONResponse(
                status_code=409,
                content={"detail": {"code": "data.refreshing", "message": DATA_REFRESHING_MESSAGE}},
            )
        return await call_next(request)

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
