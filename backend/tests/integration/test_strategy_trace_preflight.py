"""Trace rejection and cancellation happen before raw observation calculation."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from threading import Event
from types import SimpleNamespace
from typing import cast

import pytest

from strategy_workbench.adapters.outbound.engine_portfolio.facade.bridge import (
    BacktestEnginePortfolioAdapter,
)
from strategy_workbench.adapters.outbound.equity_mock.facade.provider import (
    MockEquityDataAdapter,
)
from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.factor_research.facade.ports import FactorMetadataSnapshot
from strategy_workbench.application.portfolio_design import _service as portfolio_module
from strategy_workbench.application.portfolio_design import _trace_service as trace_module
from strategy_workbench.application.portfolio_design.facade.design import (
    EngineCapabilityIssue,
    EngineCompatibility,
    EngineRequirementSummary,
    IncompatiblePortfolioRequestError,
    InvalidPortfolioRequestError,
    PortfolioDesignService,
    PortfolioPipelineCancelledError,
    PortfolioPipelineOptions,
    PortfolioStartingHolding,
    TraceObservationCapabilityError,
)
from strategy_workbench.application.portfolio_design.facade.ports import (
    RawObservationQuery,
    RawObservationSet,
)
from strategy_workbench.application.portfolio_design.facade.trace import (
    InvalidStrategyTraceRequestError,
    StrategyTraceCancelledError,
    StrategyTraceCapabilityError,
    StrategyTraceRequest,
    StrategyTraceService,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.factor.facade.evaluation import FactorEvaluation, FactorValue
from strategy_workbench.domain.factor.facade.expression import ConstantNode
from strategy_workbench.domain.factor.facade.trace import TraceSelection
from strategy_workbench.domain.portfolio.facade.construction import (
    PortfolioFieldValue,
    PortfolioObservation,
)
from strategy_workbench.domain.strategy.facade.provenance import InlineDraft
from strategy_workbench.domain.strategy.facade.specification import (
    ChoiceParameter,
    ComparisonOperator,
    EligibilityRule,
    EligibilityStep,
    FloatParameter,
    RebalanceFrequency,
)


class _NoRawAdapter:
    def __init__(self) -> None:
        self._delegate = MockEquityDataAdapter.demo()
        self.raw_called = False
        self.metadata_called = False

    def resolve_factor_fields(self, field_ids: tuple[str, ...]) -> FactorMetadataSnapshot:
        self.metadata_called = True
        return self._delegate.resolve_factor_fields(field_ids)

    def load_raw_observations(self, query: RawObservationQuery):
        self.raw_called = True
        raise AssertionError(f"raw calculation should not start — query={query!r}")

    def load_raw_observations_cancellable(self, query: RawObservationQuery, *, checkpoint):
        return self.load_raw_observations(query)


class _RejectingEngine:
    def assess(self, spec):
        return EngineCompatibility(
            compatible=False,
            requirements=EngineRequirementSummary("EverySession", (), (), ("unsupported",)),
            issues=(EngineCapabilityIssue("feature", "unsupported", "not_implemented", None),),
        )

    def to_target_action(self, frame, *, max_participation=None):  # pragma: no cover
        raise AssertionError((frame, max_participation))


class _CancelAfterRawAdapter(_NoRawAdapter):
    def __init__(self, stop: Event) -> None:
        super().__init__()
        self._stop = stop

    def load_raw_observations_cancellable(self, query: RawObservationQuery, *, checkpoint):
        self.raw_called = True
        result = self._delegate.load_raw_observations_cancellable(query, checkpoint=checkpoint)
        self._stop.set()
        return result


class _CancelInsideRawAdapter(_NoRawAdapter):
    def __init__(self, stop: Event) -> None:
        super().__init__()
        self._stop = stop

    def load_raw_observations_cancellable(self, query: RawObservationQuery, *, checkpoint):
        self.raw_called = True
        self._stop.set()
        checkpoint()
        raise AssertionError("cancellation checkpoint should have interrupted raw loading")


class _LegacyRawAdapter:
    def __init__(self) -> None:
        self._delegate = MockEquityDataAdapter.demo()
        self.raw_called = False
        self.metadata_called = False

    def resolve_factor_fields(self, field_ids: tuple[str, ...]) -> FactorMetadataSnapshot:
        self.metadata_called = True
        return self._delegate.resolve_factor_fields(field_ids)

    def load_raw_observations(self, query: RawObservationQuery):
        self.raw_called = True
        return self._delegate.load_raw_observations(query)


class _SparseFirstSignalAdapter(_LegacyRawAdapter):
    def __init__(self, security_id: str) -> None:
        super().__init__()
        self._security_id = security_id

    def resolve_factor_fields(self, field_ids: tuple[str, ...]) -> FactorMetadataSnapshot:
        return self._delegate.resolve_factor_fields(field_ids)

    def load_raw_observations(self, query: RawObservationQuery):
        raw = super().load_raw_observations(query)
        first_signal = raw.sessions[0]
        return replace(
            raw,
            observations=tuple(
                observation
                for observation in raw.observations
                if not (
                    observation.as_of == first_signal
                    and observation.security_id == self._security_id
                )
            ),
        )

    def load_raw_observations_cancellable(self, query: RawObservationQuery, *, checkpoint):
        checkpoint()
        return self.load_raw_observations(query)


def _spec():
    return StrategyDesignService(
        InMemoryStrategyRepository(),
        new_id=lambda: "unused",
        today=lambda: date(2026, 9, 3),
    ).template()


def _request(spec, *, node_ids: tuple[str, ...] = ()) -> StrategyTraceRequest:
    factor = spec.factors.factors[0]
    return StrategyTraceRequest(
        strategy_source=InlineDraft(spec, "inline_draft"),
        as_of=spec.data.end,
        security_ids=("sec-005930-1",),
        factor_id=factor.factor_id,
        node_ids=node_ids,
    )


def _every_session_spec():
    spec = _spec()
    return replace(
        spec,
        portfolio=replace(
            spec.portfolio,
            rebalance=RebalanceFrequency.EVERY_N_SESSIONS,
            rebalance_every_n_sessions=1,
        ),
    )


def test_starting_holding_must_exist_on_the_actual_first_signal_frame() -> None:
    security_id = "sec-005930-1"
    spec = _every_session_spec()
    holding = PortfolioStartingHolding(security_id, 0.5)

    present_source = MockEquityDataAdapter.demo()
    present = StrategyTraceService(
        PortfolioDesignService(
            present_source,
            BacktestEnginePortfolioAdapter(),
            factor_metadata=present_source,
            factor_registry_version="factor-registry-v1",
        ),
        InMemoryStrategyRepository(),
    )
    accepted = present.trace(replace(_request(spec), starting_holdings=(holding,)))
    assert accepted.spec_hash

    sparse_source = _SparseFirstSignalAdapter(security_id)
    sparse = StrategyTraceService(
        PortfolioDesignService(
            sparse_source,
            BacktestEnginePortfolioAdapter(),
            factor_metadata=sparse_source,
            factor_registry_version="factor-registry-v1",
        ),
        InMemoryStrategyRepository(),
    )
    with pytest.raises(
        InvalidStrategyTraceRequestError,
        match="absent from the first TargetTape signal frame",
    ):
        sparse.trace(replace(_request(spec), starting_holdings=(holding,)))


def test_starting_holding_requires_at_least_one_target_tape_frame() -> None:
    source = MockEquityDataAdapter.demo()
    spec = _every_session_spec()
    spec = replace(spec, data=replace(spec.data, start=spec.data.end))
    service = StrategyTraceService(
        PortfolioDesignService(
            source,
            BacktestEnginePortfolioAdapter(),
            factor_metadata=source,
            factor_registry_version="factor-registry-v1",
        ),
        InMemoryStrategyRepository(),
    )

    with pytest.raises(
        InvalidStrategyTraceRequestError,
        match="require a TargetTape signal frame",
    ):
        service.trace(
            replace(
                _request(spec),
                starting_holdings=(PortfolioStartingHolding("sec-005930-1", 0.5),),
            )
        )


def test_engine_capability_failure_precedes_metadata_and_raw_calculation() -> None:
    source = _NoRawAdapter()
    portfolio = PortfolioDesignService(
        source,
        _RejectingEngine(),
        factor_metadata=source,
        factor_registry_version="factor-registry-v1",
    )
    service = StrategyTraceService(portfolio, InMemoryStrategyRepository())

    with pytest.raises(IncompatiblePortfolioRequestError):
        service.trace(_request(_spec()))
    assert source.raw_called is False


def test_trace_rejects_a_legacy_raw_adapter_before_metadata_or_raw_calculation() -> None:
    source = _LegacyRawAdapter()
    portfolio = PortfolioDesignService(
        source,
        BacktestEnginePortfolioAdapter(),
        factor_metadata=source,
        factor_registry_version="factor-registry-v1",
    )

    service = StrategyTraceService(portfolio, InMemoryStrategyRepository())

    with pytest.raises(StrategyTraceCapabilityError) as excinfo:
        service.trace(_request(_spec()))

    assert excinfo.value.capability == TraceObservationCapabilityError.capability
    assert source.metadata_called is False
    assert source.raw_called is False


def test_unknown_node_and_pre_cancelled_request_never_load_raw_rows() -> None:
    source = _NoRawAdapter()
    portfolio = PortfolioDesignService(
        source,
        BacktestEnginePortfolioAdapter(),
        factor_metadata=source,
        factor_registry_version="factor-registry-v1",
    )
    service = StrategyTraceService(portfolio, InMemoryStrategyRepository())
    spec = _spec()

    with pytest.raises(InvalidStrategyTraceRequestError, match="unknown or unreachable"):
        service.trace(_request(spec, node_ids=("nope",)))
    assert source.raw_called is False

    with pytest.raises(StrategyTraceCancelledError, match="cancelled"):
        service.trace(_request(spec), cancelled=lambda: True)
    assert source.raw_called is False


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_inline_spec_fails_validation_before_raw_loading(value: float) -> None:
    source = _NoRawAdapter()
    portfolio = PortfolioDesignService(
        source,
        BacktestEnginePortfolioAdapter(),
        factor_metadata=source,
        factor_registry_version="factor-registry-v1",
    )
    service = StrategyTraceService(portfolio, InMemoryStrategyRepository())
    spec = _spec()
    spec = replace(spec, execution=replace(spec.execution, fee_bps=value))

    with pytest.raises(InvalidPortfolioRequestError):
        service.trace(_request(spec))

    assert source.raw_called is False
    assert source.metadata_called is False


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize(
    "location",
    ("factor_weight", "constant", "eligibility", "signal", "float_parameter", "choice"),
)
def test_all_non_finite_strategy_leaves_fail_before_metadata_and_raw(
    value: float, location: str
) -> None:
    source = _NoRawAdapter()
    portfolio = PortfolioDesignService(
        source,
        BacktestEnginePortfolioAdapter(),
        factor_metadata=source,
        factor_registry_version="factor-registry-v1",
    )
    service = StrategyTraceService(portfolio, InMemoryStrategyRepository())
    spec = _spec()
    factor = spec.factors.factors[0]
    if location == "factor_weight":
        spec = replace(
            spec,
            factors=replace(spec.factors, factors=(replace(factor, weight=value),)),
        )
    elif location == "constant":
        graph = replace(
            factor.graph,
            nodes=(*factor.graph.nodes, ConstantNode("bad", value, "constant")),
        )
        spec = replace(
            spec,
            factors=replace(spec.factors, factors=(replace(factor, graph=graph),)),
        )
    elif location == "eligibility":
        spec = replace(
            spec,
            eligibility=EligibilityStep(
                (EligibilityRule("price.close", ComparisonOperator.GREATER_THAN, value),)
            ),
        )
    elif location == "signal":
        spec = replace(spec, signal=replace(spec.signal, score_threshold=value))
    elif location == "float_parameter":
        spec = replace(
            spec,
            parameters=(FloatParameter("scale", value, 0.0, 1.0, "float"),),
        )
    else:
        spec = replace(
            spec,
            parameters=(ChoiceParameter("scale", 1.0, (1.0, value), "choice"),),
        )

    with pytest.raises(InvalidPortfolioRequestError) as excinfo:
        service.trace(_request(spec))

    assert "strategy.number.non_finite" in {issue.code for issue in excinfo.value.validation.issues}
    assert source.metadata_called is False
    assert source.raw_called is False


def test_cancellation_after_raw_load_stops_before_factor_evaluation() -> None:
    stop = Event()
    source = _CancelAfterRawAdapter(stop)
    portfolio = PortfolioDesignService(
        source,
        BacktestEnginePortfolioAdapter(),
        factor_metadata=source,
        factor_registry_version="factor-registry-v1",
    )
    service = StrategyTraceService(portfolio, InMemoryStrategyRepository())

    with pytest.raises(StrategyTraceCancelledError, match="cancelled"):
        service.trace(_request(_spec()), cancelled=stop.is_set)
    assert source.raw_called is True


def test_raw_port_checkpoint_interrupts_loading() -> None:
    stop = Event()
    source = _CancelInsideRawAdapter(stop)
    portfolio = PortfolioDesignService(
        source,
        BacktestEnginePortfolioAdapter(),
        factor_metadata=source,
        factor_registry_version="factor-registry-v1",
    )
    service = StrategyTraceService(portfolio, InMemoryStrategyRepository())

    with pytest.raises(StrategyTraceCancelledError, match="cancelled"):
        service.trace(_request(_spec()), cancelled=stop.is_set)
    assert source.raw_called is True


def test_raw_numeric_validation_cancellation_stops_before_factor_evaluation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from strategy_workbench.application.portfolio_design.ports.outgoing import (
        raw_observations as raw_observation_module,
    )

    stop = Event()
    numeric_checks = 0
    original = raw_observation_module._finite_number

    def latch_on_first_numeric(value: object) -> bool:
        nonlocal numeric_checks
        numeric_checks += 1
        stop.set()
        return original(value)

    def fail_later_stage(*args, **kwargs):
        raise AssertionError("factor and TargetTape stages must not start after raw cancellation")

    monkeypatch.setattr(raw_observation_module, "_finite_number", latch_on_first_numeric)
    monkeypatch.setattr(portfolio_module, "evaluate_factor_graph", fail_later_stage)
    monkeypatch.setattr(portfolio_module, "evaluate_factor_graph_with_trace", fail_later_stage)
    monkeypatch.setattr(portfolio_module, "compile_target_tape", fail_later_stage)
    source = MockEquityDataAdapter.demo()
    service = StrategyTraceService(
        PortfolioDesignService(
            source,
            BacktestEnginePortfolioAdapter(),
            factor_metadata=source,
            factor_registry_version="factor-registry-v1",
        ),
        InMemoryStrategyRepository(),
    )

    with pytest.raises(StrategyTraceCancelledError, match="cancelled"):
        service.trace(_request(_spec()), cancelled=stop.is_set)

    assert numeric_checks == 1


def test_trace_scope_cancellation_stops_after_first_observation() -> None:
    spec = _spec()
    source = MockEquityDataAdapter.demo()
    raw = source.load_raw_observations(
        RawObservationQuery(
            market=spec.data.market.value,
            universe_id=spec.data.universe_id,
            start=spec.data.start,
            end=spec.data.end,
            field_ids=("price.close",),
        )
    )
    stop = Event()
    consumed = 0

    def latching_observations():
        nonlocal consumed
        for observation in raw.observations:
            consumed += 1
            if consumed == 1:
                stop.set()
            yield observation

    scoped_raw = cast(
        RawObservationSet,
        SimpleNamespace(
            sessions=raw.sessions,
            observations=latching_observations(),
            data_snapshot_id=raw.data_snapshot_id,
        ),
    )
    options = PortfolioPipelineOptions(
        trace_factor_id=spec.factors.factors[0].factor_id,
        trace_selection=TraceSelection(
            as_of=(spec.data.end,),
            security_ids=("sec-005930-1",),
        ),
    )

    def checkpoint() -> None:
        if stop.is_set():
            raise PortfolioPipelineCancelledError("cancelled in trace scope")

    with pytest.raises(PortfolioPipelineCancelledError, match="trace scope"):
        portfolio_module._validate_loaded_trace_scope(
            options,
            scoped_raw,
            first_signal_as_of=spec.data.end,
            checkpoint=checkpoint,
        )

    assert consumed == 1


def test_factor_output_cancellation_stops_before_target_and_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = _spec()
    factor = spec.factors.factors[0]
    stop = Event()
    consumed = 0

    def latching_values():
        nonlocal consumed
        for index in range(1_000):
            consumed += 1
            if consumed == 1:
                stop.set()
            yield FactorValue(spec.data.end, f"security-{index:04d}", float(index))

    def evaluate_with_latching_values(*args, **kwargs):
        return (
            FactorEvaluation(
                factor.graph.output_node_id,
                cast(tuple[FactorValue, ...], latching_values()),
            ),
            None,
        )

    def fail_later_stage(*args, **kwargs):
        raise AssertionError("TargetTape and trace projection must not start after cancellation")

    monkeypatch.setattr(
        portfolio_module,
        "evaluate_factor_graph_with_trace",
        evaluate_with_latching_values,
    )
    monkeypatch.setattr(portfolio_module, "compile_target_tape", fail_later_stage)
    monkeypatch.setattr(trace_module, "_raw_projection", fail_later_stage)
    source = MockEquityDataAdapter.demo()
    service = StrategyTraceService(
        PortfolioDesignService(
            source,
            BacktestEnginePortfolioAdapter(),
            factor_metadata=source,
            factor_registry_version="factor-registry-v1",
        ),
        InMemoryStrategyRepository(),
    )

    with pytest.raises(StrategyTraceCancelledError, match="cancelled"):
        service.trace(_request(spec), cancelled=stop.is_set)

    assert consumed == 1


def test_factor_evaluator_checkpoint_stops_before_target_tape(monkeypatch) -> None:
    source = MockEquityDataAdapter.demo()
    portfolio = PortfolioDesignService(
        source,
        BacktestEnginePortfolioAdapter(),
        factor_metadata=source,
        factor_registry_version="factor-registry-v1",
    )
    service = StrategyTraceService(portfolio, InMemoryStrategyRepository())
    entered_evaluator = Event()
    from strategy_workbench.domain.factor import _trace as factor_trace_module

    original = factor_trace_module._compute_nodes

    def enter_then_compute(*args, **kwargs):
        entered_evaluator.set()
        return original(*args, **kwargs)

    def fail_target_tape(*args, **kwargs):
        raise AssertionError("TargetTape must not compile after evaluator cancellation")

    monkeypatch.setattr(factor_trace_module, "_compute_nodes", enter_then_compute)
    monkeypatch.setattr(
        portfolio_module,
        "compile_target_tape",
        fail_target_tape,
    )

    with pytest.raises(StrategyTraceCancelledError, match="cancelled"):
        service.trace(_request(_spec()), cancelled=entered_evaluator.is_set)


def test_raw_projection_stops_at_one_lookahead_row_and_reports_truncation() -> None:
    request = replace(_request(_spec()), include_raw=True)
    observation = PortfolioObservation(
        as_of=request.as_of,
        security_id=request.security_ids[0],
        universe_member=True,
        factor_values=(),
        fields=tuple(
            PortfolioFieldValue(f"field.{index:04d}", float(index), request.as_of)
            for index in range(trace_module.MAX_RAW_ROWS + 1)
        ),
    )

    rows, truncated = trace_module._raw_projection((observation,), request)

    assert len(rows) == trace_module.MAX_RAW_ROWS
    assert rows[-1].field_id == "field.1999"
    assert truncated is True


def test_starting_holdings_none_preserves_adapter_book_while_empty_overrides_it() -> None:
    spec = _spec()
    raw = MockEquityDataAdapter.demo().load_raw_observations(
        RawObservationQuery(
            market=spec.data.market.value,
            universe_id=spec.data.universe_id,
            start=spec.data.start,
            end=spec.data.end,
            field_ids=("price.close",),
        )
    )
    first = raw.observations[0]
    seeded = replace(first, previous_weight=0.37)
    raw = replace(raw, observations=(seeded, *raw.observations[1:]))
    evaluation = ()

    preserved = portfolio_module._to_portfolio_observations(raw, evaluation, None)
    overridden = portfolio_module._to_portfolio_observations(raw, evaluation, ())

    assert preserved[0].previous_weight == 0.37
    assert overridden[0].previous_weight == 0.0
