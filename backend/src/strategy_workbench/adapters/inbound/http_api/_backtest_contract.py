"""Inbound-owned error contract for starting a backtest run."""

from __future__ import annotations

from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any, Literal, TypeAlias

from fastapi import HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import Field

from strategy_workbench.application.backtest_run.facade.runs import InvalidRunFieldError

from ._execution_error_contract import PortfolioStrategyInvalidDetail


@dataclass(frozen=True)
class BacktestRunInvalidDetail:
    code: Literal["backtest.run.invalid"]
    message: str


@dataclass(frozen=True)
class BacktestRunFieldInvalidDetail:
    """요청 본문이 스키마나 칸 규칙을 어겼다(이슈 #260).

    `field` 는 본문의 점 경로(`initial_cash`, `environment.fee_bps`)다. 본문이 JSON 이 아니거나
    본문 전체가 빠져 칸을 특정할 수 없으면 null 이다. `message` 는 진단용 원문이고, 화면 문장은
    frontend 가 `code` 로 번역한다.
    """

    code: Literal["backtest.run.field_invalid"]
    field: str | None
    message: str


@dataclass(frozen=True)
class BacktestEnvironmentRequiredDetail:
    """실행 설정 없이 들어온 시작 요청. schema 1.2 문서는 문서에 실행 설정을 담지 않는다."""

    code: Literal["backtest.run.environment_required"]
    message: str


@dataclass(frozen=True)
class BacktestResearchWindowViolationDetail:
    """측정 시작일이 연구 구간 밖이다(spec D1).

    화면 문장은 frontend 가 `code` 로 번역하되 날짜는 자리표시자로 두고 이 detail 의 값으로 채운다 —
    날짜 owner 는 `domain/backtest/_research_window.py` 하나다.
    """

    code: Literal["backtest.run.research_window_violation"]
    message: str
    sealed_start: date
    sealed_end: date
    research_start: date


@dataclass(frozen=True)
class BacktestParameterInvalidDetail:
    """요청의 파라미터 값이 문서에 없는 파라미터이거나 허용 밖이다(spec D4).

    허용 판정 owner 는 `domain/strategy/_models.py` 의 `normalized_parameter_value` 다. 화면 문장은
    frontend 가 `code` 로 번역하고 `parameter_id` 를 자리표시자로 채운다.
    """

    code: Literal["backtest.run.parameter_invalid"]
    message: str
    parameter_id: str


@dataclass(frozen=True)
class BacktestStrategyRequiresUpgradeDetail:
    code: Literal["backtest.strategy.requires_upgrade"]
    message: str


# 시작 요청의 사전 검사는 관측 데이터를 읽지 않으므로(이슈 #158) `portfolio.data.unavailable` ·
# `portfolio.raw_observation.invalid` 는 이 경로에서 나오지 않는다. 그 실패는 run 상태 `failed` 의
# `error` 로 전달된다.
BacktestUnprocessableDetail: TypeAlias = Annotated[
    BacktestRunInvalidDetail
    | BacktestRunFieldInvalidDetail
    | BacktestEnvironmentRequiredDetail
    | BacktestResearchWindowViolationDetail
    | BacktestParameterInvalidDetail
    | BacktestStrategyRequiresUpgradeDetail
    | PortfolioStrategyInvalidDetail,
    Field(discriminator="code"),
]


@dataclass(frozen=True)
class BacktestUnprocessableResponse:
    detail: BacktestUnprocessableDetail


# 본문 검증 실패도 `backtest.run.field_invalid` 로 코드화되므로(`CodedBodyValidationRoute`)
# FastAPI 기본 배열 형식(`RequestValidationResponse`)은 이 라우트의 422 에 없다.
Backtest422Response: TypeAlias = BacktestUnprocessableResponse

_SCALAR = (str, int, float, bool, type(None))


def _issue_field(issue: Mapping[str, Any]) -> str | None:
    """검증 오류 하나가 가리키는 본문 칸의 점 경로. 칸을 특정할 수 없으면 None."""
    if issue.get("type") == "json_invalid":
        return None
    location = tuple(issue.get("loc", ()))
    parts = [str(part) for part in location[1:]] if location[:1] == ("body",) else []
    raised = issue.get("ctx", {}).get("error")
    # `__post_init__` 오류의 `loc` 은 객체까지만 가리킨다. 도메인이 예외에 실은 칸 이름을 붙인다.
    if isinstance(raised, InvalidRunFieldError):
        parts.append(raised.field)
    return ".".join(parts) or None


def _issue_message(issue: Mapping[str, Any], field: str | None) -> str:
    value = issue.get("input")
    shown = f" input={value!r}" if isinstance(value, _SCALAR) else ""
    return f"{issue.get('msg', 'invalid request')} — field={field or '-'}{shown}"


def backtest_field_invalid_detail(issues: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """본문 검증 오류 목록 → `backtest.run.field_invalid` detail. 첫 오류의 칸을 `field` 로 쓴다."""
    fields = [_issue_field(issue) for issue in issues]
    return {
        "code": "backtest.run.field_invalid",
        "field": fields[0] if fields else None,
        "message": "; ".join(
            _issue_message(issue, field) for issue, field in zip(issues, fields, strict=True)
        )
        or f"request body failed validation — issues={len(issues)}",
    }


class CodedBodyValidationRoute(APIRoute):
    """본문 검증 실패(`RequestValidationError`)를 코드화된 422 로 바꾸는 라우트(이슈 #260).

    FastAPI 기본 응답은 `detail` 이 배열이고 `code` 가 없어, 프론트가 번역할 키도 고칠 칸도 얻지
    못한다. 앱 전체 핸들러로 바꾸면 다른 라우트의 422 계약까지 바뀌므로 시작 라우트에만 건다.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def coded_handler(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as error:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=backtest_field_invalid_detail(error.errors()),
                ) from error

        return coded_handler


@dataclass(frozen=True)
class BacktestStrategyNotFoundDetail:
    code: Literal["backtest.strategy.not_found"]
    message: str


@dataclass(frozen=True)
class BacktestStrategyNotFoundResponse:
    detail: BacktestStrategyNotFoundDetail


@dataclass(frozen=True)
class BacktestStrategyStaleDetail:
    code: Literal["backtest.strategy.stale"]
    message: str


@dataclass(frozen=True)
class BacktestStrategyStaleResponse:
    detail: BacktestStrategyStaleDetail


@dataclass(frozen=True)
class BacktestRunNotFoundDetail:
    code: Literal["backtest.run.not_found"]
    message: str


@dataclass(frozen=True)
class BacktestRunNotFoundResponse:
    detail: BacktestRunNotFoundDetail


@dataclass(frozen=True)
class BacktestResultNotReadyDetail:
    code: Literal["backtest.result.not_ready"]
    message: str


@dataclass(frozen=True)
class BacktestResultNotReadyResponse:
    detail: BacktestResultNotReadyDetail


@dataclass(frozen=True)
class TrialLineageMergeRequest:
    """합칠 계열(`source_strategy_id`). 경로의 계열이 남는다."""

    source_strategy_id: Annotated[str, Field(min_length=1)]


@dataclass(frozen=True)
class TrialLineageAlreadyMergedDetail:
    code: Literal["backtest.lineage.already_merged"]
    message: str


@dataclass(frozen=True)
class TrialLineageAlreadyMergedResponse:
    detail: TrialLineageAlreadyMergedDetail


@dataclass(frozen=True)
class StrategyNotFoundDetail:
    code: Literal["strategy.not_found"]
    message: str


@dataclass(frozen=True)
class StrategyNotFoundResponse:
    """시도 원장 경로의 계열(저장된 전략)이 없다."""

    detail: StrategyNotFoundDetail
