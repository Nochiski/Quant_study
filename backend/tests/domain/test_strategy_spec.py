from __future__ import annotations

from dataclasses import replace

from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.strategy.facade.specification import (
    FactorGraph,
    FactorSignal,
    FloatParameter,
    ParameterNode,
    SignalNormalization,
    StrategyIdentity,
    strategy_spec_hash,
)


def _template():
    service = StrategyDesignService(
        InMemoryStrategyRepository(),
        new_id=lambda: "strategy-1",
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
        spec.factors[0],
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
        factors=(bad_factor,),
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

    summaries = {step.stage: step.summary for step in explanation.steps}
    # 요약 문구는 모델이 소유한 사실만 말한다.
    # 1.2에서 문서를 떠난 data·execution 단계는 설명하지 않는다 — 실행 설정의 owner 는
    # `RunEnvironment` 하나다(P2-03).
    # 새 문서의 기본 정규화는 `rank` 라서 요약도 결합 전 정규화를 말한다(P2-04).
    assert summaries["signal"] == "팩터 1개를 횡단면 순위로 맞춘 뒤 방향·가중치 가중합으로 결합"
    assert tuple(step.stage for step in explanation.steps) == (
        "signal",
        "portfolio",
        "risk",
    )
    assert isinstance(spec.factors[0], FactorSignal)


def test_every_normalization_value_has_a_signal_summary() -> None:
    """정규화 값을 늘리고 문장을 빠뜨리면 `/strategies/explain` 이 `KeyError` 로 500 을 낸다."""
    base = _template()

    summaries = {
        method: next(
            step.summary
            for step in StrategyDesignService(InMemoryStrategyRepository(), new_id=lambda: "unused")
            .explain(replace(base, signal=replace(base.signal, normalization=method)))
            .steps
            if step.stage == "signal"
        )
        for method in SignalNormalization
    }

    assert len(set(summaries.values())) == len(SignalNormalization)
