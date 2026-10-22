"""`fill` 노드(#608)는 입력의 빈 칸을 상수로 채운다 — 원장이 가린 칸은 채우지 않는다.

실행 결측 정책(`RunEnvironment.missing`)은 횡단면 진입과 출력에서만 채운다(#312). "비교값이 없으면
변화 0"·"있는 것끼리 가중 평균"처럼 그래프 한 자리의 결측 규칙은 이 노드로 문서에 적는다.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from strategy_workbench.domain.factor.facade.evaluation import (
    FactorFieldValue,
    FactorObservation,
    evaluate_factor_graph,
)
from strategy_workbench.domain.factor.facade.expression import (
    ComparisonNode,
    FactorComparisonOperator,
    FactorGraph,
    FieldMetadata,
    FieldNode,
    FillNode,
    MissingPolicy,
    NodeValueType,
    UnaryNode,
    UnaryOperator,
)
from strategy_workbench.domain.factor.facade.validation import validate_factor_graph

_FIELD = "x"
_DAYS = tuple(date(2024, 1, 1) + timedelta(days=n) for n in range(4))


def _row(day: int, *cells: FactorFieldValue) -> FactorObservation:
    return FactorObservation(_DAYS[day], "S1", cells)


def _filled(
    *nodes: UnaryNode,
    source: str = _FIELD,
    fill_value: float = 0.0,
    rows: tuple[FactorObservation, ...],
    missing: MissingPolicy = MissingPolicy.DROP,
) -> list[float | None]:
    graph = FactorGraph(
        (FieldNode(_FIELD, _FIELD, "field"), *nodes, FillNode("out", source, fill_value, "fill")),
        "out",
    )
    values = evaluate_factor_graph(graph, observations=rows, missing=missing).values
    return [value.value for value in values]


def test_fill_writes_the_constant_into_missing_and_omitted_cells() -> None:
    """값 None 칸과 필드가 빠진 칸을 채우고 값이 있는 칸은 그대로 둔다. 결측 정책이 DROP 이어도
    채운다."""
    rows = (
        _row(0, FactorFieldValue(_FIELD, 2.0)),
        _row(1, FactorFieldValue(_FIELD, None)),
        _row(2),
        _row(3, FactorFieldValue(_FIELD, -0.5)),
    )

    assert _filled(fill_value=-1.0, rows=rows) == [2.0, -1.0, -1.0, -0.5]


@pytest.mark.parametrize("missing", list(MissingPolicy))
def test_fill_leaves_a_masked_cell_missing(missing: MissingPolicy) -> None:
    """원장이 가린 칸은 사건 경계라 어느 결측 정책도 채우지 않는다(#298) — `fill` 도 같다."""
    rows = (
        _row(0, FactorFieldValue(_FIELD, 2.0)),
        _row(1, FactorFieldValue(_FIELD, None, masked=True)),
        _row(2, FactorFieldValue(_FIELD, None)),
    )

    assert _filled(rows=rows, missing=missing) == [2.0, None, 0.0]


def test_fill_treats_a_non_number_field_cell_as_missing() -> None:
    """숫자 자리에 실려 온 bool 은 필드를 읽을 때 이미 빈 칸이다(`_field_values`) — 결측 정책처럼
    채운다."""
    rows = (_row(0, FactorFieldValue(_FIELD, True)), _row(1, FactorFieldValue(_FIELD, None)))

    assert _filled(rows=rows) == [0.0, 0.0]


def test_fill_also_fills_warm_up_cells() -> None:
    """이력이 모자라 빈 칸(워밍업)도 빈 칸이라 채운다 — "지표가 없으면 0점" 규칙이 이 칸을 쓴다."""
    rows = tuple(_row(day, FactorFieldValue(_FIELD, float(day))) for day in range(3))
    lag = UnaryNode("lag", UnaryOperator.LAG, _FIELD, "unary", periods=1)

    assert _filled(lag, source="lag", fill_value=9.0, rows=rows) == [9.0, 0.0, 1.0]


def test_fill_keeps_the_input_contract() -> None:
    """출력은 입력의 값 타입·단위를 그대로 잇는다 — 채울 값은 입력 단위의 숫자다."""
    graph = FactorGraph(
        (FieldNode(_FIELD, _FIELD, "field"), FillNode("out", _FIELD, 0.0, "fill")), "out"
    )

    contracts = {
        contract.node_id: contract
        for contract in validate_factor_graph(
            graph, fields=(FieldMetadata(_FIELD, "KRW"),)
        ).node_contracts
    }

    assert (contracts["out"].value_type, contracts["out"].unit) == (
        NodeValueType.NUMERIC_SERIES,
        "KRW",
    )


def test_fill_rejects_a_boolean_input() -> None:
    """참·거짓 입력을 숫자로 채우면 타입이 섞인다 — 다른 변환처럼 `input_type` 으로 막는다."""
    graph = FactorGraph(
        (
            FieldNode(_FIELD, _FIELD, "field"),
            ComparisonNode(
                "gate", FactorComparisonOperator.GREATER_THAN, _FIELD, _FIELD, "comparison"
            ),
            FillNode("out", "gate", 0.0, "fill"),
        ),
        "out",
    )

    issues = validate_factor_graph(graph).issues

    assert [(issue.code, issue.node_id) for issue in issues] == [("factor.graph.input_type", "out")]
