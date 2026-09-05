from __future__ import annotations

from dataclasses import replace
from datetime import date

from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.strategy.facade.specification import (
    FactorGraph,
    FactorSignal,
    FactorStep,
    FloatParameter,
    ParameterNode,
    StrategyIdentity,
    strategy_spec_hash,
)


def _template():
    service = StrategyDesignService(
        InMemoryStrategyRepository(),
        new_id=lambda: "strategy-1",
        today=lambda: date(2026, 9, 3),
    )
    return service.template()


def test_canonical_hash_ignores_storage_identity_but_tracks_semantics() -> None:
    draft = _template()
    saved = replace(draft, identity=StrategyIdentity("strategy-1", 7))
    renamed = replace(saved, title="다른 전략")

    assert strategy_spec_hash(draft) == strategy_spec_hash(saved)
    assert strategy_spec_hash(saved) != strategy_spec_hash(renamed)


def test_validation_reports_unknown_parameter_and_invalid_bounds() -> None:
    spec = _template()
    bad_factor = replace(
        spec.factors.factors[0],
        graph=FactorGraph(
            nodes=(
                ParameterNode(
                    node_id="window",
                    parameter_id="missing",
                    kind="parameter",
                ),
            ),
            output_node_id="window",
        ),
    )
    invalid = replace(
        spec,
        factors=FactorStep((bad_factor,)),
        parameters=(
            FloatParameter(
                parameter_id="lookback",
                default=5.0,
                minimum=20.0,
                maximum=10.0,
                kind="float",
            ),
        ),
    )

    validation = StrategyDesignService(
        InMemoryStrategyRepository(), new_id=lambda: "unused"
    ).validate(invalid)

    assert not validation.valid
    assert {issue.code for issue in validation.issues} == {
        "strategy.expression.parameter_missing",
        "strategy.parameter.bounds",
        "strategy.parameter.default",
    }


def test_explanation_preserves_pipeline_order() -> None:
    spec = _template()
    explanation = StrategyDesignService(
        InMemoryStrategyRepository(), new_id=lambda: "unused"
    ).explain(spec)

    assert tuple(step.stage for step in explanation.steps) == (
        "data",
        "signal",
        "portfolio",
        "risk",
        "execution",
    )
    assert isinstance(spec.factors.factors[0], FactorSignal)
