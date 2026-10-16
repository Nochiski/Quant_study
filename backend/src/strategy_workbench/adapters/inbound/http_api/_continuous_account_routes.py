"""명시 요청 ID로 시작하고 영속 이력을 읽는 연속 계좌 API."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import FastAPI, HTTPException

from strategy_workbench.application.continuous_account.facade.evaluation import (
    ContinuousAccountCode,
    ContinuousAccountLatest,
    ContinuousAccountNotFoundError,
    ContinuousAccountRejectedError,
    ContinuousAccountService,
    ContinuousAccountState,
    ContinuousAccountUnreadableError,
)
from strategy_workbench.domain.experiment.facade.design import ExperimentNotFoundError

from ._backtest_contract import CodedBodyValidationRoute


@dataclass(frozen=True)
class ContinuousAccountStartRequest:
    client_request_id: UUID


@dataclass(frozen=True)
class ContinuousAccountErrorDetail:
    code: ContinuousAccountCode
    message: str
    field: str | None = None


@dataclass(frozen=True)
class ContinuousAccountErrorResponse:
    detail: ContinuousAccountErrorDetail


class _ContinuousBodyRoute(CodedBodyValidationRoute):
    @staticmethod
    def coded_detail(issues: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        # reason: FastAPI 검증 오류 hook의 공개 JSON 계약을 그대로 받는다.
        return {
            "code": "continuous.request_invalid",
            "message": "유효한 client_request_id UUID를 요청 본문에 지정해 주세요.",
            "field": "/client_request_id",
        }


def register_continuous_account_routes(app: FastAPI, service: ContinuousAccountService) -> None:
    responses: dict[int | str, dict[str, object]] = {
        404: {"model": ContinuousAccountErrorResponse},
        409: {"model": ContinuousAccountErrorResponse},
        410: {"model": ContinuousAccountErrorResponse},
        422: {"model": ContinuousAccountErrorResponse},
    }

    def translate(error: Exception) -> HTTPException:
        if isinstance(error, ContinuousAccountUnreadableError):
            return HTTPException(
                410,
                detail={
                    "code": "continuous.result_unreadable",
                    "message": (
                        "저장된 연속 평가 이력을 검증할 수 없습니다. "
                        "이 결과의 자동 재조회는 중지됩니다."
                    ),
                },
            )
        if isinstance(error, ContinuousAccountRejectedError):
            return HTTPException(
                error.http_status, detail={"code": error.code, "message": str(error)}
            )
        return HTTPException(
            404,
            detail={
                "code": "continuous.not_found",
                "message": "요청한 실험 또는 연속 평가 이력을 찾을 수 없습니다.",
            },
        )

    @app.get(
        "/api/v1/experiments/{experiment_id}/continuous-account",
        operation_id="getExperimentContinuousAccount",
        responses=responses,
    )
    def latest(experiment_id: str) -> ContinuousAccountLatest:
        try:
            return service.latest(experiment_id)
        except (
            ContinuousAccountNotFoundError,
            ExperimentNotFoundError,
            ContinuousAccountUnreadableError,
        ) as error:
            raise translate(error) from error

    def run(experiment_id: str, body: ContinuousAccountStartRequest) -> ContinuousAccountState:
        # 동기 route를 Starlette가 threadpool에서 실행한다. 접속 유실은 실행 취소가 아니다.
        try:
            return service.run(experiment_id, str(body.client_request_id))
        except (
            ContinuousAccountRejectedError,
            ContinuousAccountNotFoundError,
            ContinuousAccountUnreadableError,
            ExperimentNotFoundError,
        ) as error:
            raise translate(error) from error

    app.router.add_api_route(
        "/api/v1/experiments/{experiment_id}/continuous-account",
        run,
        methods=["POST"],
        operation_id="runExperimentContinuousAccount",
        responses=responses,
        route_class_override=_ContinuousBodyRoute,
    )

    @app.get(
        "/api/v1/continuous-accounts/{evaluation_id}",
        operation_id="getContinuousAccount",
        responses=responses,
    )
    def get(evaluation_id: str) -> ContinuousAccountState:
        try:
            return service.get(evaluation_id)
        except (ContinuousAccountNotFoundError, ContinuousAccountUnreadableError) as error:
            raise translate(error) from error
