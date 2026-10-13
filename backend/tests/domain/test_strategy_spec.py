from __future__ import annotations

import math
from dataclasses import replace

import pytest

from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.strategy.facade.specification import (
    ChoiceParameter,
    FactorGraph,
    FactorSignal,
    FloatParameter,
    IntegerParameter,
    InvalidParameterValueError,
    ParameterNode,
    SignalNormalization,
    StrategyIdentity,
    normalized_parameter_value,
    parameter_value_allowed,
    resolve_parameter_values,
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
        # 파라미터 노드 하나가 출력이라 종목을 가르지 못하는 scalar 다(P2-07 compile 게이트).
        "strategy.factor.output_type",
    }


def test_parameter_value_allowed_checks_type_range_and_choices_but_not_step() -> None:
    weight = FloatParameter(
        parameter_id="weight", default=0.15, minimum=0.1, maximum=0.3, kind="float", step=0.1
    )
    lookback = IntegerParameter(
        parameter_id="lookback", default=20, minimum=10, maximum=30, kind="integer", step=10
    )
    mode = ChoiceParameter(parameter_id="mode", default="a", choices=("a", "b"), kind="choice")

    # 간격(step)은 격자를 펼치는 폭이라 간격 밖 값도 허용한다.
    assert parameter_value_allowed(weight, 0.15)
    assert parameter_value_allowed(lookback, 15)
    assert not parameter_value_allowed(weight, 0.35)
    assert not parameter_value_allowed(lookback, 20.5)
    assert not parameter_value_allowed(lookback, True)
    assert parameter_value_allowed(mode, "b")
    assert not parameter_value_allowed(mode, "c")

    # 검증기는 같은 술어로 기본값을 보므로 간격 밖 기본값을 가진 저장 문서가 새로 깨지지 않는다.
    validation = StrategyDesignService(
        InMemoryStrategyRepository(), new_id=lambda: "unused"
    ).validate(replace(_template(), parameters=(weight,)))
    assert "strategy.parameter.default" not in {issue.code for issue in validation.issues}


def test_values_are_normalized_to_the_declared_type_without_bool_leaking() -> None:
    lookback = IntegerParameter(
        parameter_id="lookback", default=20, minimum=10, maximum=30, kind="integer"
    )
    weight = FloatParameter(
        parameter_id="weight", default=0.5, minimum=0.0, maximum=2.0, kind="float"
    )
    numeric = ChoiceParameter(parameter_id="n", default=1, choices=(1, 2.5), kind="choice")
    switch = ChoiceParameter(parameter_id="s", default=True, choices=(True, False), kind="choice")

    # 문서 hydrate 와 같은 규칙: 정수 칸 20.0 → 20, 실수 칸 1 → 1.0, 선택지는 문서의 선택지 값.
    for parameter, value, expected in (
        (lookback, 20.0, 20),
        (weight, 1, 1.0),
        (numeric, 1.0, 1),
        (numeric, 2.5, 2.5),
        (switch, False, False),
    ):
        normalized = normalized_parameter_value(parameter, value)
        assert normalized == expected and type(normalized) is type(expected), (parameter, value)
    # `True == 1`·`1 == True` 이지만 bool 과 숫자는 서로의 값이 아니다.
    assert normalized_parameter_value(numeric, True) is None
    assert normalized_parameter_value(switch, 1) is None
    assert normalized_parameter_value(lookback, True) is None
    assert normalized_parameter_value(weight, False) is None
    assert normalized_parameter_value(weight, math.nan) is None
    assert normalized_parameter_value(lookback, math.inf) is None


def test_resolved_values_fill_defaults_and_name_the_unresolvable_parameter() -> None:
    parameters = (
        IntegerParameter(
            parameter_id="lookback", default=20, minimum=10, maximum=30, kind="integer"
        ),
        FloatParameter(parameter_id="weight", default=0.5, minimum=0.0, maximum=2.0, kind="float"),
    )

    assert resolve_parameter_values(parameters, {"weight": 1}) == {"lookback": 20, "weight": 1.0}
    # 생략과 기본값 명시는 같은 해소 결과다 — 실행 지문도 같다.
    assert resolve_parameter_values(parameters, {}) == resolve_parameter_values(
        parameters, {"lookback": 20.0, "weight": 0.5}
    )

    with pytest.raises(InvalidParameterValueError) as unknown:
        resolve_parameter_values(parameters, {"weight": 1.0, "missing": 1, "absent": 2})
    assert unknown.value.parameter_id == "absent"
    assert "unknown=['absent', 'missing'] declared=['lookback', 'weight']" in str(unknown.value)

    with pytest.raises(InvalidParameterValueError) as outside:
        resolve_parameter_values(parameters, {"lookback": 31})
    assert outside.value.parameter_id == "lookback"
    assert "value=31 kind=integer minimum=10 maximum=30" in str(outside.value)


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
