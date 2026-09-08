"""Inbound-owned error contract for starting a backtest run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal, TypeAlias

from pydantic import Field

from ._execution_error_contract import (
    PortfolioDataUnavailableDetail,
    PortfolioRawObservationInvalidDetail,
    PortfolioStrategyInvalidDetail,
    RequestValidationResponse,
)


@dataclass(frozen=True)
class BacktestRunInvalidDetail:
    code: Literal["backtest.run.invalid"]
    message: str


BacktestUnprocessableDetail: TypeAlias = Annotated[
    BacktestRunInvalidDetail
    | PortfolioStrategyInvalidDetail
    | PortfolioDataUnavailableDetail
    | PortfolioRawObservationInvalidDetail,
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
