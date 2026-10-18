"""추적·미리보기와 기존 내부 파이프라인이 같은 파라미터 해소를 사용하는지 검증한다."""

from dataclasses import replace

import pytest

from strategy_workbench.adapters.outbound.engine_portfolio.facade.bridge import (
    BacktestEnginePortfolioAdapter,
)
from strategy_workbench.adapters.outbound.equity_mock.facade.provider import MockEquityDataAdapter
from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.portfolio_design.facade.design import (
    PortfolioDesignService,
    PortfolioPipelineOptions,
    PortfolioPreviewRequest,
)
from strategy_workbench.application.portfolio_design.facade.trace import (
    StrategyTraceRequest,
    StrategyTraceService,
)
from strategy_workbench.domain.strategy.facade.provenance import InlineDraft
from strategy_workbench.domain.strategy.facade.specification import (
    InvalidParameterValueError,
    ParameterValue,
)
from tests.application.test_run_parameter_values import _ENVIRONMENT, _portfolio, _spec
from tests.integration.test_strategy_trace_preflight import _NoRawAdapter


def test_public_preview_parameters_match_legacy_pipeline_without_changing_document() -> None:
    portfolio = _portfolio()
    request = PortfolioPreviewRequest(_spec(), environment=_ENVIRONMENT)
    default = portfolio.preview(request)
    explicit_default = portfolio.preview(replace(request, parameter_values={"scale": 1}))
    flipped = portfolio.preview(replace(request, parameter_values={"scale": -1}))
    legacy = portfolio.run_pipeline(
        request, options=PortfolioPipelineOptions(parameter_values={"scale": -1})
    )
    assert default == explicit_default
    assert default.parameter_values == {"scale": 1.0}
    assert flipped == legacy.preview
    assert type(flipped.parameter_values["scale"]) is float
    assert flipped.tape.tape_hash != default.tape.tape_hash
    assert flipped.tape.strategy_hash == default.tape.strategy_hash
    assert _spec().parameters[0].default == 1.0


@pytest.mark.parametrize(
    "parameters", [{"scale": True}, {"scale": 2}, {"scale": "bad"}, {"unknown": 1}]
)
@pytest.mark.parametrize("operation", ["preview", "preflight", "trace"])
def test_bad_parameters_fail_before_metadata_and_raw_reads(
    parameters: dict[str, ParameterValue],
    operation: str,
) -> None:
    source = _NoRawAdapter()
    portfolio = PortfolioDesignService(
        source,
        BacktestEnginePortfolioAdapter(),
        factor_metadata=source,
        factor_registry_version="test-registry",
    )
    request = PortfolioPreviewRequest(
        _spec(), environment=_ENVIRONMENT, parameter_values=parameters
    )
    with pytest.raises(InvalidParameterValueError):
        if operation == "trace":
            StrategyTraceService(
                portfolio, InMemoryStrategyRepository(), MockEquityDataAdapter.demo()
            ).trace(
                StrategyTraceRequest(
                    InlineDraft(_spec(), "inline_draft"),
                    (),
                    "scaled_momentum",
                    environment=_ENVIRONMENT,
                    parameter_values=parameters,
                )
            )
        elif operation == "preflight":
            portfolio.preflight(request)
        else:
            portfolio.preview(request)
    assert not source.raw_called
    assert not source.metadata_called


def test_conflicting_public_and_legacy_values_fail_closed_before_data() -> None:
    source = _NoRawAdapter()
    portfolio = PortfolioDesignService(
        source,
        BacktestEnginePortfolioAdapter(),
        factor_metadata=source,
        factor_registry_version="test-registry",
    )
    with pytest.raises(InvalidParameterValueError, match="parameter_id=scale"):
        portfolio.run_pipeline(
            PortfolioPreviewRequest(
                _spec(), environment=_ENVIRONMENT, parameter_values={"scale": -1}
            ),
            options=PortfolioPipelineOptions(parameter_values={"scale": 1}),
        )
    assert not source.raw_called
    assert not source.metadata_called


def test_equal_normalized_public_and_legacy_values_use_one_result() -> None:
    portfolio = _portfolio()
    request = PortfolioPreviewRequest(
        _spec(), environment=_ENVIRONMENT, parameter_values={"scale": -1}
    )
    expected = portfolio.preview(request)
    actual = portfolio.run_pipeline(
        request, options=PortfolioPipelineOptions(parameter_values={"scale": -1.0})
    )
    assert actual.preview == expected
