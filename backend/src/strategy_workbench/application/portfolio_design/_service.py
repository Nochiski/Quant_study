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

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import date
from typing import TypeVar

from strategy_workbench.application.factor_research.facade.ports import (
    FactorMetadataPort,
    FactorMetadataSnapshot,
)
from strategy_workbench.domain.equity.facade.research_data import DataLoadStatus
from strategy_workbench.domain.factor.facade.evaluation import (
    FactorFieldValue,
    FactorObservation,
    FactorValue,
    NonFiniteFactorCalculationError,
    evaluate_factor_graph,
)
from strategy_workbench.domain.factor.facade.expression import NodeValueType
from strategy_workbench.domain.factor.facade.planning import (
    FactorExecutionPlan,
    InvalidFactorGraphError,
    ResolvedFactorParameter,
    compile_factor_plan,
)
from strategy_workbench.domain.factor.facade.trace import (
    FactorTrace,
    evaluate_factor_graph_with_trace,
)
from strategy_workbench.domain.factor.facade.validation import (
    FactorValidationSeverity,
)
from strategy_workbench.domain.factor.facade.validation import (
    required_field_ids as factor_required_field_ids,
)
from strategy_workbench.domain.portfolio.facade.construction import (
    NonFinitePortfolioCalculationError,
    PortfolioConstructionTrace,
    PortfolioFactorValue,
    PortfolioFieldValue,
    PortfolioObservation,
    compile_rebalance_schedule,
    compile_target_tape,
    compile_target_tape_with_trace,
)
from strategy_workbench.domain.strategy.facade.specification import StrategySpec
from strategy_workbench.domain.strategy.facade.validation import (
    StrategyValidation,
    ValidationSeverity,
    semantic_issue,
    validate_strategy,
)

from ._models import (
    EngineCompatibility,
    PortfolioPipelineOptions,
    PortfolioPreview,
    PortfolioPreviewRequest,
    PortfolioStartingHolding,
)
from .ports.outgoing.engine_portfolio import EnginePortfolioPort
from .ports.outgoing.raw_observations import (
    CancellableRawObservationPort,
    RawObservation,
    RawObservationContractViolation,
    RawObservationPort,
    RawObservationQuery,
    RawObservationSet,
)

_T = TypeVar("_T")
_CHECKPOINT_BATCH = 256


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


class PortfolioSnapshotMismatchError(RawObservationContractError):
    """Factor contracts and raw values came from different dataset snapshots."""

    def __init__(self, *, expected: str, actual: str) -> None:
        super().__init__(
            "portfolio metadata/raw observation snapshot mismatch ??"
            f"expected_data_snapshot_id={expected!r} actual_data_snapshot_id={actual!r}"
        )
        self.expected = expected
        self.actual = actual


class IncompatiblePortfolioRequestError(ValueError):
    """The selected execution engine cannot implement this strategy."""

    def __init__(self, compatibility: EngineCompatibility) -> None:
        super().__init__("strategy exceeds engine capabilities")
        self.compatibility = compatibility


class PortfolioPipelineCancelledError(RuntimeError):
    """Cooperative cancellation observed between bounded pipeline stages."""


class InvalidPortfolioTraceSelectionError(ValueError):
    """The requested factor/node projection is not in the compiled execution plan."""


class TraceObservationCapabilityError(RuntimeError):
    """The configured raw source cannot provide a cooperatively cancellable trace."""

    capability = "raw_observation.cancellation"

    def __init__(self, adapter_type: str) -> None:
        super().__init__(
            "raw observation adapter lacks the cancellable trace capability — "
            f"capability={self.capability!r} adapter_type={adapter_type!r}"
        )
        self.adapter_type = adapter_type


@dataclass(frozen=True)
class FactorEvaluationRecord:
    """One factor's plan and evaluated values, kept for parity checks and the debug trace."""

    factor_id: str
    plan: FactorExecutionPlan
    values: tuple[FactorValue, ...]
    trace: FactorTrace | None = None


@dataclass(frozen=True)
class PortfolioPipelineResult:
    data_snapshot_id: str
    factor_evaluations: tuple[FactorEvaluationRecord, ...]
    raw_observations: tuple[RawObservation, ...]
    observations: tuple[PortfolioObservation, ...]
    construction_trace: PortfolioConstructionTrace | None
    preview: PortfolioPreview


class PortfolioDesignService:
    def __init__(
        self,
        observation_source: RawObservationPort,
        engine_portfolio: EnginePortfolioPort,
        *,
        factor_metadata: FactorMetadataPort,
        factor_registry_version: str,
    ) -> None:
        self._observation_source = observation_source
        self._engine_portfolio = engine_portfolio
        self._factor_metadata = factor_metadata
        self._factor_registry_version = factor_registry_version

    def preview(self, request: PortfolioPreviewRequest) -> PortfolioPreview:
        return self.run_pipeline(request).preview

    def run_pipeline(
        self,
        request: PortfolioPreviewRequest,
        *,
        options: PortfolioPipelineOptions | None = None,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> PortfolioPipelineResult:
        pipeline_options = options or PortfolioPipelineOptions()

        def checkpoint() -> None:
            _raise_if_cancelled(cancelled)

        spec = request.spec
        validation = validate_strategy(spec)
        if not validation.valid:
            raise InvalidPortfolioRequestError(validation)
        checkpoint()

        engine = self._engine_portfolio.assess(spec)
        if pipeline_options.require_engine_compatible and not engine.compatible:
            raise IncompatiblePortfolioRequestError(engine)
        trace_requested = (
            pipeline_options.trace_selection is not None
            or pipeline_options.construction_trace_selection is not None
        )
        if trace_requested and not isinstance(
            self._observation_source, CancellableRawObservationPort
        ):
            raise TraceObservationCapabilityError(type(self._observation_source).__name__)

        metadata = self._factor_metadata.resolve_factor_fields(
            tuple(
                sorted(
                    {
                        field_id
                        for factor in spec.factors.factors
                        for field_id in factor_required_field_ids(factor.graph)
                    }
                )
            )
        )
        plans = self._plans(spec, metadata)
        _reject_non_numeric_factor_outputs(spec, plans)
        _reject_saved_references(spec, plans)
        _validate_trace_selection(pipeline_options, plans)
        checkpoint()
        raw_query = RawObservationQuery(
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
        try:
            if isinstance(self._observation_source, CancellableRawObservationPort):
                raw = self._observation_source.load_raw_observations_cancellable(
                    raw_query, checkpoint=checkpoint
                )
            else:
                raw = self._observation_source.load_raw_observations(raw_query)
            # Normal construction already validates the immutable value. Recheck at the consumer
            # boundary so a foreign/stale adapter cannot bypass the current port contract.
            raw.validate_contract(checkpoint=checkpoint)
        except RawObservationContractViolation as error:
            raise RawObservationContractError(str(error)) from error
        checkpoint()
        if not raw.ok:
            raise RawObservationUnavailableError(raw.status, raw.detail)
        if raw.data_snapshot_id != metadata.data_snapshot_id:
            raise PortfolioSnapshotMismatchError(
                expected=metadata.data_snapshot_id,
                actual=raw.data_snapshot_id,
            )
        _reject_sessions_outside_strategy_range(raw, spec, checkpoint=checkpoint)
        schedule = compile_rebalance_schedule(spec, raw.sessions, checkpoint=checkpoint)
        _validate_loaded_trace_scope(
            pipeline_options,
            raw,
            first_signal_as_of=schedule.first_signal_as_of,
            checkpoint=checkpoint,
        )
        checkpoint()
        factor_observations = tuple(
            _to_factor_observation(item, checkpoint=checkpoint)
            for item in _checkpointed(raw.observations, checkpoint)
        )
        parameters = tuple(
            ResolvedFactorParameter(parameter.parameter_id, parameter.default)
            for parameter in spec.parameters
        )
        evaluations: list[FactorEvaluationRecord] = []
        for factor_index, factor in enumerate(spec.factors.factors):
            checkpoint()
            trace = None
            try:
                if factor.factor_id == pipeline_options.trace_factor_id:
                    evaluation, trace = evaluate_factor_graph_with_trace(
                        factor.graph,
                        observations=factor_observations,
                        parameters=parameters,
                        selection=pipeline_options.trace_selection,
                        checkpoint=checkpoint,
                    )
                else:
                    evaluation = evaluate_factor_graph(
                        factor.graph,
                        observations=factor_observations,
                        parameters=parameters,
                        checkpoint=checkpoint,
                    )
            except NonFiniteFactorCalculationError as error:
                node_index = next(
                    index
                    for index, node in enumerate(factor.graph.nodes)
                    if node.node_id == error.node_id
                )
                issue = semantic_issue(
                    "strategy.expression.calculation_non_finite",
                    f"factors.factors.{factor_index}.graph.nodes.{node_index}",
                    str(error),
                    node_id=error.node_id,
                )
                raise InvalidPortfolioRequestError(
                    StrategyValidation(valid=False, issues=(issue,))
                ) from error
            evaluations.append(
                FactorEvaluationRecord(
                    factor_id=factor.factor_id,
                    plan=plans[factor.factor_id],
                    values=evaluation.values,
                    trace=trace,
                )
            )
        evaluation_records = tuple(evaluations)
        checkpoint()
        observations = _to_portfolio_observations(
            raw,
            evaluation_records,
            pipeline_options.starting_holdings,
            checkpoint=checkpoint,
        )
        checkpoint()
        try:
            if pipeline_options.construction_trace_selection is None:
                tape = compile_target_tape(
                    spec,
                    data_snapshot_id=raw.data_snapshot_id,
                    sessions=raw.sessions,
                    observations=observations,
                    schedule=schedule,
                    checkpoint=checkpoint,
                )
                construction_trace = None
            else:
                compiled = compile_target_tape_with_trace(
                    spec,
                    data_snapshot_id=raw.data_snapshot_id,
                    sessions=raw.sessions,
                    observations=observations,
                    schedule=schedule,
                    trace_selection=pipeline_options.construction_trace_selection,
                    checkpoint=checkpoint,
                )
                tape = compiled.tape
                construction_trace = compiled.trace
        except NonFinitePortfolioCalculationError as error:
            issue = semantic_issue(
                "strategy.expression.calculation_non_finite",
                "portfolio",
                str(error),
            )
            raise InvalidPortfolioRequestError(
                StrategyValidation(valid=False, issues=(issue,))
            ) from error
        preview = PortfolioPreview(
            tape=tape,
            engine=engine,
            warnings=raw.warnings,
        )
        return PortfolioPipelineResult(
            data_snapshot_id=raw.data_snapshot_id,
            factor_evaluations=evaluation_records,
            raw_observations=raw.observations,
            observations=observations,
            construction_trace=construction_trace,
            preview=preview,
        )

    def _plans(
        self, spec: StrategySpec, metadata: FactorMetadataSnapshot
    ) -> dict[str, FactorExecutionPlan]:
        parameter_ids = tuple(parameter.parameter_id for parameter in spec.parameters)
        factor_ids = tuple(factor.factor_id for factor in spec.factors.factors)
        plans: dict[str, FactorExecutionPlan] = {}
        issues = []
        for factor_index, factor in enumerate(spec.factors.factors):
            try:
                plans[factor.factor_id] = compile_factor_plan(
                    factor.graph,
                    registry_version=self._factor_registry_version,
                    fields=metadata.fields,
                    parameter_ids=parameter_ids,
                    factor_ids=factor_ids,
                    require_field_metadata=True,
                )
            except InvalidFactorGraphError as error:
                issues.extend(
                    semantic_issue(
                        factor_issue.code,
                        f"factors.factors.{factor_index}.graph.{factor_issue.path}",
                        factor_issue.message,
                        severity=(
                            ValidationSeverity.ERROR
                            if factor_issue.severity is FactorValidationSeverity.ERROR
                            else ValidationSeverity.WARNING
                        ),
                        node_id=factor_issue.node_id,
                    )
                    for factor_issue in error.validation.issues
                )
        if issues:
            raise InvalidPortfolioRequestError(
                StrategyValidation(valid=False, issues=tuple(issues))
            )
        return plans


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
    return tuple(sorted(fields))


def _validate_trace_selection(
    options: PortfolioPipelineOptions, plans: dict[str, FactorExecutionPlan]
) -> None:
    factor_id = options.trace_factor_id
    selection = options.trace_selection
    if factor_id is None or selection is None:
        return
    plan = plans.get(factor_id)
    if plan is None:
        raise InvalidPortfolioTraceSelectionError(
            "trace references an unknown factor before observation loading — "
            f"factor_id={factor_id!r} available={sorted(plans)!r}"
        )
    reachable = {step.node_id for step in plan.steps}
    unknown = set(selection.node_ids or ()) - reachable
    if unknown:
        raise InvalidPortfolioTraceSelectionError(
            "trace references unknown or unreachable nodes before observation loading — "
            f"factor_id={factor_id!r} node_ids={sorted(unknown)!r}"
        )


def _validate_loaded_trace_scope(
    options: PortfolioPipelineOptions,
    raw: RawObservationSet,
    *,
    first_signal_as_of: date | None,
    checkpoint: Callable[[], None],
) -> None:
    """Reject an untraceable date/security scope before any FactorGraph calculation.

    A non-member row is still a valid point-in-time security observation and must not be confused
    with an unknown identifier. Absence of the row is an invalid scope, not a successful missing
    value: missing fields on a present row remain represented by the trace value status.
    """
    factor_selection = options.trace_selection
    construction_selection = options.construction_trace_selection
    if factor_selection is None and construction_selection is None:
        return
    scopes = []
    if factor_selection is not None:
        scopes.append(
            (
                "factor",
                tuple(_checkpointed(factor_selection.as_of or (), checkpoint)),
                tuple(_checkpointed(factor_selection.security_ids or (), checkpoint)),
            )
        )
    if construction_selection is not None:
        scopes.append(
            (
                "construction",
                (construction_selection.as_of,),
                tuple(_checkpointed(construction_selection.security_ids, checkpoint)),
            )
        )
    selected_dates = {selected_date for _, dates, _ in scopes for selected_date in dates}
    missing_sessions = selected_dates - set(_checkpointed(raw.sessions, checkpoint))
    if missing_sessions:
        raise InvalidPortfolioTraceSelectionError(
            "trace as_of dates are not trading sessions in the selected snapshot — "
            f"as_of={sorted(missing_sessions)!r} snapshot={raw.data_snapshot_id!r}"
        )

    if any(security_ids for _, _, security_ids in scopes):
        observed_pairs = {
            (item.as_of, item.security_id) for item in _checkpointed(raw.observations, checkpoint)
        }
        observed_ids = {security_id for _, security_id in observed_pairs}
        for scope_name, dates, security_ids in scopes:
            if not security_ids:
                continue
            missing_pairs = (
                {
                    (selected_date, security_id)
                    for selected_date in dates
                    for security_id in security_ids
                }
                - observed_pairs
                if dates
                else set()
            )
            unmatched = set(security_ids) - observed_ids if not dates else set()
            if not missing_pairs and not unmatched:
                continue
            raise InvalidPortfolioTraceSelectionError(
                "trace security ids have no observation rows at the selected as_of — "
                f"scope={scope_name!r} security_ids={sorted(unmatched)!r} "
                f"date_security_pairs={sorted(missing_pairs)!r} "
                f"snapshot={raw.data_snapshot_id!r}"
            )
    if options.starting_holdings:
        if first_signal_as_of is None:
            raise InvalidPortfolioTraceSelectionError(
                "trace starting holdings require a TargetTape signal frame — "
                f"snapshot={raw.data_snapshot_id!r}"
            )
        observed_ids = {
            item.security_id
            for item in _checkpointed(raw.observations, checkpoint)
            if item.as_of == first_signal_as_of
        }
        unmatched_holdings = {
            holding.security_id for holding in _checkpointed(options.starting_holdings, checkpoint)
        } - observed_ids
        if unmatched_holdings:
            raise InvalidPortfolioTraceSelectionError(
                "trace starting holdings are absent from the first TargetTape signal frame — "
                f"security_ids={sorted(unmatched_holdings)!r} "
                f"signal_as_of={first_signal_as_of} snapshot={raw.data_snapshot_id!r}"
            )


def _raise_if_cancelled(cancelled: Callable[[], bool]) -> None:
    if cancelled():
        raise PortfolioPipelineCancelledError("portfolio pipeline trace was cancelled")


def _checkpointed(items: Iterable[_T], checkpoint: Callable[[], None]) -> Iterator[_T]:
    for index, item in enumerate(items):
        if index % _CHECKPOINT_BATCH == 0:
            checkpoint()
        yield item


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


def _reject_non_numeric_factor_outputs(
    spec: StrategySpec, plans: dict[str, FactorExecutionPlan]
) -> None:
    """A FactorSignal is a per-security score, not an arbitrary typed expression.

    Generic factor explain remains free to describe scalar, boolean, and group outputs. The
    executable portfolio boundary accepts only numeric series: group/boolean values are not
    scores, and a scalar cannot distinguish securities. This check must inspect the compiled plan
    so it consumes the same metadata-derived contract as execution.
    """
    issues = tuple(
        semantic_issue(
            "strategy.expression.output_type",
            f"factors.factors.{factor_index}.graph.output_node_id",
            "FactorSignal output must be numeric_series for portfolio/backtest execution: "
            f"actual={output.output_type!r}",
            node_id=plan.output_node_id,
        )
        for factor_index, factor in enumerate(spec.factors.factors)
        for plan in (plans[factor.factor_id],)
        for output in (next(step for step in plan.steps if step.node_id == plan.output_node_id),)
        if output.output_type != NodeValueType.NUMERIC_SERIES.value
    )
    if issues:
        raise InvalidPortfolioRequestError(StrategyValidation(valid=False, issues=issues))


def _reject_sessions_outside_strategy_range(
    raw: RawObservationSet,
    spec: StrategySpec,
    *,
    checkpoint: Callable[[], None],
) -> None:
    """Sessions must stay inside `spec.data.start..end` (fail-closed, D-004).

    A wider answer is fail-open: `compile_target_tape` would emit frames whose execution date has
    no bar in the backtest dataset, which `application/backtest_run` queries for the strategy
    range alone.
    """
    outside = tuple(
        session
        for session in _checkpointed(raw.sessions, checkpoint)
        if not spec.data.start <= session <= spec.data.end
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


def _to_factor_observation(
    item: RawObservation, *, checkpoint: Callable[[], None]
) -> FactorObservation:
    for field in _checkpointed(item.fields, checkpoint):
        if field.available_date > item.as_of:
            raise LookAheadViolationError(
                "raw field published after its observation date — "
                f"field_id={field.field_id!r} security_id={item.security_id!r} "
                f"as_of={item.as_of} available_date={field.available_date}"
            )
    return FactorObservation(
        as_of=item.as_of,
        security_id=item.security_id,
        fields=tuple(
            FactorFieldValue(field.field_id, field.value)
            for field in _checkpointed(item.fields, checkpoint)
        ),
        # Membership travels with the row so cross-sectional operators score members against
        # members only (D-001); dropping non-members here would truncate time-series lookbacks.
        universe_member=item.universe_member,
    )


def _to_portfolio_observations(
    raw: RawObservationSet,
    evaluations: tuple[FactorEvaluationRecord, ...],
    starting_holdings: tuple[PortfolioStartingHolding, ...] | None = None,
    *,
    checkpoint: Callable[[], None] = lambda: None,
) -> tuple[PortfolioObservation, ...]:
    in_range = set(_checkpointed(raw.sessions, checkpoint))
    values_by_key: dict[tuple[str, date, str], float | None] = {}
    for record in _checkpointed(evaluations, checkpoint):
        for value in _checkpointed(record.values, checkpoint):
            values_by_key[(record.factor_id, value.as_of, value.security_id)] = value.value
    opening_weights = (
        None
        if starting_holdings is None
        else {
            holding.security_id: holding.weight
            for holding in _checkpointed(starting_holdings, checkpoint)
        }
    )
    return tuple(
        PortfolioObservation(
            as_of=item.as_of,
            security_id=item.security_id,
            universe_member=item.universe_member,
            factor_values=tuple(
                PortfolioFactorValue(
                    factor_id=record.factor_id,
                    value=values_by_key.get((record.factor_id, item.as_of, item.security_id)),
                    available_date=_latest_input_publication(
                        item, record.plan, checkpoint=checkpoint
                    ),
                )
                for record in _checkpointed(evaluations, checkpoint)
            ),
            fields=tuple(
                PortfolioFieldValue(field.field_id, field.value, field.available_date)
                for field in _checkpointed(item.fields, checkpoint)
            ),
            sector_id=item.sector_id,
            previous_weight=(
                item.previous_weight
                if opening_weights is None
                else opening_weights.get(item.security_id, 0.0)
            ),
        )
        for item in _checkpointed(raw.observations, checkpoint)
        if item.as_of in in_range
    )


def _latest_input_publication(
    item: RawObservation,
    plan: FactorExecutionPlan,
    *,
    checkpoint: Callable[[], None],
) -> date:
    """Publication date of the factor value: the latest among the fields its plan reads."""
    required = set(plan.required_field_ids)
    return max(
        (
            field.available_date
            for field in _checkpointed(item.fields, checkpoint)
            if field.field_id in required
        ),
        default=item.as_of,
    )
