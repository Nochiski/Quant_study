"""Trace rejection and cancellation happen before raw observation calculation."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from threading import Event

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
    TraceObservationCapabilityError,
)
from strategy_workbench.application.portfolio_design.facade.ports import RawObservationQuery
from strategy_workbench.application.portfolio_design.facade.trace import (
    InvalidStrategyTraceRequestError,
    StrategyTraceCancelledError,
    StrategyTraceCapabilityError,
    StrategyTraceRequest,
    StrategyTraceService,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.factor.facade.expression import ConstantNode
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
