"""P2-02: 결측 정책이 팩터 그래프가 아니라 실행 설정에서 plan·평가로 들어온다.

세 가지를 고정한다. (1) `missing` 은 `compile_factor_plan` 인자이고 `plan_hash` 에 남는다.
(2) 기본값 경로(`MissingPolicy.DROP`)의 `plan_hash` 는 P2-02 이전과 같다. (3) 평가의 결측
채우기는 그래프의 `missing_policy` 가 아니라 넘겨준 `missing` 을 따른다.
"""

from __future__ import annotations

from dataclasses import fields
from datetime import date

import pytest

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

# 같은 그래프·같은 기본 결측 정책의 plan hash 골든. P2-02 는 정책을 인자로 옮기면서도 이 값을
# 유지했지만(`6aa3a445…`), P2-03 이 `FactorGraph.missing_policy` 필드 자체를 지우면서 `graph_hash`
# 가 바뀌어 값이 한 번 갈렸다. 팩터 행렬 캐시는 코드베이스에 아직 없어 잘못된 히트는 불가능하고,
# 영향은 이 골든 하나다(1.1 문서는 P2-09 업그레이더를 거쳐 들어온다).
PLAN_HASH_1_2 = "e8bdd889366602ffa16c40e3c1d4de04c7f040f637252ec7217cf50679d13f4e"


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


def test_default_missing_policy_plan_hash_is_pinned() -> None:
    plan = compile_factor_plan(
        _graph(), registry_version="test-registry", missing=MissingPolicy.DROP
    )

    assert plan.plan_hash == PLAN_HASH_1_2


def test_the_graph_no_longer_carries_a_missing_policy() -> None:
    """P2-03: 결측 정책의 owner 는 실행 설정 하나다 — 그래프에 같은 사실을 두 번 두지 않는다.

    필드가 남아 있으면 `graph_hash` 가 정책에 따라 갈려서, 실행 설정만 바꾼 두 실행이 서로 다른
    팩터 식으로 취급된다.
    """
    assert not hasattr(_graph(), "missing_policy")
    assert "missing_policy" not in {field.name for field in fields(FactorGraph)}


def test_evaluation_fills_by_the_argument() -> None:
    observations = _observations()

    dropped = evaluate_factor_graph(
        _field_graph(), observations=observations, missing=MissingPolicy.DROP
    )
    zeroed = evaluate_factor_graph(
        _field_graph(), observations=observations, missing=MissingPolicy.ZERO
    )

    assert [value.value for value in dropped.values] == [100.0, None]
    assert [value.value for value in zeroed.values] == [100.0, 0.0]


def _masked_cross_section() -> tuple[FactorObservation, ...]:
    """한 날짜의 세 종목 — 값 · 모르는 결측 · 원장이 가린 결측(#298)."""
    day = date(2024, 1, 8)
    return (
        FactorObservation(day, "A", (FactorFieldValue("price.close", 100.0),)),
        FactorObservation(day, "B", (FactorFieldValue("price.close", None),)),
        FactorObservation(day, "C", (FactorFieldValue("price.close", None, masked=True),)),
    )


def test_no_missing_policy_fills_a_cell_the_ledger_masked() -> None:
    """결측 정책은 모르는 값만 채운다. 원장이 틀린 값이라 가린 셀은 어느 정책에서도 결측이다(#298).

    채우면 가리기 전보다 더 틀린다 — 잔고 0·가격 0 이 모멘텀·변화를 −100% 쪽으로 끌어간다.
    """
    expected = {
        MissingPolicy.DROP: [100.0, None, None],
        MissingPolicy.KEEP: [100.0, None, None],
        MissingPolicy.ZERO: [100.0, 0.0, None],
        MissingPolicy.CROSS_SECTIONAL_MEDIAN: [100.0, 100.0, None],
    }
    for policy, values in expected.items():
        evaluation = evaluate_factor_graph(
            _field_graph(), observations=_masked_cross_section(), missing=policy
        )
        assert [value.value for value in evaluation.values] == values, policy


def test_a_masked_cell_leaves_every_window_over_it_missing_under_zero() -> None:
    """가린 셀은 창 결측 전파로만 처리된다 — 창 가운데에 있어도 창 값이 결측이다.

    같은 자리의 모르는 결측은 `zero` 가 0 으로 채워 창이 선다(값은 끝점 비라 그대로다).
    """
    days = [date(2024, 1, day) for day in (8, 9, 10)]

    def momentum(middle: FactorFieldValue) -> float | None:
        closes = (FactorFieldValue("price.close", 100.0), middle,
                  FactorFieldValue("price.close", 110.0))
        observations = tuple(
            FactorObservation(day, "A", (close,)) for day, close in zip(days, closes, strict=True)
        )
        evaluation = evaluate_factor_graph(
            _graph(), observations=observations, missing=MissingPolicy.ZERO
        )
        return evaluation.values[-1].value

    assert momentum(FactorFieldValue("price.close", None)) == pytest.approx(0.1)
    assert momentum(FactorFieldValue("price.close", None, masked=True)) is None


def test_trace_evaluation_reads_the_same_argument() -> None:
    observations = _observations()

    evaluation, trace = evaluate_factor_graph_with_trace(
        _field_graph(), observations=observations, missing=MissingPolicy.ZERO
    )

    assert [value.value for value in evaluation.values] == [100.0, 0.0]
    assert trace.output_node_id == "close"
