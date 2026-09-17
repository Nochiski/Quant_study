"""Typed 422 details for the strategy document upgrade endpoint (P1-04)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from ._execution_error_contract import RequestValidationResponse


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
    StrategyDocumentNotUpgradeableResponse
    | StrategyDocumentUpgradeDriftResponse
    | RequestValidationResponse
)
