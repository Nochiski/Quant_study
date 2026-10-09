"""전략 문서 엔드포인트의 typed 422 detail.

P1-04가 upgrade에 만들었고, save·revise는 Phase 1 감사 이월로 같은 모양을 선언한다.
"""

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

# save·revise의 422: 런타임에 내던 `_invalid_document_compiled` 모양(`strategy_document.invalid`)을
# OpenAPI에도 선언한다(Phase 1 감사 우선순위 2 — upgrade만 typed였던 비대칭). compile은 진단을
# 200으로 돌려주므로 422는 요청 봉투 오류뿐이라 그대로 둔다.
StrategyDocumentSave422Response: TypeAlias = (
    StrategyDocumentInvalidResponse | RequestValidationResponse
)
