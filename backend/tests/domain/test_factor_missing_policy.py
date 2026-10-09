"""P2-02: 결측 정책이 팩터 그래프가 아니라 실행 설정에서 plan·평가로 들어온다.

세 가지를 고정한다. (1) `missing` 은 `compile_factor_plan` 인자이고 `plan_hash` 에 남는다.
(2) 기본값 경로(`MissingPolicy.DROP`)의 `plan_hash` 는 P2-02 이전과 같다. (3) 평가의 결측
채우기는 그래프의 `missing_policy` 가 아니라 넘겨준 `missing` 을 따른다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from strategy_workbench.domain.factor.facade.evaluation import (
    FactorFieldValue,
    FactorObservation,
    evaluate_factor_graph,
)
from strategy_workbench.domain.factor.facade.expression import (
    FactorGraph,
    FieldNode,
    MissingPolicy,
    TimeSeriesNode,
    TimeSeriesOperator,
)
from strategy_workbench.domain.factor.facade.planning import compile_factor_plan
from strategy_workbench.domain.factor.facade.trace import evaluate_factor_graph_with_trace

# P2-02 이전(`graph.missing_policy` 를 읽던 시점)에 같은 그래프가 내던 값. 결측 정책을 인자로
# 옮겨도 기본값 경로의 팩터 행렬 캐시 키가 갈리지 않는다는 회귀 고정이다.
PLAN_HASH_BEFORE_P2_02 = "6aa3a44540f01b9a108fc7961a8fd47bf9b64e6ab0b8e225ff59c491bb597c2a"


def _graph() -> FactorGraph:
    return FactorGraph(
        nodes=(
            FieldNode("close", "price.close", "field"),
            TimeSeriesNode("mom", TimeSeriesOperator.MOMENTUM, "close", 3, "time_series"),
        ),
        output_node_id="mom",
    )


def _field_graph() -> FactorGraph:
    return FactorGraph(nodes=(FieldNode("close", "price.close", "field"),), output_node_id="close")


def _observations() -> tuple[FactorObservation, ...]:
    return (
        FactorObservation(
            as_of=date(2024, 1, 8),
            security_id="005930",
            fields=(FactorFieldValue("price.close", 100.0),),
        ),
        FactorObservation(
            as_of=date(2024, 1, 8),
            security_id="000660",
            fields=(FactorFieldValue("price.close", None),),
        ),
    )


def test_plan_hash_splits_on_the_environment_missing_policy() -> None:
    graph = _graph()

    dropped = compile_factor_plan(graph, registry_version="r", missing=MissingPolicy.DROP)
    zeroed = compile_factor_plan(graph, registry_version="r", missing=MissingPolicy.ZERO)

    assert dropped.plan_hash != zeroed.plan_hash
    assert (dropped.missing_policy, zeroed.missing_policy) == ("drop", "zero")
    # 그래프 자체는 같다: 갈리는 것은 plan 이지 팩터 식이 아니다.
    assert dropped.graph_hash == zeroed.graph_hash


def test_default_missing_policy_keeps_the_plan_hash_it_had_before_the_move() -> None:
    plan = compile_factor_plan(
        _graph(), registry_version="test-registry", missing=MissingPolicy.DROP
    )

    assert plan.plan_hash == PLAN_HASH_BEFORE_P2_02


def test_plan_reads_the_argument_not_the_deprecated_graph_field() -> None:
    """1.1 호환으로 남은 `graph.missing_policy` 는 plan 의 결측 정책을 정하지 않는다.

    그 필드는 1.1 문서의 일부라 `graph_hash` 에는 아직 들어간다(1.2 에서 사라진다). plan 이
    읽는 값은 실행 설정에서 온 `missing` 하나뿐이다.
    """
    legacy = replace(_graph(), missing_policy=MissingPolicy.ZERO)

    planned = compile_factor_plan(legacy, registry_version="r", missing=MissingPolicy.DROP)

    assert planned.missing_policy == "drop"


def test_evaluation_fills_by_the_argument_not_by_the_graph_field() -> None:
    legacy = replace(_field_graph(), missing_policy=MissingPolicy.ZERO)
    observations = _observations()

    dropped = evaluate_factor_graph(legacy, observations=observations, missing=MissingPolicy.DROP)
    zeroed = evaluate_factor_graph(
        _field_graph(), observations=observations, missing=MissingPolicy.ZERO
    )

    assert [value.value for value in dropped.values] == [100.0, None]
    assert [value.value for value in zeroed.values] == [100.0, 0.0]


def test_trace_evaluation_reads_the_same_argument() -> None:
    observations = _observations()

    evaluation, trace = evaluate_factor_graph_with_trace(
        _field_graph(), observations=observations, missing=MissingPolicy.ZERO
    )

    assert [value.value for value in evaluation.values] == [100.0, 0.0]
    assert trace.output_node_id == "close"
