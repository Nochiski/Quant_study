from __future__ import annotations

from dataclasses import dataclass, field
from dataclasses import fields as dataclass_fields
from enum import StrEnum
from typing import Literal, TypeAlias, get_args, get_type_hints

# Editor metadata the runtime schema (domain.strategy._schema) turns into `x-catalog` /
# `x-reference`: which catalog or document-internal namespace an identifier resolves in. The
# engine never reads it; it exists so no client keeps a hand-written list of which ids are which.
CATALOG_EQUITY_FIELD = {"catalog": "equity-field"}
CATALOG_FACTOR = {"catalog": "factor"}
CATALOG_SUBGRAPH = {"catalog": "subgraph"}
REFERENCE_NODE = {"reference": "node"}
REFERENCE_PARAMETER = {"reference": "parameter"}
# The array that declares a namespace; its items carry the `<namespace>_id` definition.
DEFINES_NODE = {"defines": "node"}


def minimum(value: int) -> dict[str, int]:
    """정수 파라미터의 하한을 필드 옆에 선언한다 (P1-04).

    이 값 하나를 `_validation.py`가 검사에 쓰고 runtime schema(`domain.strategy._schema`)가
    JSON Schema `minimum`으로 발행한다. 하한을 검증기와 스키마에 따로 적으면 한쪽만 고쳐질 때
    화면이 만들어 준 기본값을 backend가 거부한다 — 팔레트가 `window: 0`인 노드를 만들어 곧바로
    검증 오류가 나던 결함이 그 모양이었다.
    """
    return {"minimum": value}


def field_minimum(node_type: type, property_name: str) -> int:
    """`node_type.property_name`에 선언된 하한. 선언이 없으면 `KeyError`."""
    for item in dataclass_fields(node_type):
        if item.name == property_name:
            declared = item.metadata.get("minimum")
            if isinstance(declared, int):
                return declared
            break
    raise KeyError(f"{node_type.__name__}.{property_name}에 minimum 선언이 없습니다")


# 요소별 변환만 남긴다(schema 1.1 S4). 횡단면 순위·표준화·윈저화·demean은 CrossSectionalOperator다.
class UnaryOperator(StrEnum):
    NEGATE = "negate"
    LAG = "lag"


class BinaryOperator(StrEnum):
    ADD = "add"
    SUBTRACT = "subtract"
    MULTIPLY = "multiply"
    DIVIDE = "divide"


class TimeSeriesOperator(StrEnum):
    MEAN = "mean"
    STANDARD_DEVIATION = "std"
    MOMENTUM = "momentum"
    DELTA = "delta"
    MINIMUM = "min"
    MAXIMUM = "max"


class CrossSectionalOperator(StrEnum):
    RANK = "rank"
    ZSCORE = "zscore"
    WINSORIZE = "winsorize"
    DEMEAN = "demean"  # 같은 날 유니버스 평균을 뺀다 (1.0의 `unary: neutralize`)


class GroupOperator(StrEnum):
    NEUTRALIZE = "neutralize"
    RANK = "rank"


class FactorComparisonOperator(StrEnum):
    GREATER_THAN = "gt"
    GREATER_THAN_OR_EQUAL = "gte"
    LESS_THAN = "lt"
    LESS_THAN_OR_EQUAL = "lte"
    EQUAL = "eq"


class MissingPolicy(StrEnum):
    DROP = "drop"
    KEEP = "keep"
    ZERO = "zero"
    CROSS_SECTIONAL_MEDIAN = "cross_sectional_median"


class NodeValueType(StrEnum):
    NUMERIC_SERIES = "numeric_series"
    BOOLEAN_SERIES = "boolean_series"
    GROUP_SERIES = "group_series"
    SCALAR = "scalar"


@dataclass(frozen=True)
class FieldMetadata:
    field_id: str
    unit: str
    value_type: NodeValueType = NodeValueType.NUMERIC_SERIES
    available_history_sessions: int | None = None


@dataclass(frozen=True)
class FieldNode:
    node_id: str
    field_id: str = field(metadata=CATALOG_EQUITY_FIELD)
    kind: Literal["field"]


@dataclass(frozen=True)
class ConstantNode:
    node_id: str
    value: float
    kind: Literal["constant"]


@dataclass(frozen=True)
class ParameterNode:
    node_id: str
    parameter_id: str = field(metadata=REFERENCE_PARAMETER)
    kind: Literal["parameter"]


@dataclass(frozen=True)
class UnaryNode:
    node_id: str
    operator: UnaryOperator
    input_node_id: str = field(metadata=REFERENCE_NODE)
    kind: Literal["unary"]
    periods: int | None = field(default=None, metadata=minimum(1))


@dataclass(frozen=True)
class BinaryNode:
    node_id: str
    operator: BinaryOperator
    left_node_id: str = field(metadata=REFERENCE_NODE)
    right_node_id: str = field(metadata=REFERENCE_NODE)
    kind: Literal["binary"]


@dataclass(frozen=True)
class TimeSeriesNode:
    node_id: str
    operator: TimeSeriesOperator
    input_node_id: str = field(metadata=REFERENCE_NODE)
    window: int = field(metadata=minimum(1))
    kind: Literal["time_series"]
    lag: int = field(default=0, metadata=minimum(0))


@dataclass(frozen=True)
class CrossSectionalNode:
    node_id: str
    operator: CrossSectionalOperator
    input_node_id: str = field(metadata=REFERENCE_NODE)
    kind: Literal["cross_sectional"]
    lower_quantile: float = 0.01
    upper_quantile: float = 0.99


@dataclass(frozen=True)
class GroupNode:
    node_id: str
    operator: GroupOperator
    input_node_id: str = field(metadata=REFERENCE_NODE)
    group_field_id: str = field(metadata=CATALOG_EQUITY_FIELD)
    kind: Literal["group"]


@dataclass(frozen=True)
class ComparisonNode:
    node_id: str
    operator: FactorComparisonOperator
    left_node_id: str = field(metadata=REFERENCE_NODE)
    right_node_id: str = field(metadata=REFERENCE_NODE)
    kind: Literal["comparison"]


@dataclass(frozen=True)
class ConditionalNode:
    node_id: str
    predicate_node_id: str = field(metadata=REFERENCE_NODE)
    true_node_id: str = field(metadata=REFERENCE_NODE)
    false_node_id: str = field(metadata=REFERENCE_NODE)
    kind: Literal["conditional"]


@dataclass(frozen=True)
class SavedFactorNode:
    node_id: str
    factor_id: str = field(metadata=CATALOG_FACTOR)
    kind: Literal["saved_factor"]


@dataclass(frozen=True)
class SavedSubgraphNode:
    node_id: str
    subgraph_id: str = field(metadata=CATALOG_SUBGRAPH)
    kind: Literal["saved_subgraph"]


ExpressionNode: TypeAlias = (
    FieldNode
    | ConstantNode
    | ParameterNode
    | UnaryNode
    | BinaryNode
    | TimeSeriesNode
    | CrossSectionalNode
    | GroupNode
    | ComparisonNode
    | ConditionalNode
    | SavedFactorNode
    | SavedSubgraphNode
)


@dataclass(frozen=True)
class FactorGraph:
    nodes: tuple[ExpressionNode, ...] = field(metadata=DEFINES_NODE)
    output_node_id: str = field(metadata=REFERENCE_NODE)
    missing_policy: MissingPolicy = MissingPolicy.DROP


def _kind_of(node_type: type) -> str:
    return get_args(get_type_hints(node_type)["kind"])[0]


# `kind` discriminator → node type, derived from each node's `kind: Literal[...]` hint (the
# only declaration). The authoring schema (P1-05) reads this map; nothing restates the union.
EXPRESSION_NODE_KINDS: dict[str, type] = {
    _kind_of(node_type): node_type for node_type in get_args(ExpressionNode)
}
