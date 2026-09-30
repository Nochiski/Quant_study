"""실행 경로가 함께 쓰는 실패 wire 모델.

검증·데이터 실패의 뜻은 application 이 소유하고, 이 모듈은 그 HTTP 봉투를 실행 경로 전체에 한 번만
정의해 미리보기·추적·백테스트가 서로 다른 DTO 를 만들지 못하게 한다. FastAPI 기본 본문 검증 실패
형식(`RequestValidationResponse`)은 코드화된 detail 이 없어 따로 두지만, 실행 요청 라우트(시작·미리
계산·미리보기·추적)는 본문 검증 실패도 `backtest.run.field_invalid` 로 코드화하므로
(`CodedBodyValidationRoute`) 그 형식을 싣지 않는다(#351).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Annotated, Literal, TypeAlias

from pydantic import Field

from strategy_workbench.domain.equity.facade.research_data import DataLoadStatus
from strategy_workbench.domain.strategy.facade.validation import StrategyValidation


@dataclass(frozen=True)
class PortfolioStrategyInvalidDetail:
    code: Literal["portfolio.strategy.invalid"]
    validation: StrategyValidation


@dataclass(frozen=True)
class PortfolioDataUnavailableDetail:
    code: Literal["portfolio.data.unavailable"]
    status: DataLoadStatus
    detail: str | None


@dataclass(frozen=True)
class PortfolioRawObservationInvalidDetail:
    """A configured data adapter violated the raw execution-input contract."""

    code: Literal["portfolio.raw_observation.invalid"]
    message: str


# 아래 세 거절은 실행 요청의 본문·실행 설정 판정이라 시작·미리보기·추적이 같은 코드·detail 로 낸다
# (이슈 #260, #351). 실행 설정 거절의 코드는 실행 유스케이스의 `rejection_code` 가, 본문 검증 실패는
# `CodedBodyValidationRoute` 가 준다.


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
    """실행 설정 없이 들어온 실행 요청. schema 1.2 문서는 문서에 실행 설정을 담지 않는다."""

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
class RequestValidationIssue:
    loc: tuple[str | int, ...]
    msg: str
    type: str


@dataclass(frozen=True)
class RequestValidationResponse:
    """FastAPI's malformed-envelope 422 shape, alongside coded application diagnostics."""

    detail: tuple[RequestValidationIssue, ...]


PortfolioUnprocessableDetail: TypeAlias = Annotated[
    PortfolioStrategyInvalidDetail
    | PortfolioDataUnavailableDetail
    | PortfolioRawObservationInvalidDetail
    | BacktestRunFieldInvalidDetail
    | BacktestEnvironmentRequiredDetail
    | BacktestResearchWindowViolationDetail,
    Field(discriminator="code"),
]


@dataclass(frozen=True)
class PortfolioUnprocessableResponse:
    detail: PortfolioUnprocessableDetail


Portfolio422Response: TypeAlias = PortfolioUnprocessableResponse
