"""Inbound-owned error contract for starting a backtest run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal, TypeAlias

from pydantic import Field

from ._execution_error_contract import (
    PortfolioStrategyInvalidDetail,
    RequestValidationResponse,
)


@dataclass(frozen=True)
class BacktestRunInvalidDetail:
    code: Literal["backtest.run.invalid"]
    message: str


@dataclass(frozen=True)
class BacktestStrategyRequiresUpgradeDetail:
    code: Literal["backtest.strategy.requires_upgrade"]
    message: str


# 시작 요청의 사전 검사는 관측 데이터를 읽지 않으므로(이슈 #158) `portfolio.data.unavailable` ·
# `portfolio.raw_observation.invalid` 는 이 경로에서 나오지 않는다. 그 실패는 run 상태 `failed` 의
# `error` 로 전달된다.
BacktestUnprocessableDetail: TypeAlias = Annotated[
    BacktestRunInvalidDetail
    | BacktestStrategyRequiresUpgradeDetail
    | PortfolioStrategyInvalidDetail,
    Field(discriminator="code"),
]


@dataclass(frozen=True)
class BacktestUnprocessableResponse:
    detail: BacktestUnprocessableDetail


Backtest422Response: TypeAlias = BacktestUnprocessableResponse | RequestValidationResponse


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
