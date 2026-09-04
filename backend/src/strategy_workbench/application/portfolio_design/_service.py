"""Portfolio design use case: the truthful preview pipeline (WORKFLOW P1.5-04).

    raw PIT observations (adapter)  ─▶  FactorGraph evaluation per factor (domain.factor)
                                   ─▶  PortfolioObservation with *evaluated* factor values
                                   ─▶  compile_target_tape (domain.portfolio)

Factor values in every CandidateDecision are FactorGraph outputs; the composite score is the
weighted, direction-signed sum the portfolio compiler derives from them. Backtest runs consume the
same TargetTape, so preview and backtest cannot diverge. No adapter is allowed to invent factor
values.

PIT enforcement is owned here, and it covers *dated* values only: a raw field published after its
`as_of` is an adapter contract violation and raises `LookAheadViolationError` (fail-closed, loud).
Each factor value carries the latest publication date among the fields its plan reads, so the
portfolio compiler's FUTURE_DATA guard stays meaningful. `universe_member` and `sector_id` have no
publication date, so no guard here or downstream can catch a retroactive membership or sector
change; the observation adapter owns their as_of vintage (D-006).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from strategy_workbench.domain.equity.facade.research_data import DataLoadStatus
from strategy_workbench.domain.factor.facade.evaluation import (
    FactorFieldValue,
    FactorObservation,
    FactorValue,
    evaluate_factor_graph,
)
from strategy_workbench.domain.factor.facade.expression import FactorGraph, GroupNode
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
    semantic_issue,
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


class RawObservationUnavailableError(RuntimeError):
    """The observation source could not serve the query (unknown universe/field, no data)."""

    def __init__(self, status: DataLoadStatus, detail: str | None) -> None:
        super().__init__(f"raw observations unavailable — status={status.value} detail={detail}")
        self.status = status
        self.detail = detail


class RawObservationContractError(RuntimeError):
    """The observation adapter answered outside its declared contract.

    Fail-loud on purpose and deliberately not mapped to an HTTP status: a contract violation is an
    adapter bug, not a user input error, and answering 4xx would let a wrong tape look accepted.
    """


class LookAheadViolationError(RawObservationContractError):
    """An adapter returned a field published after the observation date (contract bug)."""


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
        _reject_saved_references(spec, plans)
        raw = self._observation_source.load_raw_observations(
            RawObservationQuery(
                market=spec.data.market.value,
                universe_id=spec.data.universe_id,
                start=spec.data.start,
                end=spec.data.end,
                field_ids=_required_field_ids(spec, plans),
                # Plans count as_of itself; the port counts sessions strictly before start.
                history_sessions_before_start=max(
                    (plan.minimum_history_sessions - 1 for plan in plans.values()), default=0
                ),
            )
        )
        if not raw.ok:
            raise RawObservationUnavailableError(raw.status, raw.detail)
        _reject_sessions_outside_strategy_range(raw, spec)
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
            warnings=raw.warnings,
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
    return {node.group_field_id for node in graph.nodes if isinstance(node, GroupNode)}


def _reject_saved_references(spec: StrategySpec, plans: dict[str, FactorExecutionPlan]) -> None:
    # TODO(PLAN P5-03): evaluate referenced factors/subgraphs in topological order instead.
    issues = tuple(
        # The domain owns the code registry; minting an issue here goes through the same gate.
        semantic_issue(
            "strategy.expression.reference_unsupported",
            f"factors.factors.{index}.graph",
            "저장된 팩터/서브그래프 참조는 아직 preview/backtest에서 계산되지 않습니다: "
            f"factor_ids={plan.referenced_factor_ids} "
            f"subgraph_ids={plan.referenced_subgraph_ids}",
        )
        for index, factor in enumerate(spec.factors.factors)
        for plan in (plans[factor.factor_id],)
        if plan.referenced_factor_ids or plan.referenced_subgraph_ids
    )
    if issues:
        raise InvalidPortfolioRequestError(StrategyValidation(valid=False, issues=issues))


def _reject_sessions_outside_strategy_range(raw: RawObservationSet, spec: StrategySpec) -> None:
    """Sessions must stay inside `spec.data.start..end` (fail-closed, D-004).

    A wider answer is fail-open: `compile_target_tape` would emit frames whose execution date has
    no bar in the backtest dataset, which `application/backtest_run` queries for the strategy
    range alone.
    """
    outside = tuple(
        session for session in raw.sessions if not spec.data.start <= session <= spec.data.end
    )
    if not outside:
        return
    raise RawObservationContractError(
        "raw observation sessions fall outside the requested strategy range — "
        f"expected={spec.data.start}..{spec.data.end} "
        f"actual={raw.sessions[0]}..{raw.sessions[-1]} "
        f"outside={outside[:5]} outside_count={len(outside)} "
        f"universe_id={spec.data.universe_id!r} snapshot={raw.data_snapshot_id!r}"
    )


def _to_factor_observation(item: RawObservation) -> FactorObservation:
    for field in item.fields:
        if field.available_date > item.as_of:
            raise LookAheadViolationError(
                "raw field published after its observation date — "
                f"field_id={field.field_id!r} security_id={item.security_id!r} "
                f"as_of={item.as_of} available_date={field.available_date}"
            )
    return FactorObservation(
        as_of=item.as_of,
        security_id=item.security_id,
        fields=tuple(FactorFieldValue(field.field_id, field.value) for field in item.fields),
        # Membership travels with the row so cross-sectional operators score members against
        # members only (D-001); dropping non-members here would truncate time-series lookbacks.
        universe_member=item.universe_member,
    )


def _to_portfolio_observations(
    raw: RawObservationSet, evaluations: tuple[FactorEvaluationRecord, ...]
) -> tuple[PortfolioObservation, ...]:
    in_range = set(raw.sessions)
    values_by_key: dict[tuple[str, date, str], float | None] = {}
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
                    available_date=_latest_input_publication(item, record.plan),
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


def _latest_input_publication(item: RawObservation, plan: FactorExecutionPlan) -> date:
    """Publication date of the factor value: the latest among the fields its plan reads."""
    required = set(plan.required_field_ids)
    return max(
        (field.available_date for field in item.fields if field.field_id in required),
        default=item.as_of,
    )
