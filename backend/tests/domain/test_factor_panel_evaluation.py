"""열 평가기(columnar-tape T3-01~T3-05)의 경계 동작. 값의 비트 대조는 `tests/parity` L1 이 한다."""

from dataclasses import replace
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
        CrossSectionalNode("out", CrossSectionalOperator.RANK, "x", "cross_sectional"),
        GroupNode("out", GroupOperator.RANK, "x", "g", "group"),
    ],
)
def test_section_nodes_rank_within_each_date(node: ExpressionNode) -> None:
    """날짜마다 두 종목(S0 < S1)이 한 묶음이다. 그룹은 모두 "A" 라 횡단면과 같다."""
    cache = _evaluate((node,))
    assert [cache.value("out", row) for row in range(6)] == [0.0, 1.0] * 3


def test_group_node_needs_its_group_column() -> None:
    graph = FactorGraph((_X, GroupNode("out", GroupOperator.RANK, "x", "g", "group")), "out")
    panel = FactorPanel.from_observations(_rows(6), FactorGraph((_X,), "x"))
    with pytest.raises(ValueError, match="no group column"):
        evaluate_panel_nodes(graph, panel=panel, missing=MissingPolicy.DROP)


@pytest.mark.parametrize(
    ("operator", "expected"),
    [
        (TimeSeriesOperator.MEAN, [None, None, 1.0, 2.0, 3.0, 4.0]),
        (TimeSeriesOperator.STANDARD_DEVIATION, [None, None, 1.0, 1.0, 1.0, 1.0]),
    ],
)
def test_time_series_mean_and_std_read_full_windows(
    operator: TimeSeriesOperator, expected: list[float | None]
) -> None:
    # 종목마다 x 가 2 씩 오른다: 창 2 의 평균은 가운데, 모집단 표준편차는 1 이다
    cache = _evaluate((TimeSeriesNode("out", operator, "x", 2, "time_series"),))
    assert [cache.value("out", row) for row in range(6)] == expected


def test_time_series_checkpoints_per_chunk_and_window_offset_with_monotone_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_panel_evaluation, "_CHUNK_ROWS", 2)
    graph = FactorGraph(
        (_X, TimeSeriesNode("out", TimeSeriesOperator.MINIMUM, "x", 2, "time_series")), "out"
    )
    calls: list[str] = []
    fractions: list[float] = []
    cache = evaluate_panel_nodes(
        graph,
        panel=FactorPanel.from_observations(_rows(6), graph),
        missing=MissingPolicy.DROP,
        checkpoint=lambda: calls.append("checkpoint"),
        progress=fractions.append,
    )
    # 종목마다 첫 행은 이력이 모자라 None 이고, 나머지는 직전 행과의 작은 값이다
    assert [cache.value("out", row) for row in range(6)] == [None, None, 0.0, 1.0, 2.0, 3.0]
    # 노드 진입 2번 + x 유한값 검사 3청크 + out 가림 구간 1필드
    # + 시계열 커널(종목 우선 재배열 3청크·누적합 뒤 1번·창 후보 5행의 3청크와 청크마다 offset
    # 1번·흩뿌리기 3청크) + out 유한값 검사 3청크
    assert len(calls) == 2 + 3 + 1 + (3 + 1 + 3 * 2 + 3) + 3
    # 노드 안에서도 청크마다 올리고 끝값은 1.0 이다
    assert len(fractions) == 2 + 3
    assert fractions == sorted(fractions) and fractions[-1] == 1.0


def test_cross_sectional_median_fills_the_output_from_its_peers() -> None:
    """그래프 출력의 빈칸은 같은 (as_of, universe_member) 동료 값의 중앙값으로 채운다."""
    rows = list(_rows(8))
    # 날짜 1 의 S0 값을 비우고, 날짜 3 은 두 종목을 다 비운다(동료에 값이 없으면 채우지 않는다)
    for index in (2, 6, 7):
        rows[index] = replace(rows[index], fields=(FactorFieldValue("x", None),))
    graph = FactorGraph((_X,), "x")
    cache = evaluate_panel_nodes(
        graph,
        panel=FactorPanel.from_observations(tuple(rows), graph),
        missing=MissingPolicy.CROSS_SECTIONAL_MEDIAN,
    )
    assert [cache.value("x", row) for row in range(8)] == [0.0, 1.0, 3.0, 3.0, 4.0, 5.0, None, None]
    # 채움이 없는 정책은 빈칸을 그대로 둔다
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
    # + 출력 채움 8번(동료 묶음 배치 3·값 있는 동료 3·채움 2) + out 유한값 검사 3청크
    assert len(calls) == 3 + 3 + 1 + 3 + 8 + 3
    assert fractions == sorted(fractions) and fractions[-1] == 1.0
