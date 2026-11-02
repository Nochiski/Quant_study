"""이슈 #196: 팩터 평가가 끝나면 입력 관측을 참조 카운트만으로 놓아 준다.

평가기의 재귀 클로저가 자기 cell 을 붙잡는 순환을 남기면, 그 cell 이 쥔 관측 튜플 전체가
파이프라인이 끝난 뒤에도 풀리지 않고 다음 전체 수집(2세대)을 기다린다. 4년 실데이터에서는 약
830만 객체가 그렇게 남아 run 종료 뒤 수 GB 가 묶였다. 순환 수집기를 끈 채 참조 수가 원래대로
돌아오는지로 순환이 없음을 확인한다.
"""

from __future__ import annotations

import gc
import sys
from collections.abc import Iterator
from datetime import date

import pytest

from strategy_workbench.domain.factor._evaluation import _compute_nodes
from strategy_workbench.domain.factor.facade.evaluation import (
    FactorFieldValue,
    FactorObservation,
    evaluate_factor_graph,
)
from strategy_workbench.domain.factor.facade.expression import (
    BinaryNode,
    BinaryOperator,
    FactorGraph,
    FieldNode,
    MissingPolicy,
    TimeSeriesNode,
    TimeSeriesOperator,
)
from strategy_workbench.domain.factor.facade.trace import evaluate_factor_graph_with_trace


@pytest.fixture(autouse=True)
def _cycle_collector_off() -> Iterator[None]:
    # 평가 도중 0세대 수집이 돌면 순환이 우연히 치워져 결함이 가려진다. 결정적으로 보려고 끈다.
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if was_enabled:
            gc.enable()


def _graph(output_node_id: str = "mom") -> FactorGraph:
    return FactorGraph(
        nodes=(
            FieldNode("close", "price.close", "field"),
            TimeSeriesNode("mom", TimeSeriesOperator.MOMENTUM, "close", 1, "time_series"),
        ),
        output_node_id=output_node_id,
    )


def _observations() -> tuple[FactorObservation, ...]:
    return tuple(
        FactorObservation(
            as_of=date(2024, 1, day),
            security_id="005930",
            fields=(FactorFieldValue("price.close", 100.0 + day),),
        )
        for day in (8, 9, 10)
    )


def test_evaluation_releases_its_observations_without_the_cycle_collector() -> None:
    observations = _observations()
    before = sys.getrefcount(observations)

    evaluation = evaluate_factor_graph(
        _graph(), observations=observations, missing=MissingPolicy.DROP
    )

    assert len(evaluation.values) == len(observations)
    assert sys.getrefcount(observations) == before


def test_traced_evaluation_releases_its_observations_without_the_cycle_collector() -> None:
    observations = _observations()
    before = sys.getrefcount(observations)

    evaluation, trace = evaluate_factor_graph_with_trace(
        _graph(),
        observations=observations,
        missing=MissingPolicy.DROP,
        selection=None,
    )

    assert len(evaluation.values) == len(observations)
    assert trace is not None
    del evaluation, trace
    assert sys.getrefcount(observations) == before


def test_failed_evaluation_releases_its_observations_without_the_cycle_collector() -> None:
    observations = _observations()
    before = sys.getrefcount(observations)
    graph = _graph(output_node_id="missing_node")

    try:
        evaluate_factor_graph(graph, observations=observations, missing=MissingPolicy.DROP)
    except ValueError as error:
        assert "missing_node" in str(error)
    else:  # pragma: no cover - 실패해야 하는 입력
        pytest.fail("unknown output node must be rejected")

    assert sys.getrefcount(observations) == before


def _diamond_graph() -> FactorGraph:
    """close 를 세 간선(제곱의 양쪽·차이)이 읽는 다이아몬드. 출력은 차이의 모멘텀."""
    return FactorGraph(
        nodes=(
            FieldNode("close", "price.close", "field"),
            BinaryNode("square", BinaryOperator.MULTIPLY, "close", "close", "binary"),
            BinaryNode("spread", BinaryOperator.SUBTRACT, "square", "close", "binary"),
            TimeSeriesNode("mom", TimeSeriesOperator.MOMENTUM, "spread", 1, "time_series"),
        ),
        output_node_id="mom",
    )


def test_evaluation_drops_intermediate_values_once_every_consumer_has_read_them() -> None:
    # 노드 492개 그래프를 5년 실데이터에서 모두 들고 있으면 60GB 를 넘었다. 중간 노드는 마지막
    # 소비 노드가 끝나면 놓고, 값은 모든 노드를 보존한 평가와 같다.
    observations = _observations()
    released = _compute_nodes(
        _diamond_graph(), observations=observations, missing=MissingPolicy.DROP
    )
    kept = _compute_nodes(
        _diamond_graph(),
        observations=observations,
        missing=MissingPolicy.DROP,
        keep_intermediate=True,
    )

    assert list(released.computed) == ["mom"]
    assert list(kept.computed) == ["close", "square", "spread", "mom"]
    assert released.computed["mom"] == kept.computed["mom"]


def test_node_cache_view_requires_the_kept_intermediate_values() -> None:
    released = _compute_nodes(
        _diamond_graph(), observations=_observations(), missing=MissingPolicy.DROP
    )
    with pytest.raises(ValueError, match="keep_intermediate=True"):
        _ = released.cache
