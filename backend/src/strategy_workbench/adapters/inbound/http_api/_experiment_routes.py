"""`/api/v1/experiments/*` 라우트 (검증 랩 spec D5·D6·D9).

요청을 유스케이스로 넘기고 거절을 코드 있는 HTTP 오류로 옮기기만 한다. 실험 설계·상태 판정은
`application/experiment_run`·`domain/experiment` 가, 기반 실행 요청 판정은 실행 접수(`admitted`)가
한다 — 기반 요청 거절은 백테스트 시작과 같은 코드다.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from pydantic import Field
from pydantic.config import JsonDict

from strategy_workbench.application.experiment_run.facade.experiments import (
    Experiment,
    ExperimentPreview,
    ExperimentRequest,
    ExperimentRunService,
    ExperimentTrialState,
)
from strategy_workbench.application.experiment_run.facade.ports import ExperimentSelection
from strategy_workbench.domain.experiment.facade.design import (
    EXPERIMENT_CODES,
    ExperimentError,
    ExperimentNotFoundError,
    ExperimentStateError,
)

from ._backtest_contract import (
    BacktestStrategyNotFoundResponse,
    BacktestStrategyStaleResponse,
    BacktestUnprocessableDetail,
    CodedBodyValidationRoute,
    backtest_field_invalid_detail,
)


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
class ExperimentSelectionRequest:
    trial_index: Annotated[int, Field(ge=0)]
    # 공백만 있는 이유는 기록이 아니다.
    reason: Annotated[str, Field(pattern=r"\S")]


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

    for path, endpoint, operation_id, code in (
        ("/api/v1/experiments/preview", preview_experiment, "previewExperiment", 200),
        ("/api/v1/experiments", create_experiment, "createExperiment", 202),
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

    @app.get(
        "/api/v1/experiments/{experiment_id}",
        operation_id="getExperiment",
        responses={404: rejected[404]},
    )
    def get_experiment(experiment_id: str) -> Experiment:
        return experiments.get(experiment_id)

    @app.get(
        "/api/v1/experiments/{experiment_id}/trials",
        operation_id="listExperimentTrials",
        responses={404: rejected[404]},
    )
    def list_experiment_trials(experiment_id: str) -> tuple[ExperimentTrialState, ...]:
        """trial 전개 순. 상태는 최신 attempt 의 실행 상태에서 파생한다."""
        return experiments.trials(experiment_id)

    @app.post(
        "/api/v1/experiments/{experiment_id}/cancel",
        operation_id="cancelExperiment",
        responses={404: rejected[404]},
    )
    def cancel_experiment(experiment_id: str) -> Experiment:
        return experiments.cancel(experiment_id)

    @app.post(
        "/api/v1/experiments/{experiment_id}/trials/{trial_index}/retry",
        operation_id="retryExperimentTrial",
        responses=rejected,
    )
    def retry_experiment_trial(experiment_id: str, trial_index: int) -> ExperimentTrialState:
        """실패·취소된 trial 을 새 attempt 로 다시 넘긴다."""
        return experiments.retry(experiment_id, trial_index)

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
