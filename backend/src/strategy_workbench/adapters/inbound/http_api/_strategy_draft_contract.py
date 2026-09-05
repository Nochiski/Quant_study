from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from strategy_workbench.application.strategy_authoring.facade.authoring import StrategyDraft

from ._execution_error_contract import RequestValidationResponse


@dataclass(frozen=True)
class StrategyDraftErrorDetail:
    code: Literal["strategy.draft.not_found"]
    message: str


@dataclass(frozen=True)
class StrategyDraftErrorResponse:
    detail: StrategyDraftErrorDetail


@dataclass(frozen=True)
class StrategyDraftConflictDetail(StrategyDraftErrorDetail):
    code: Literal["strategy.draft.conflict"]
    current: StrategyDraft | None


@dataclass(frozen=True)
class StrategyDraftConflictResponse:
    detail: StrategyDraftConflictDetail


@dataclass(frozen=True)
class StrategyDraftInvalidDetail:
    code: Literal["strategy.draft.invalid"]
    message: str


@dataclass(frozen=True)
class StrategyDraftInvalidResponse:
    detail: StrategyDraftInvalidDetail


StrategyDraft422Response: TypeAlias = StrategyDraftInvalidResponse | RequestValidationResponse


__all__ = [
    "StrategyDraft422Response",
    "StrategyDraftConflictDetail",
    "StrategyDraftConflictResponse",
    "StrategyDraftErrorDetail",
    "StrategyDraftErrorResponse",
    "StrategyDraftInvalidDetail",
    "StrategyDraftInvalidResponse",
]
