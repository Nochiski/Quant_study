from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta

from strategy_workbench.domain.strategy.facade.explanation import (
    StrategyExplanation,
    explain_strategy,
)
from strategy_workbench.domain.strategy.facade.specification import (
    DataStep,
    EligibilityStep,
    ExecutionStep,
    FactorDirection,
    FactorGraph,
    FactorSignal,
    FactorStep,
    FieldNode,
    Market,
    PortfolioStep,
    RiskStep,
    SignalStep,
    StrategyIdentity,
    StrategySpec,
    strategy_spec_hash,
)
from strategy_workbench.domain.strategy.facade.validation import (
    StrategyValidation,
    validate_strategy,
)

from .ports.outgoing.strategy_repository import (
    RevisionOrigin,
    RevisionProvenance,
    StrategyRepositoryPort,
    StrategyRevisionRecord,
)


@dataclass(frozen=True)
class SavedStrategy:
    spec: StrategySpec
    spec_hash: str


class InvalidStrategyError(ValueError):
    def __init__(self, validation: StrategyValidation) -> None:
        super().__init__("strategy validation failed")
        self.validation = validation


class StrategyDesignService:
    def __init__(
        self,
        repository: StrategyRepositoryPort,
        *,
        new_id: Callable[[], str],
        today: Callable[[], date] = date.today,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._new_id = new_id
        self._today = today
        self._now = now

    def template(self) -> StrategySpec:
        end = self._today()
        return StrategySpec(
            identity=StrategyIdentity(strategy_id="draft", revision=0),
            title="새 팩터 전략",
            description="",
            data=DataStep(
                market=Market.KRX,
                start=end - timedelta(days=365 * 5),
                end=end,
                universe_id="krx.common-stock",
            ),
            eligibility=EligibilityStep(),
            factors=FactorStep(
                factors=(
                    FactorSignal(
                        factor_id="price.close",
                        label="종가",
                        direction=FactorDirection.HIGH,
                        weight=1.0,
                        graph=FactorGraph(
                            nodes=(
                                FieldNode(
                                    node_id="close",
                                    field_id="price.close",
                                    kind="field",
                                ),
                            ),
                            output_node_id="close",
                        ),
                    ),
                )
            ),
            signal=SignalStep(),
            portfolio=PortfolioStep(),
            risk=RiskStep(),
            execution=ExecutionStep(),
        )

    def validate(self, spec: StrategySpec) -> StrategyValidation:
        return validate_strategy(spec)

    def explain(self, spec: StrategySpec) -> StrategyExplanation:
        return explain_strategy(spec)

    def create(self, draft: StrategySpec) -> SavedStrategy:
        self._require_valid(draft)
        saved = replace(
            draft,
            identity=StrategyIdentity(
                strategy_id=self._new_id(),
                revision=1,
                schema_version=draft.identity.schema_version,
            ),
        )
        record = self._legacy_record(saved)
        self._repository.add(record)
        return SavedStrategy(record.spec, record.spec_hash)

    def get(self, strategy_id: str, revision: int | None = None) -> SavedStrategy:
        record = self._repository.get(strategy_id, revision)
        return SavedStrategy(record.spec, record.spec_hash)

    def revise(
        self,
        strategy_id: str,
        draft: StrategySpec,
        *,
        expected_revision: int,
    ) -> SavedStrategy:
        self._require_valid(draft)
        saved = replace(
            draft,
            identity=StrategyIdentity(
                strategy_id=strategy_id,
                revision=expected_revision + 1,
                schema_version=draft.identity.schema_version,
            ),
        )
        record = self._legacy_record(saved)
        self._repository.append(record, expected_revision=expected_revision)
        return SavedStrategy(record.spec, record.spec_hash)

    def _legacy_record(self, spec: StrategySpec) -> StrategyRevisionRecord:
        """JSON spec API revisions carry no source text (authoring ADR D9 migration)."""
        return StrategyRevisionRecord(
            spec=spec,
            spec_hash=strategy_spec_hash(spec),
            source=None,
            provenance=RevisionProvenance(RevisionOrigin.LEGACY_JSON, self._now()),
        )

    @staticmethod
    def _require_valid(spec: StrategySpec) -> None:
        validation = validate_strategy(spec)
        if not validation.valid:
            raise InvalidStrategyError(validation)
