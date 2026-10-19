"""열 평가기 골격(columnar-tape T3-01)의 경계 동작. 값의 비트 대조는 `tests/parity` L1 이 한다."""

from datetime import date

import pytest

from strategy_workbench.domain.factor import _panel_evaluation
from strategy_workbench.domain.factor.facade.evaluation import FactorFieldValue, FactorObservation
from strategy_workbench.domain.factor.facade.expression import (
    BinaryNode,
    BinaryOperator,
    ComparisonNode,
    ConstantNode,
    CrossSectionalNode,
    CrossSectionalOperator,
    ExpressionNode,
    FactorComparisonOperator,
    FactorGraph,
    FieldNode,
    GroupNode,
    GroupOperator,
    MissingPolicy,
    TimeSeriesNode,
    TimeSeriesOperator,
    UnaryNode,
    UnaryOperator,
)
from strategy_workbench.domain.factor.facade.panel_evaluation import (
    FactorNodeCache,
    FactorPanel,
    UnsupportedPanelEvaluationError,
    evaluate_panel_nodes,
)

_X = FieldNode("x", "x", "field")


def _rows(count: int) -> tuple[FactorObservation, ...]:
    return tuple(
        FactorObservation(
            as_of=date(2024, 1, 1 + index // 2),
            security_id=f"S{index % 2}",
            fields=(FactorFieldValue("x", float(index)), FactorFieldValue("g", "A")),
        )
        for index in range(count)
    )


def _evaluate(
    nodes: tuple[ExpressionNode, ...], missing: MissingPolicy = MissingPolicy.DROP
) -> FactorNodeCache:
    graph = FactorGraph((_X, *nodes), nodes[-1].node_id if nodes else "x")
    return evaluate_panel_nodes(
        graph, panel=FactorPanel.from_observations(_rows(6), graph), missing=missing
    )


@pytest.mark.parametrize(
    "node",
    [
        TimeSeriesNode("out", TimeSeriesOperator.MEAN, "x", 2, "time_series"),
        CrossSectionalNode("out", CrossSectionalOperator.RANK, "x", "cross_sectional"),
        GroupNode("out", GroupOperator.RANK, "x", "g", "group"),
    ],
)
def test_nodes_outside_the_skeleton_are_rejected(node: ExpressionNode) -> None:
    with pytest.raises(UnsupportedPanelEvaluationError, match="kind="):
        _evaluate((node,))


def test_cross_sectional_median_fill_waits_for_t3_05() -> None:
    with pytest.raises(UnsupportedPanelEvaluationError, match="cross_sectional_median"):
        _evaluate((), MissingPolicy.CROSS_SECTIONAL_MEDIAN)
    # 채움이 없는 정책은 같은 그래프를 평가한다
    assert _evaluate((), MissingPolicy.KEEP).value("x", 0) == 0.0


def test_integer_constants_are_rejected() -> None:
    """hydrate 는 상수를 float 로 바꾼다. 정수 상수는 객체 경로와 Python 타입이 갈라 받지 않는다."""
    with pytest.raises(ValueError, match="float constant"):
        _evaluate((ConstantNode("out", 0, "constant"),))


def test_cache_values_are_python_values_in_evaluation_order() -> None:
    cache = _evaluate(
        (
            UnaryNode("lag", UnaryOperator.LAG, "x", "unary", periods=1),
            ComparisonNode("out", FactorComparisonOperator.GREATER_THAN, "x", "lag", "comparison"),
        )
    )
    assert list(cache.columns) == ["x", "lag", "out"]
    # 행 0·1 은 종목마다 첫 행이라 lag 가 비고, 행 2 는 S0 의 둘째 행(값 2.0 > 0.0)이다
    assert [cache.value("lag", row) for row in range(3)] == [None, None, 0.0]
    assert [cache.value("out", row) for row in range(3)] == [None, None, True]
    assert type(cache.value("out", 2)) is bool


def test_checkpoint_runs_per_node_and_per_chunk_and_progress_ends_at_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_panel_evaluation, "_CHUNK_ROWS", 2)
    graph = FactorGraph((_X, BinaryNode("out", BinaryOperator.ADD, "x", "x", "binary")), "out")
    calls: list[str] = []
    fractions: list[float] = []
    evaluate_panel_nodes(
        graph,
        panel=FactorPanel.from_observations(_rows(6), graph),
        missing=MissingPolicy.ZERO,
        checkpoint=lambda: calls.append("checkpoint"),
        progress=fractions.append,
    )
    # 노드 진입 3번(out, x, 캐시된 x) + x 유한값 검사 3청크 + out 가림 구간 1필드 + 덧셈 3청크
    # + out 유한값 검사 3청크
    assert len(calls) == 3 + 3 + 1 + 3 + 3
    assert fractions == sorted(fractions) and fractions[-1] == 1.0
