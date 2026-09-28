from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from strategy_workbench.domain.strategy.facade.explanation import (
    StrategyExplanation,
    explain_strategy,
)
from strategy_workbench.domain.strategy.facade.specification import (
    EligibilityStep,
    FactorDirection,
    FactorGraph,
    FactorSignal,
    FieldNode,
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
    StrategyRevisionConflictError,
    StrategyRevisionRecord,
)


@dataclass(frozen=True)
class SavedStrategy:
    spec: StrategySpec
    spec_hash: str
    # 은퇴한 schema 버전으로 동결된 revision (spec D2). spec은 업그레이드 변환을 거친 1.1 모양이고
    # spec_hash는 저장된 값이라 서로 재계산 관계가 아니다.
    requires_upgrade: bool


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
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._new_id = new_id
        self._now = now

    def template(self) -> StrategySpec:
        """새 전략 초안. 1.2 부터 실행 설정(시장·기간·유니버스·체결)은 담지 않는다.

        팩터 하나를 남겨 두는 이유는 이 초안이 곧바로 `create()` 로 들어가기 때문이다 —
        비우면 `strategy.factor.required` 로 저장이 막힌다. 편집 화면이 여는 빈 시작 문서
        (`schema_version`·`title` 만)는 저장 전 원문이라 규칙이 다르다(P4-04).
        """
        return StrategySpec(
            identity=StrategyIdentity(strategy_id="draft", revision=0),
            title="새 팩터 전략",
            description="",
            eligibility=EligibilityStep(),
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
            ),
            signal=SignalStep(),
            portfolio=PortfolioStep(),
            risk=RiskStep(),
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
        return SavedStrategy(
            spec=record.spec, spec_hash=record.spec_hash, requires_upgrade=record.requires_upgrade
        )

    def get(self, strategy_id: str, revision: int | None = None) -> SavedStrategy:
        record = self._repository.get(strategy_id, revision)
        return SavedStrategy(
            spec=record.spec, spec_hash=record.spec_hash, requires_upgrade=record.requires_upgrade
        )

    def revise(
        self,
        strategy_id: str,
        draft: StrategySpec,
        *,
        expected_revision: int,
    ) -> SavedStrategy:
        self._require_valid(draft)
        latest = self._repository.get(strategy_id)
        if latest.source is not None:
            raise StrategyRevisionConflictError(
                "strategy is authored as a document; revise it through the document API — "
                f"strategy_id={strategy_id} latest_revision={latest.revision}",
                latest_revision=latest.revision,
            )
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
        return SavedStrategy(
            spec=record.spec, spec_hash=record.spec_hash, requires_upgrade=record.requires_upgrade
        )

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
