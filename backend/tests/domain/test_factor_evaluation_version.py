"""평가 규칙 수치 digest와 판본을 고정한다. 의미 변경은 두 값을 함께 올린다."""

from dataclasses import asdict
from datetime import date, timedelta

from strategy_workbench.domain.canonical.facade.encoding import canonical_json_hash
from strategy_workbench.domain.factor.facade.evaluation import (
    FactorFieldValue,
    FactorObservation,
    evaluate_factor_graph,
)
from strategy_workbench.domain.factor.facade.expression import (
    BinaryNode,
    BinaryOperator,
    ComparisonNode,
    ConditionalNode,
    ConstantNode,
    CrossSectionalNode,
    CrossSectionalOperator,
    ExpressionNode,
    FactorComparisonOperator,
    FactorGraph,
    FieldNode,
    FillNode,
    GroupNode,
    GroupOperator,
    MissingPolicy,
    TimeSeriesNode,
    TimeSeriesOperator,
    UnaryNode,
    UnaryOperator,
)
from strategy_workbench.domain.factor.facade.planning import FACTOR_EVALUATION_VERSION

_PINNED = (
    "factor-evaluation-v1",
    "9287573ea2d563587ffb52745e02cea6416a2c488fac832db4c23b264b2ba965",
)
# `fill`(#608)은 새 노드라 기존 그래프의 수치를 바꾸지 않는다 — 위 digest 는 그대로 두고 따로
# 고정한다.
_PINNED_FILL = (
    "factor-evaluation-v1",
    "ccaaa44343f49232552284299ef79609f5366831c2a73f7e0f653bf45ebb6667",
)


def _graphs() -> list[FactorGraph]:
    leaf = FieldNode("x", "x", "field")
    other = FieldNode("y", "y", "field")
    outputs: list[ExpressionNode] = [
        *(
            UnaryNode("out", op, "x", "unary", periods=2 if op is UnaryOperator.LAG else None)
            for op in UnaryOperator
        ),
        *(BinaryNode("out", op, "x", "y", "binary") for op in BinaryOperator),
        *(TimeSeriesNode("out", op, "x", 3, "time_series", lag=1) for op in TimeSeriesOperator),
        *(
            CrossSectionalNode("out", op, "x", "cross_sectional", 0.2, 0.8)
            for op in CrossSectionalOperator
        ),
        *(GroupNode("out", op, "x", "sector", "group") for op in GroupOperator),
        *(ComparisonNode("out", op, "x", "y", "comparison") for op in FactorComparisonOperator),
    ]
    graphs = [FactorGraph((leaf, other, node), "out") for node in outputs]
    graphs.append(
        FactorGraph(
            (
                leaf,
                other,
                ComparisonNode(
                    "gate", FactorComparisonOperator.GREATER_THAN, "x", "y", "comparison"
                ),
                ConstantNode("zero", 0, "constant"),
                ConditionalNode("out", "gate", "x", "zero", "conditional"),
            ),
            "out",
        )
    )
    # 채움을 잎에서 해버리는 회귀는 시계열→횡단면 조합에서 드러난다.
    graphs.append(
        FactorGraph(
            (
                leaf,
                TimeSeriesNode("mean", TimeSeriesOperator.MEAN, "x", 3, "time_series"),
                CrossSectionalNode("out", CrossSectionalOperator.RANK, "mean", "cross_sectional"),
            ),
            "out",
        )
    )
    return graphs


def _fill_graphs() -> list[FactorGraph]:
    leaf = FieldNode("x", "x", "field")
    return [
        FactorGraph((leaf, FillNode("out", "x", -1.0, "fill")), "out"),
        FactorGraph(
            (
                leaf,
                UnaryNode("lag", UnaryOperator.LAG, "x", "unary", periods=2),
                FillNode("out", "lag", 0.0, "fill"),
            ),
            "out",
        ),
        FactorGraph(
            (
                leaf,
                FillNode("f", "x", 0.0, "fill"),
                CrossSectionalNode("out", CrossSectionalOperator.RANK, "f", "cross_sectional"),
            ),
            "out",
        ),
    ]


def _rules_digest(graphs: list[FactorGraph]) -> str:
    rows = tuple(
        FactorObservation(
            date(2024, 1, 1) + timedelta(days=day),
            str(security),
            (
                FactorFieldValue(
                    "x",
                    None if (day + security) % 7 == 0 else float((day * 3 + security) % 11),
                    masked=(day, security) == (4, 3),
                ),
                FactorFieldValue("y", float(security % 3)),
                FactorFieldValue("sector", "A" if security < 3 else "B"),
            ),
            universe_member=security != 5,
        )
        for day in range(9)
        for security in range(6)
    )
    return canonical_json_hash(
        [
            [
                asdict(value)
                for value in evaluate_factor_graph(graph, observations=rows, missing=policy).values
            ]
            for graph in graphs
            for policy in MissingPolicy
        ]
    )


def test_evaluation_rule_changes_bump_the_factor_evaluation_version() -> None:
    assert (FACTOR_EVALUATION_VERSION, _rules_digest(_graphs())) == _PINNED
    assert (FACTOR_EVALUATION_VERSION, _rules_digest(_fill_graphs())) == _PINNED_FILL
