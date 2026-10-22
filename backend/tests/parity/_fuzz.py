"""L1 평가기 퍼저(columnar-tape T3-05). seed 하나로 그래프와 패널을 함께 만든다.

그래프는 필드·상수에서 시작해 원소·채움·시계열(mean·std 포함)·횡단면·그룹 노드를 무작위로
쌓는다. 패널은 종목·날짜·그룹 수, 행 공백, 비구성원, 가린 셀, 값 None·생략, 동점·±0·극단 크기 값,
그룹 결측·중복 셀, 정본이 아닌 행 순서를 섞는다. 같은 seed 는 늘 같은 사례다
(`np.random.default_rng`).

CI 는 `test_parity.py` 가 seed 수백 개를 돌리고, PR 증거용 대량 실행은 이 모듈을 그대로 쓴다.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from strategy_workbench.domain.factor.facade.evaluation import FactorFieldValue, FactorObservation
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

from ._corpus import EvaluatorCase

_GROUP_FIELD = "sector"
# 값 분포. 동점·±0·극단 크기는 정렬·채움·정확 커널 경계를 밟는다
_DISTRIBUTIONS = ("ties", "signed_zero", "normal", "integers", "extreme", "mixed")
_QUANTILES = ((0.01, 0.99), (0.2, 0.8), (0.0, 1.0), (0.25, 0.75), (0.0, 0.5), (0.5, 1.0))


def _value(rng: np.random.Generator, distribution: str) -> float:
    if distribution == "ties":
        return float(rng.integers(-3, 4)) / 2.0
    if distribution == "signed_zero":
        return float(rng.choice((0.0, -0.0, 1.0, -1.0, 0.5)))
    if distribution == "normal":
        return float(rng.normal(0.0, 1.0))
    if distribution == "integers":
        return float(np.round(rng.normal(0.0, 1e6)))
    if distribution == "extreme":
        # ±1.7e308 은 두 값의 합·중앙값 (a + b) / 2 가 넘치는 크기다(#598 리뷰 P3-2)
        sizes = (1.7e308, -1.7e308, 1e300, -3e299, 5e-324, -1e-310, 7e-291, 0.0, -0.0, 1e144)
        sizes += (2e-126,)
        return float(rng.choice(sizes))
    pick = int(rng.integers(0, 4))
    return (
        float(rng.normal(0.0, 1.0)),
        float(rng.choice((0.0, -0.0, 0.25))),
        float(rng.lognormal(0.0, 5.0) * rng.choice((-1.0, 1.0))),
        float(rng.integers(-2, 3)),
    )[pick]


def _cell(rng: np.random.Generator, field_id: str, distribution: str) -> FactorFieldValue | None:
    draw = rng.random()
    if draw < 0.05:
        return None  # 생략
    if draw < 0.11:
        return FactorFieldValue(field_id, None)  # 값 None
    if draw < 0.15:
        return FactorFieldValue(field_id, None, masked=True)
    if draw < 0.16:
        return FactorFieldValue(field_id, bool(rng.integers(0, 2)))  # 숫자 자리의 bool
    return FactorFieldValue(field_id, _value(rng, distribution))


def _group_cells(rng: np.random.Generator, groups: int) -> tuple[FactorFieldValue, ...]:
    draw = rng.random()
    if draw < 0.06:
        return ()
    if draw < 0.10:
        return (FactorFieldValue(_GROUP_FIELD, None),)
    if draw < 0.12:
        return (FactorFieldValue(_GROUP_FIELD, 3.0),)  # 문자열이 아닌 그룹 값
    label = f"G{int(rng.integers(0, groups))}"
    if draw < 0.14:
        # 같은 필드 셀이 둘이면 그룹은 첫 셀을 읽는다(`_peer_indices`)
        return (FactorFieldValue(_GROUP_FIELD, label), FactorFieldValue(_GROUP_FIELD, "Z"))
    return (FactorFieldValue(_GROUP_FIELD, label),)


def fuzz_panel(rng: np.random.Generator) -> tuple[FactorObservation, ...]:
    securities = int(rng.integers(1, 26))
    dates = int(rng.integers(2, 30))
    groups = int(rng.integers(1, 6))
    distributions = {field_id: str(rng.choice(_DISTRIBUTIONS)) for field_id in ("x", "y")}
    gap = float(rng.choice((0.0, 0.1, 0.3)))
    rows: list[FactorObservation] = []
    start = date(2024, 1, 1)
    for day in range(dates):
        for security in range(securities):
            if rng.random() < gap:
                continue  # 행 공백
            fields = tuple(
                cell
                for field_id in ("x", "y")
                if (cell := _cell(rng, field_id, distributions[field_id])) is not None
            ) + _group_cells(rng, groups)
            rows.append(
                FactorObservation(
                    as_of=start + timedelta(days=day),
                    security_id=f"S{security:02d}",
                    fields=fields,
                    universe_member=bool(rng.random() >= 0.2),
                )
            )
    if rng.random() < 0.15:
        # 정본 순서((as_of, security_id))가 아닌 입력도 같은 값이어야 한다
        order = rng.permutation(len(rows))
        rows = [rows[index] for index in order]
    return tuple(rows)


class _GraphBuilder:
    def __init__(self, rng: np.random.Generator) -> None:
        self.rng = rng
        self.nodes: list[ExpressionNode] = []
        self.numeric: list[str] = []
        self.boolean: list[str] = []

    def add(self, node: ExpressionNode, *, boolean: bool = False) -> str:
        self.nodes.append(node)
        (self.boolean if boolean else self.numeric).append(node.node_id)
        return node.node_id

    def pick(self) -> str:
        # 최근 노드를 더 자주 골라 깊은 사슬을 만든다
        weights = np.arange(1, len(self.numeric) + 1, dtype=np.float64) ** 2
        return str(self.rng.choice(self.numeric, p=weights / weights.sum()))

    def step(self, index: int) -> None:
        rng, node_id = self.rng, f"n{index}"
        kind = str(
            rng.choice(
                (
                    "ts",
                    "ts",
                    "cs",
                    "cs",
                    "group",
                    "group",
                    "unary",
                    "lag",
                    "binary",
                    "condition",
                    "fill",
                ),
            )
        )
        if kind == "ts":
            operator = TimeSeriesOperator(
                str(rng.choice([item.value for item in TimeSeriesOperator]))
            )
            window, lag = int(rng.integers(1, 6)), int(rng.integers(0, 3))
            self.add(TimeSeriesNode(node_id, operator, self.pick(), window, "time_series", lag=lag))
        elif kind == "cs":
            operator = CrossSectionalOperator(
                str(rng.choice([item.value for item in CrossSectionalOperator]))
            )
            lower, upper = _QUANTILES[int(rng.integers(0, len(_QUANTILES)))]
            self.add(
                CrossSectionalNode(node_id, operator, self.pick(), "cross_sectional", lower, upper)
            )
        elif kind == "group":
            operator = GroupOperator(str(rng.choice([item.value for item in GroupOperator])))
            self.add(GroupNode(node_id, operator, self.pick(), _GROUP_FIELD, "group"))
        elif kind == "fill":
            # −0.0 은 부호 있는 0, −1.7e308 은 뒤 노드의 합·중앙값이 넘치는 크기다
            value = float(rng.choice((0.0, -0.0, 2.5, -1.7e308)))
            self.add(FillNode(node_id, self.pick(), value, "fill"))
        elif kind == "unary":
            self.add(UnaryNode(node_id, UnaryOperator.NEGATE, self.pick(), "unary"))
        elif kind == "lag":
            periods = int(rng.integers(1, 4))
            self.add(UnaryNode(node_id, UnaryOperator.LAG, self.pick(), "unary", periods=periods))
        elif kind == "binary":
            operator = BinaryOperator(str(rng.choice([item.value for item in BinaryOperator])))
            self.add(BinaryNode(node_id, operator, self.pick(), self.pick(), "binary"))
        else:
            gate = self.add(
                ComparisonNode(
                    f"{node_id}g",
                    FactorComparisonOperator(
                        str(rng.choice([item.value for item in FactorComparisonOperator]))
                    ),
                    self.pick(),
                    self.pick(),
                    "comparison",
                ),
                boolean=True,
            )
            self.add(ConditionalNode(node_id, gate, self.pick(), self.pick(), "conditional"))


def fuzz_graph(rng: np.random.Generator) -> FactorGraph:
    builder = _GraphBuilder(rng)
    builder.add(FieldNode("x", "x", "field"))
    if rng.random() < 0.6:
        builder.add(FieldNode("y", "y", "field"))
    if rng.random() < 0.2:
        builder.add(ConstantNode("c", float(rng.choice((0.0, -0.0, 2.5, 1e-300))), "constant"))
    for index in range(int(rng.integers(1, 7))):
        builder.step(index)
    return FactorGraph(tuple(builder.nodes), builder.nodes[-1].node_id)


def fuzz_case(seed: int) -> EvaluatorCase:
    rng = np.random.default_rng([seed, 305])
    graph = fuzz_graph(rng)
    policy = MissingPolicy(str(rng.choice([item.value for item in MissingPolicy])))
    return EvaluatorCase(f"L1/fuzz/seed={seed}", graph, fuzz_panel(rng), policy)
