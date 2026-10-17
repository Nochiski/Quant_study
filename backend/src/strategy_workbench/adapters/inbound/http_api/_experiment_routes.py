"""`/api/v1/experiments/*` 라우트 (검증 랩 spec D5·D6·D9).

요청을 유스케이스로 넘기고 거절을 코드 있는 HTTP 오류로 옮기기만 한다. 실험 설계·상태 판정은
`application/experiment_run`·`domain/experiment` 가, 기반 실행 요청 판정은 실행 접수(`admitted`)가
한다 — 기반 요청 거절은 백테스트 시작과 같은 코드다.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Annotated, Any

from fastapi import FastAPI, Query, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import AfterValidator, Field
from pydantic.config import JsonDict

from strategy_workbench.application.experiment_run.facade.experiments import (
    CapacityReport,
    CapacitySweepRequest,
    Experiment,
    ExperimentAuditEvent,
    ExperimentPage,
    ExperimentPreview,
    ExperimentRequest,
    ExperimentRunService,
    ExperimentTrialState,
    ParameterMap,
    WalkForwardReport,
)
from strategy_workbench.application.experiment_run.facade.ports import ExperimentSelection
from strategy_workbench.domain.experiment.facade.design import (
    EXPERIMENT_CODES,
    ExperimentError,
    ExperimentNotFoundError,
    ExperimentStateError,
    TimeEstimate,
)
from strategy_workbench.domain.experiment.facade.trial import (
    MAX_EXPERIMENT_PRIORITY,
    ExperimentStatus,
    TrialStatus,
    validate_selection_reason,
)

from ._backtest_contract import (
    BacktestStrategyNotFoundResponse,
    BacktestStrategyStaleResponse,
    BacktestUnprocessableDetail,
    CodedBodyValidationRoute,
    backtest_field_invalid_detail,
)
from ._sse import SSE_KEEPALIVE_FRAME, SSE_KEEPALIVE_SECONDS, sse_frame

# 실험 진행 스트림이 실험 상태를 다시 읽는 간격. 한 번 읽을 때 trial 마다 실행 상태를 모으므로
# run 스트림(`SSE_POLL_SECONDS`)보다 드물게 읽는다.
_EXPERIMENT_POLL_SECONDS = 1.0


def _code_enum(schema: JsonDict) -> None:
    schema["enum"] = [code for code in sorted(EXPERIMENT_CODES)]


@dataclass(frozen=True)
class ExperimentErrorDetail:
    """실험 거절. 코드 목록 owner 는 `domain/experiment/_errors.py` 이고, 화면 문장은 frontend 가
    `code` 로 번역한다."""

    code: Annotated[str, Field(json_schema_extra=_code_enum)]
    message: str


@dataclass(frozen=True)
class ExperimentErrorResponse:
    detail: ExperimentErrorDetail


@dataclass(frozen=True)
class ExperimentAdmissionErrorResponse:
    """실험 설계 거절이거나, 기반 실행 요청을 실행 접수가 거절했다(백테스트 시작과 같은 코드)."""

    detail: ExperimentErrorDetail | BacktestUnprocessableDetail


@dataclass(frozen=True)
class ExperimentControlsRequest:
    """일시정지·우선순위(1 = 보통). 보내지 않은 칸은 그대로다. 범위 owner 는 domain
    `MAX_EXPERIMENT_PRIORITY` 다."""

    paused: bool | None = None
    priority: Annotated[int | None, Field(ge=1, le=MAX_EXPERIMENT_PRIORITY)] = None


@dataclass(frozen=True)
class ExperimentProgress:
    """실험 진행 스트림의 이벤트 — 실험 상태와 trial 상태별 수."""

    status: ExperimentStatus
    trial_counts: dict[TrialStatus, int]


@dataclass(frozen=True)
class ExperimentSelectionRequest:
    trial_index: Annotated[int, Field(ge=0)]
    # 공백만 있는 이유는 기록이 아니다.
    reason: Annotated[str, AfterValidator(validate_selection_reason)]


class _ExperimentBodyRoute(CodedBodyValidationRoute):
    """본문을 읽다 분할 규칙이 거절하면(`SplitSpec.__post_init__`) 그 실험 코드를 싣는다."""

    @staticmethod
    def coded_detail(issues: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        for issue in issues:
            raised = issue.get("ctx", {}).get("error")
            if isinstance(raised, ExperimentError):
                return {"code": raised.code, "message": str(raised)}
        return backtest_field_invalid_detail(issues)


def _http_status(error: ExperimentError) -> int:
    if isinstance(error, ExperimentNotFoundError):
        return status.HTTP_404_NOT_FOUND
    if isinstance(error, ExperimentStateError):
        return status.HTTP_409_CONFLICT
    return status.HTTP_422_UNPROCESSABLE_CONTENT


def register_experiment_routes(
    app: FastAPI,
    experiments: ExperimentRunService,
    *,
    admitted: Callable[[Callable[[], Any]], Any],
) -> None:
    streams = _ExperimentStreamHub(experiments)
    app.router.add_event_handler("shutdown", streams.close)

    async def experiment_error(_: Request, error: Exception) -> JSONResponse:
        assert isinstance(error, ExperimentError)
        return JSONResponse(
            status_code=_http_status(error),
            content={"detail": {"code": error.code, "message": str(error)}},
        )

    app.add_exception_handler(ExperimentError, experiment_error)

    rejected: dict[int | str, dict[str, Any]] = {
        404: {
            "model": ExperimentErrorResponse,
            "description": "The experiment or trial is missing",
        },
        409: {"model": ExperimentErrorResponse, "description": "The experiment state refuses it"},
    }
    admission: dict[int | str, dict[str, Any]] = {
        404: {
            "model": BacktestStrategyNotFoundResponse,
            "description": "The base strategy revision does not exist",
        },
        409: {
            "model": BacktestStrategyStaleResponse,
            "description": "The base revision hash differs from the expected hash",
        },
        422: {
            "model": ExperimentAdmissionErrorResponse,
            "description": "A coded experiment design or base run diagnostic",
        },
    }

    def preview_experiment(request: ExperimentRequest) -> ExperimentPreview:
        """시작 전 미리 계산 — 조합·창·실행 수와 계열 시도 수 N 의 변화(spec D2)."""
        return admitted(lambda: experiments.preview(request))

    def create_experiment(request: ExperimentRequest) -> Experiment:
        """실험을 만들고 trial 을 실행 대기열에 넘긴다. 기반은 저장한 리비전뿐이다."""
        return admitted(lambda: experiments.create(request))

    def preview_capacity_sweep(request: CapacitySweepRequest) -> ExperimentPreview:
        """용량 스윕 시작 전 미리 계산. 금액만 다른 실행은 한 시도라 N 은 많아야 1 늘어난다."""
        return admitted(lambda: experiments.preview_capacity(request))

    def create_capacity_sweep(request: CapacitySweepRequest) -> Experiment:
        """용량 스윕(초기 자본만 바꾼 실행들, V4-04)을 만들고 금액마다 실행 대기열에 넘긴다."""
        return admitted(lambda: experiments.create_capacity(request))

    for path, endpoint, operation_id, code in (
        ("/api/v1/experiments/preview", preview_experiment, "previewExperiment", 200),
        ("/api/v1/experiments", create_experiment, "createExperiment", 202),
        (
            "/api/v1/experiments/capacity/preview",
            preview_capacity_sweep,
            "previewCapacitySweep",
            200,
        ),
        ("/api/v1/experiments/capacity", create_capacity_sweep, "createCapacitySweep", 202),
    ):
        app.router.add_api_route(
            path,
            endpoint,
            methods=["POST"],
            operation_id=operation_id,
            status_code=code,
            route_class_override=_ExperimentBodyRoute,
            responses=admission,
        )

    robustness = {
        **admission,
        404: {
            "model": ExperimentErrorResponse | BacktestStrategyNotFoundResponse,
            "description": "The result run or its base strategy revision does not exist",
        },
        409: {
            "model": ExperimentErrorResponse | BacktestStrategyStaleResponse,
            "description": "A check of this run is still running, or the base revision is stale",
        },
    }

    @app.post(
        "/api/v1/backtests/{run_id}/robustness-check/preview",
        operation_id="previewRobustnessCheck",
        responses=robustness,
    )
    def preview_robustness_check(run_id: str) -> ExperimentPreview:
        """결과 실행의 "튼튼한지 확인하기" 미리 계산 — 기본 설계와 새로 세는 시도 수(US-DM-11)."""
        return admitted(lambda: experiments.preview_robustness(run_id))

    @app.post(
        "/api/v1/backtests/{run_id}/robustness-check",
        operation_id="createRobustnessCheck",
        status_code=202,
        responses=robustness,
    )
    def create_robustness_check(run_id: str) -> Experiment:
        """결과 실행의 기본 설계로 실험을 만들고 대기열에 넣는다(US-DM-11)."""
        return admitted(lambda: experiments.create_robustness(run_id))

    @app.get("/api/v1/backtests/{run_id}/robustness-checks", operation_id="listRobustnessChecks")
    def list_robustness_checks(run_id: str) -> list[Experiment]:
        """결과 실행에서 "튼튼한지 확인하기"로 만든 실험, 최근에 만든 순. 없으면 빈 목록."""
        return list(experiments.robustness_checks(run_id))

    @app.get("/api/v1/experiments", operation_id="listExperiments")
    def list_experiments(
        after: Annotated[str | None, Query(min_length=1)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> ExperimentPage:
        """최근에 만든 순. 다음 쪽은 응답의 `next_after` 를 `after` 로 넘긴다."""
        return experiments.list(after=after, limit=limit)

    @app.get(
        "/api/v1/experiments/{experiment_id}",
        operation_id="getExperiment",
        responses={404: rejected[404]},
    )
    def get_experiment(experiment_id: str) -> Experiment:
        return experiments.get(experiment_id)

    def control_experiment(experiment_id: str, request: ExperimentControlsRequest) -> Experiment:
        """일시정지·재개·우선순위. 대기 trial 에만 적용하고 도는 trial 은 끝까지 돈다(spec D6)."""
        return experiments.control(experiment_id, paused=request.paused, priority=request.priority)

    app.router.add_api_route(
        "/api/v1/experiments/{experiment_id}/controls",
        control_experiment,
        methods=["PATCH"],
        operation_id="controlExperiment",
        route_class_override=_ExperimentBodyRoute,
        responses={
            404: rejected[404],
            422: {"model": ExperimentAdmissionErrorResponse, "description": "Invalid controls"},
        },
    )

    @app.get(
        "/api/v1/experiments/{experiment_id}/events",
        operation_id="streamExperimentEvents",
        response_class=StreamingResponse,
        responses={404: rejected[404], 200: {"content": {"text/event-stream": {}}}},
    )
    def stream_experiment_events(experiment_id: str) -> StreamingResponse:
        """실험 진행(상태·trial 상태별 수)이 바뀔 때마다 흘리고, 실험이 끝나고 도는·대기 trial 이
        없으면 마지막 수를 보낸 뒤 닫는다."""
        experiments.get(experiment_id)
        return StreamingResponse(
            streams.stream(experiment_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get(
        "/api/v1/experiments/{experiment_id}/trials",
        operation_id="listExperimentTrials",
        responses={404: rejected[404]},
    )
    def list_experiment_trials(experiment_id: str) -> tuple[ExperimentTrialState, ...]:
        """trial 전개 순. 상태는 최신 attempt 의 실행 상태에서 파생한다."""
        return experiments.trials(experiment_id)

    @app.get(
        "/api/v1/experiments/{experiment_id}/walk-forward",
        operation_id="getExperimentWalkForward",
        responses=rejected,
    )
    def get_experiment_walk_forward(experiment_id: str) -> WalkForwardReport:
        """창마다 자동으로 고른 칸과 검증 구간만 이어 붙인 곡선·유지율(V3-05). 사용자 후보 선택
        (`selections`)과 다르다."""
        return experiments.walk_forward(experiment_id)

    @app.get(
        "/api/v1/experiments/{experiment_id}/remaining-time",
        operation_id="getExperimentRemainingTime",
        responses=rejected,
    )
    def get_experiment_remaining_time(experiment_id: str) -> TimeEstimate | None:
        """남은 실행의 시간 어림(로드맵 M6 ETA). 실험 조회·진행 스트림과 따로 둔다 — 계열 원장을
        읽어 목록·스트림 폴링마다 치르지 않게 한다. 근거가 없으면 null."""
        return experiments.remaining_time(experiment_id)

    @app.get(
        "/api/v1/experiments/{experiment_id}/audit",
        operation_id="getExperimentAudit",
        responses=rejected,
    )
    def get_experiment_audit(experiment_id: str) -> tuple[ExperimentAuditEvent, ...]:
        """실험 생성·조작·trial attempt·실행 시작과 끝·창 선택·후보 선택·취소를 시각 순으로
        보인다(로드맵 M6 감사 기록)."""
        return experiments.audit(experiment_id)

    @app.get(
        "/api/v1/experiments/{experiment_id}/parameter-map",
        operation_id="getExperimentParameterMap",
        responses=rejected,
    )
    def get_experiment_parameter_map(experiment_id: str) -> ParameterMap:
        """그리드 칸마다 추천·봉우리·실패 판정과 점수·고원 점수·민감도(V4-03). 판정 기준은 domain
        상수이고 화면은 판정을 번역·칠하기만 한다(V5-04)."""
        return experiments.parameter_map(experiment_id)

    @app.get(
        "/api/v1/experiments/{experiment_id}/capacity",
        operation_id="getExperimentCapacity",
        responses=rejected,
    )
    def get_experiment_capacity(experiment_id: str) -> CapacityReport:
        """용량 스윕 금액별 비용 후 샤프·가격 충격·미체결 비율과 한계 금액(V4-04). 화면은
        V5-06 이다."""
        return experiments.capacity(experiment_id)

    @app.post(
        "/api/v1/experiments/{experiment_id}/cancel",
        operation_id="cancelExperiment",
        responses={
            404: rejected[404],
            409: {"model": ExperimentErrorResponse, "description": "The experiment has completed"},
        },
    )
    def cancel_experiment(experiment_id: str) -> Experiment:
        return experiments.cancel(experiment_id)

    @app.post(
        "/api/v1/experiments/{experiment_id}/trials/{trial_index}/retry",
        operation_id="retryExperimentTrial",
        responses={
            404: {
                "model": ExperimentErrorResponse | BacktestStrategyNotFoundResponse,
                "description": "The trial or its base strategy revision is missing",
            },
            409: {
                "model": ExperimentErrorResponse | BacktestStrategyStaleResponse,
                "description": "The trial is not retryable or the base revision changed",
            },
            422: admission[422],
        },
    )
    def retry_experiment_trial(experiment_id: str, trial_index: int) -> ExperimentTrialState:
        """실패·취소된 trial 을 새 attempt 로 다시 넘긴다. 기반 요청이 이제 접수되지 않으면 백테스트
        시작과 같은 코드로 거절한다."""
        return admitted(lambda: experiments.retry(experiment_id, trial_index))

    @app.post(
        "/api/v1/experiments/{experiment_id}/selections",
        operation_id="selectExperimentTrial",
        responses=rejected,
    )
    def select_experiment_trial(
        experiment_id: str, request: ExperimentSelectionRequest
    ) -> ExperimentSelection:
        """완료된 trial 을 후보로 고른 기록을 남긴다(spec D9). 되돌릴 수 없다."""
        return experiments.select(experiment_id, request.trial_index, request.reason)


async def _progress_stream(
    experiments: ExperimentRunService,
    experiment_id: str,
    *,
    poll_seconds: float = _EXPERIMENT_POLL_SECONDS,
    keepalive_seconds: float = SSE_KEEPALIVE_SECONDS,
    read: Callable[[str], Awaitable[Experiment]] | None = None,
) -> AsyncIterator[str]:
    """창을 닫아도 실험은 서버에서 돈다 — 스트림은 진행을 보이기만 한다(spec D6).

    상태 조회는 저장소를 읽으므로 스레드에서 한다. 조용한 동안에는 keepalive 주석을 보낸다.
    """
    sequence = 0
    last: ExperimentProgress | None = None
    last_frame_at = time.monotonic()
    while True:
        experiment = (
            await asyncio.to_thread(experiments.get, experiment_id)
            if read is None
            else await read(experiment_id)
        )
        progress = ExperimentProgress(experiment.status, experiment.trial_counts)
        if progress != last:
            yield sse_frame(sequence=sequence, event="progress", data=progress)
            sequence, last, last_frame_at = sequence + 1, progress, time.monotonic()
        # 취소한 실험도 도는 trial 이 끝날 때까지는 닫지 않아 마지막 프레임이 최종 수다.
        if experiment.settled:
            return
        if time.monotonic() - last_frame_at >= keepalive_seconds:
            yield SSE_KEEPALIVE_FRAME
            last_frame_at = time.monotonic()
        await asyncio.sleep(poll_seconds)


@dataclass
class _SharedProgress:
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    subscribers: int = 0
    version: int = 0
    latest: str | None = None
    done: bool = False
    error: Exception | None = None
    producer: asyncio.Task[None] | None = None


class _ExperimentStreamHub:
    """같은 실험의 구독은 상태 조회 하나를 공유한다. 마지막 연결이 닫히면 반복 조회를 멈춘다.

    저장된 실험 상태를 캐시하지 않는다. 구독 중인 실험마다 최신 SSE 프레임 하나만 보관하며
    느린 연결에 중간 프레임을 쌓지 않는다. 새 연결도 마지막 상태를 즉시 받는다.
    진행 중인 스레드 조회는 완료까지 따로 소유하여 재접속이 중복 조회를 만들지 않는다.
    """

    def __init__(
        self, experiments: ExperimentRunService, *, poll_seconds: float = _EXPERIMENT_POLL_SECONDS
    ) -> None:
        self._experiments = experiments
        self._poll_seconds = poll_seconds
        self._streams: dict[str, _SharedProgress] = {}
        # 생산자 취소는 실행 중인 스레드를 중단하지 않는다. 실제 조회의 수명은 연결과 분리한다.
        self._reads: dict[str, asyncio.Task[Experiment]] = {}
        self._closing = False
        self._shutdown: asyncio.Task[None] | None = None

    async def stream(self, experiment_id: str) -> AsyncGenerator[str, None]:
        if self._closing:
            return
        shared = self._streams.get(experiment_id)
        if shared is None or shared.done:
            shared = _SharedProgress()
            self._streams[experiment_id] = shared
            shared.producer = asyncio.create_task(self._produce(experiment_id, shared))
        shared.subscribers += 1
        seen = 0
        try:
            while True:
                frame: str | None = None
                async with shared.condition:
                    if shared.version == seen and not shared.done:
                        try:
                            await asyncio.wait_for(
                                shared.condition.wait_for(
                                    lambda observed=seen: shared.version != observed or shared.done
                                ),
                                timeout=SSE_KEEPALIVE_SECONDS,
                            )
                        except TimeoutError:
                            frame = SSE_KEEPALIVE_FRAME
                    if shared.version != seen:
                        seen, frame = shared.version, shared.latest
                    elif shared.done:
                        if shared.error is not None:
                            raise shared.error
                        return
                if frame is not None:
                    yield frame
        finally:
            shared.subscribers -= 1
            if shared.subscribers == 0:
                if self._streams.get(experiment_id) is shared:
                    self._streams.pop(experiment_id, None)
                if shared.producer is not None:
                    shared.producer.cancel()
                    try:
                        await shared.producer
                    except asyncio.CancelledError:
                        # 생산자 자신의 취소는 정상 회수다. 이 구독 작업이 취소되는 중이면 그
                        # 취소를 삼키지 않고 전파한다(끊긴 연결이 정상 종료로 보이지 않게).
                        task = asyncio.current_task()
                        if task is not None and task.cancelling():
                            raise

    async def close(self) -> None:
        """종료 요청자가 취소돼도 실제 조회 회수를 마친 뒤 취소를 돌려준다."""
        if self._shutdown is None:
            self._closing = True
            entries = list(self._streams.values())
            self._streams.clear()
            tasks = [shared.producer for shared in entries if shared.producer is not None]
            for task in tasks:
                task.cancel()
            self._shutdown = asyncio.create_task(self._drain(entries, tasks))
        shutdown = self._shutdown
        cancelled = False
        while not shutdown.done():
            try:
                await asyncio.shield(shutdown)
            except asyncio.CancelledError:
                # 여러 close 호출은 같은 회수를 기다린다. 한 호출의 취소가 공유 작업이나
                # 조회 task를 취소해 실제 스레드의 추적을 지워서는 안 된다.
                cancelled = True
        shutdown.result()
        if self._shutdown is shutdown:
            self._shutdown = None
        if cancelled:
            raise asyncio.CancelledError

    async def _drain(self, entries: list[_SharedProgress], tasks: list[asyncio.Task[None]]) -> None:
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
            # 저장소가 닫히기 전에 실제 스레드가 조회를 마칠 때까지 기다린다.
            await asyncio.gather(*self._reads.values(), return_exceptions=True)
        finally:
            # 시작 전에 취소된 task는 _produce의 finally를 실행하지 않는다. 종료 owner가
            # 직접 종결을 알리므로 첫 프레임을 기다리는 구독도 반드시 깨어난다.
            for shared in entries:
                async with shared.condition:
                    shared.done = True
                    shared.condition.notify_all()
            self._closing = False

    async def _read(self, experiment_id: str) -> Experiment:
        task = self._reads.get(experiment_id)
        if task is None:
            task = asyncio.create_task(asyncio.to_thread(self._experiments.get, experiment_id))
            self._reads[experiment_id] = task

            def finished(done: asyncio.Task[Experiment]) -> None:
                if self._reads.get(experiment_id) is done:
                    self._reads.pop(experiment_id, None)
                # 마지막 구독이 떠난 뒤 실패해도 회수한다.
                # 연결된 생산자는 await로 같은 오류를 받는다.
                if not done.cancelled():
                    done.exception()

            task.add_done_callback(finished)
        return await asyncio.shield(task)

    async def _produce(self, experiment_id: str, shared: _SharedProgress) -> None:
        try:
            async for frame in _progress_stream(
                self._experiments, experiment_id, poll_seconds=self._poll_seconds, read=self._read
            ):
                if frame.startswith(":"):
                    continue
                async with shared.condition:
                    shared.version += 1
                    shared.latest = frame
                    shared.condition.notify_all()
        except Exception as error:
            shared.error = error
        finally:
            async with shared.condition:
                shared.done = True
                shared.condition.notify_all()
