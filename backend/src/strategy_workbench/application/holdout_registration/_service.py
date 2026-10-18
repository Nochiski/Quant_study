"""실제 봉인 권위와 연결하지 않는 사전등록·모의 관측 유스케이스."""

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.backtest.facade.holdout import (
    HoldoutCriterion,
    HoldoutRegistration,
    HoldoutSimulationClaim,
    SimulationClaimStatus,
    evaluate_holdout_simulation,
)
from strategy_workbench.domain.canonical.facade.encoding import canonical_payload_json
from strategy_workbench.domain.strategy.facade.provenance import SavedRevisionReference
from strategy_workbench.domain.strategy.facade.specification import (
    ParameterValue,
    resolve_parameter_values,
)

from .ports.outgoing.simulation import (
    FrozenHoldoutSourcePort,
    HoldoutSimulationExecutorPort,
    HoldoutSimulationLedgerPort,
    HoldoutSimulationRejectedError,
    HoldoutSimulationStorageError,
)


class HoldoutRegistrationService:
    def __init__(
        self,
        source: FrozenHoldoutSourcePort,
        executor: HoldoutSimulationExecutorPort,
        ledger: HoldoutSimulationLedgerPort,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        new_id: Callable[[], str] = lambda: str(uuid4()),
    ) -> None:
        self._source = source
        self._executor = executor
        self._ledger = ledger
        self._now = now
        self._new_id = new_id
        self._storage_unknown = False

    def register(
        self,
        reference: SavedRevisionReference,
        selection_id: str,
        environment: RunEnvironment,
        criteria: tuple[HoldoutCriterion, ...],
        *,
        parameter_values: Mapping[str, ParameterValue] | None = None,
    ) -> HoldoutRegistration:
        candidate = self._source.pin(reference, selection_id, parameter_values or {})
        if candidate.reference != reference or candidate.selection_id != selection_id:
            raise HoldoutSimulationRejectedError("pinned source differs from requested identity")
        resolved = resolve_parameter_values(candidate.spec.parameters, parameter_values or {})
        if canonical_payload_json(dict(candidate.parameter_values)) != canonical_payload_json(
            resolved
        ):
            raise HoldoutSimulationRejectedError("pinned parameters differ from requested values")
        registration = HoldoutRegistration(
            self._new_id(), self._now(), candidate, environment, criteria
        )
        self._ledger.register(registration)
        return registration

    def registration(self, registration_id: str) -> HoldoutRegistration:
        return self._ledger.registration(registration_id)

    def get(self, request_id: str) -> HoldoutSimulationClaim | None:
        return self._ledger.get(request_id)

    def simulate(self, registration_id: str, request_id: str) -> HoldoutSimulationClaim:
        registration_id, request_id = str(UUID(registration_id)), str(UUID(request_id))
        if self._storage_unknown:
            raise HoldoutSimulationStorageError(
                "storage_unknown: manual recovery inspection required"
            )
        previous = self._ledger.get(request_id)
        if previous is not None:
            if previous.registration_id != registration_id:
                raise HoldoutSimulationRejectedError("request UUID belongs to another registration")
            return previous
        registration = self._ledger.registration(registration_id)
        claim = HoldoutSimulationClaim(
            request_id, registration_id, registration.registration_hash, self._now()
        )
        try:
            admitted = self._ledger.claim(claim)
        except HoldoutSimulationStorageError:
            self._storage_unknown = True
            raise
        if not admitted:
            existing = self._ledger.get(request_id)
            if existing is None:
                self._storage_unknown = True
                raise HoldoutSimulationStorageError(
                    "storage_unknown: committed claim cannot be read"
                )
            return existing
        # claim이 내구성 있게 commit됐다고 반환한 다음에만 합성 관측을 요청한다.
        try:
            observation = self._executor.execute(registration)
            result = evaluate_holdout_simulation(registration, observation)
            completed = replace(
                claim,
                status=SimulationClaimStatus.OBSERVED,
                completed_at=self._now(),
                result=result,
            )
        except Exception:
            completed = replace(
                claim, status=SimulationClaimStatus.INTERRUPTED_UNKNOWN, completed_at=self._now()
            )
        try:
            self._ledger.finish(completed)
        except HoldoutSimulationStorageError:
            self._storage_unknown = True
            raise
        return completed

    def recover(self) -> None:
        """작업자 시작 전 명시 복구 조회다. 소모된 claim은 재실행하지 않는다."""
        self._ledger.interrupt_claimed(self._now())
        self._storage_unknown = False
