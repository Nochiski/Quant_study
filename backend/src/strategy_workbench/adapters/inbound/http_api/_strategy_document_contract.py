"""Typed 422 details for the strategy document upgrade endpoint (P1-04)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from strategy_workbench.application.strategy_authoring.facade.ports import SourceDiagnostic

from ._execution_error_contract import RequestValidationResponse


@dataclass(frozen=True)
class StrategyDocumentInvalidDetail:
    """`_invalid_document_compiled`가 내보내는 모양: compile·save·revise·upgrade가 공유한다."""

    code: Literal["strategy_document.invalid"]
    source_hash: str
    schema_version: str | None
    diagnostics: tuple[SourceDiagnostic, ...]


@dataclass(frozen=True)
class StrategyDocumentInvalidResponse:
    detail: StrategyDocumentInvalidDetail


@dataclass(frozen=True)
class StrategyDocumentNotUpgradeableDetail:
    code: Literal["strategy_document.not_upgradeable"]
    schema_version: str | None
    message: str


@dataclass(frozen=True)
class StrategyDocumentNotUpgradeableResponse:
    detail: StrategyDocumentNotUpgradeableDetail


@dataclass(frozen=True)
class StrategyDocumentUpgradeDriftDetail:
    code: Literal["strategy_document.upgrade_drift"]
    pointer: str
    message: str


@dataclass(frozen=True)
class StrategyDocumentUpgradeDriftResponse:
    detail: StrategyDocumentUpgradeDriftDetail


StrategyDocumentUpgrade422Response: TypeAlias = (
    StrategyDocumentInvalidResponse
    | StrategyDocumentNotUpgradeableResponse
    | StrategyDocumentUpgradeDriftResponse
    | RequestValidationResponse
)
