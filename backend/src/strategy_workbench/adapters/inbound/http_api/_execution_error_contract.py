"""Shared inbound wire models for truthful execution failures.

The application owns validation/data failure semantics.  This module owns their HTTP envelope
once for every execution route so preview, trace, and backtest cannot invent incompatible DTOs.
FastAPI's malformed-request response is included separately because it has no coded application
detail to discriminate.
"""

from __future__ import annotations

from dataclasses import dataclass
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
class RequestValidationIssue:
    loc: tuple[str | int, ...]
    msg: str
    type: str


@dataclass(frozen=True)
class RequestValidationResponse:
    """FastAPI's malformed-envelope 422 shape, alongside coded application diagnostics."""

    detail: tuple[RequestValidationIssue, ...]


PortfolioUnprocessableDetail: TypeAlias = Annotated[
    PortfolioStrategyInvalidDetail | PortfolioDataUnavailableDetail,
    Field(discriminator="code"),
]


@dataclass(frozen=True)
class PortfolioUnprocessableResponse:
    detail: PortfolioUnprocessableDetail


Portfolio422Response: TypeAlias = PortfolioUnprocessableResponse | RequestValidationResponse
