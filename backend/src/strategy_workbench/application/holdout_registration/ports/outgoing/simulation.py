"""모의 절차가 요구하는 출처 고정·관측·원장 계약. 실제 엔진 권한은 제공하지 않는다."""

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol

from strategy_workbench.domain.backtest.facade.holdout import (
    FrozenHoldoutCandidate,
    HoldoutRegistration,
    HoldoutSimulationClaim,
    HoldoutSimulationObservation,
)
from strategy_workbench.domain.strategy.facade.provenance import SavedRevisionReference
from strategy_workbench.domain.strategy.facade.specification import ParameterValue


class HoldoutSimulationRejectedError(ValueError):
    pass


class HoldoutSimulationStorageError(RuntimeError):
    """저장/commit 결과가 불확실하다. 이 호출은 executor를 재전송하지 않는다."""


class FrozenHoldoutSourcePort(Protocol):
    def pin(
        self,
        reference: SavedRevisionReference,
        selection_id: str,
        parameter_values: Mapping[str, ParameterValue],
    ) -> FrozenHoldoutCandidate:
        """정확한 원문/spec/선택 정합을 원래 owner로 검증하고 전부 고정한다."""
        ...


class HoldoutSimulationExecutorPort(Protocol):
    def execute(self, registration: HoldoutRegistration) -> HoldoutSimulationObservation:
        """테스트 합성 지표만 반환한다. 실제 원천/일반 BacktestRunService 호출은 허용하지 않는다."""
        ...


class HoldoutSimulationLedgerPort(Protocol):
    def register(self, registration: HoldoutRegistration) -> None: ...
    def registration(self, registration_id: str) -> HoldoutRegistration: ...
    def get(self, request_id: str) -> HoldoutSimulationClaim | None: ...
    def claim(self, claim: HoldoutSimulationClaim) -> bool:
        """동일 요청 replay면 False. 신규 claim은 durable commit 성공 반환 후 True다."""
        ...

    def finish(self, claim: HoldoutSimulationClaim) -> None: ...
    def interrupt_claimed(self, completed_at: datetime) -> None: ...
