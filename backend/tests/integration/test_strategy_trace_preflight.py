"""Trace rejection and cancellation happen before raw observation calculation."""

from __future__ import annotations

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
from strategy_workbench.application.portfolio_design.facade.design import (
    EngineCapabilityIssue,
    EngineCompatibility,
    EngineRequirementSummary,
    IncompatiblePortfolioRequestError,
    PortfolioDesignService,
)
from strategy_workbench.application.portfolio_design.facade.ports import RawObservationQuery
from strategy_workbench.application.portfolio_design.facade.trace import (
    InvalidStrategyTraceRequestError,
    StrategyTraceCancelledError,
    StrategyTraceRequest,
    StrategyTraceService,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.strategy.facade.provenance import InlineDraft


class _NoRawAdapter:
    def __init__(self) -> None:
        self._delegate = MockEquityDataAdapter.demo()
        self.raw_called = False

    def resolve_factor_fields(self, field_ids: tuple[str, ...]) -> FactorMetadataSnapshot:
        return self._delegate.resolve_factor_fields(field_ids)

    def load_raw_observations(self, query: RawObservationQuery):
        self.raw_called = True
        raise AssertionError(f"raw calculation should not start — query={query!r}")


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

    def load_raw_observations(self, query: RawObservationQuery):
        self.raw_called = True
        result = self._delegate.load_raw_observations(query)
        self._stop.set()
        return result


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
        security_ids=("005930",),
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
