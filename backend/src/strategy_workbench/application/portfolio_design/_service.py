"""Portfolio design use case: the truthful preview pipeline (WORKFLOW P1.5-04).

    raw PIT observations (adapter)  ─▶  FactorGraph evaluation per factor (domain.factor)
                                   ─▶  PortfolioObservation with *evaluated* factor values
                                   ─▶  compile_target_tape (domain.portfolio)

Factor values in every CandidateDecision are FactorGraph outputs; the composite score is the
weighted, direction-signed sum the portfolio compiler derives from them. Backtest runs consume the
same TargetTape, so preview and backtest cannot diverge. No adapter is allowed to invent factor
values.
"""

from __future__ import annotations

from dataclasses import dataclass

from strategy_workbench.domain.factor.facade.evaluation import (
    FactorFieldValue,
    FactorObservation,
    FactorValue,
    evaluate_factor_graph,
)
from strategy_workbench.domain.factor.facade.expression import FactorGraph, FieldNode, GroupNode
from strategy_workbench.domain.factor.facade.planning import (
    FactorExecutionPlan,
    ResolvedFactorParameter,
    compile_factor_plan,
)
from strategy_workbench.domain.portfolio.facade.construction import (
    PortfolioFactorValue,
    PortfolioFieldValue,
    PortfolioObservation,
    compile_target_tape,
)
from strategy_workbench.domain.strategy.facade.specification import StrategySpec
from strategy_workbench.domain.strategy.facade.validation import (
    StrategyValidation,
    validate_strategy,
)

from ._models import PortfolioPreview, PortfolioPreviewRequest
from .ports.outgoing.engine_portfolio import EnginePortfolioPort
from .ports.outgoing.raw_observations import (
    RawObservation,
    RawObservationPort,
    RawObservationQuery,
    RawObservationSet,
)


class InvalidPortfolioRequestError(ValueError):
    def __init__(self, validation: StrategyValidation) -> None:
        super().__init__("portfolio preview requires a valid StrategySpec")
        self.validation = validation


@dataclass(frozen=True)
class FactorEvaluationRecord:
    """One factor's plan and evaluated values, kept for parity checks and the debug trace."""

    factor_id: str
    plan: FactorExecutionPlan
    values: tuple[FactorValue, ...]


@dataclass(frozen=True)
class PortfolioPipelineResult:
    data_snapshot_id: str
    factor_evaluations: tuple[FactorEvaluationRecord, ...]
    observations: tuple[PortfolioObservation, ...]
    preview: PortfolioPreview


class PortfolioDesignService:
    def __init__(
        self,
        observation_source: RawObservationPort,
        engine_portfolio: EnginePortfolioPort,
        *,
        factor_registry_version: str,
    ) -> None:
        self._observation_source = observation_source
        self._engine_portfolio = engine_portfolio
        self._factor_registry_version = factor_registry_version

    def preview(self, request: PortfolioPreviewRequest) -> PortfolioPreview:
        return self.run_pipeline(request).preview

    def run_pipeline(self, request: PortfolioPreviewRequest) -> PortfolioPipelineResult:
        spec = request.spec
        validation = validate_strategy(spec)
        if not validation.valid:
            raise InvalidPortfolioRequestError(validation)

        plans = self._plans(spec)
        raw = self._observation_source.load_raw_observations(
            RawObservationQuery(
                start=spec.data.start,
                end=spec.data.end,
                field_ids=_required_field_ids(spec, plans),
                minimum_history_sessions=max(
                    (plan.minimum_history_sessions for plan in plans.values()), default=0
                ),
            )
        )
        factor_observations = tuple(_to_factor_observation(item) for item in raw.observations)
        parameters = tuple(
            ResolvedFactorParameter(parameter.parameter_id, parameter.default)
            for parameter in spec.parameters
        )
        evaluations = tuple(
            FactorEvaluationRecord(
                factor_id=factor.factor_id,
                plan=plans[factor.factor_id],
                values=evaluate_factor_graph(
                    factor.graph, observations=factor_observations, parameters=parameters
                ).values,
            )
            for factor in spec.factors.factors
        )
        observations = _to_portfolio_observations(raw, evaluations)
        preview = PortfolioPreview(
            tape=compile_target_tape(
                spec,
                data_snapshot_id=raw.data_snapshot_id,
                sessions=raw.sessions,
                observations=observations,
            ),
            engine=self._engine_portfolio.assess(spec),
        )
        return PortfolioPipelineResult(
            data_snapshot_id=raw.data_snapshot_id,
            factor_evaluations=evaluations,
            observations=observations,
            preview=preview,
        )

    def _plans(self, spec: StrategySpec) -> dict[str, FactorExecutionPlan]:
        parameter_ids = tuple(parameter.parameter_id for parameter in spec.parameters)
        factor_ids = tuple(factor.factor_id for factor in spec.factors.factors)
        return {
            factor.factor_id: compile_factor_plan(
                factor.graph,
                registry_version=self._factor_registry_version,
                parameter_ids=parameter_ids,
                factor_ids=factor_ids,
            )
            for factor in spec.factors.factors
        }


def _required_field_ids(
    spec: StrategySpec, plans: dict[str, FactorExecutionPlan]
) -> tuple[str, ...]:
    fields: set[str] = {rule.field_id for rule in spec.eligibility.rules}
    fields.update(
        field_id
        for field_id in (
            spec.portfolio.liquidity_field_id,
            spec.signal.regime_field_id,
            spec.risk.risk_field_id,
        )
        if field_id is not None
    )
    for plan in plans.values():
        fields.update(plan.required_field_ids)
    for factor in spec.factors.factors:
        fields.update(_group_field_ids(factor.graph))
    return tuple(sorted(fields))


def _group_field_ids(graph: FactorGraph) -> set[str]:
    return {
        node.group_field_id
        for node in graph.nodes
        if isinstance(node, GroupNode) and not isinstance(node, FieldNode)
    }


def _to_factor_observation(item: RawObservation) -> FactorObservation:
    return FactorObservation(
        as_of=item.as_of,
        security_id=item.security_id,
        fields=tuple(FactorFieldValue(field.field_id, field.value) for field in item.fields),
    )


def _to_portfolio_observations(
    raw: RawObservationSet, evaluations: tuple[FactorEvaluationRecord, ...]
) -> tuple[PortfolioObservation, ...]:
    in_range = set(raw.sessions)
    values_by_key: dict[tuple[str, object, str], float | None] = {}
    for record in evaluations:
        for value in record.values:
            values_by_key[(record.factor_id, value.as_of, value.security_id)] = value.value
    return tuple(
        PortfolioObservation(
            as_of=item.as_of,
            security_id=item.security_id,
            universe_member=item.universe_member,
            factor_values=tuple(
                PortfolioFactorValue(
                    factor_id=record.factor_id,
                    value=values_by_key.get((record.factor_id, item.as_of, item.security_id)),
                    available_date=item.as_of,
                )
                for record in evaluations
            ),
            fields=tuple(
                PortfolioFieldValue(field.field_id, field.value, field.available_date)
                for field in item.fields
            ),
            sector_id=item.sector_id,
            previous_weight=item.previous_weight,
        )
        for item in raw.observations
        if item.as_of in in_range
    )
