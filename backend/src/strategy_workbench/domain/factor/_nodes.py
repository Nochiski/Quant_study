from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, TypeAlias


class UnaryOperator(StrEnum):
    NEGATE = "negate"
    LAG = "lag"
    RANK = "rank"
    ZSCORE = "zscore"
    WINSORIZE = "winsorize"
    NEUTRALIZE = "neutralize"


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
    field_id: str
    kind: Literal["field"]


@dataclass(frozen=True)
class ConstantNode:
    node_id: str
    value: float
    kind: Literal["constant"]


@dataclass(frozen=True)
class ParameterNode:
    node_id: str
    parameter_id: str
    kind: Literal["parameter"]


@dataclass(frozen=True)
class UnaryNode:
    node_id: str
    operator: UnaryOperator
    input_node_id: str
    kind: Literal["unary"]
    periods: int | None = None


@dataclass(frozen=True)
class BinaryNode:
    node_id: str
    operator: BinaryOperator
    left_node_id: str
    right_node_id: str
    kind: Literal["binary"]


@dataclass(frozen=True)
class TimeSeriesNode:
    node_id: str
    operator: TimeSeriesOperator
    input_node_id: str
    window: int
    kind: Literal["time_series"]
    lag: int = 0


@dataclass(frozen=True)
class CrossSectionalNode:
    node_id: str
    operator: CrossSectionalOperator
    input_node_id: str
    kind: Literal["cross_sectional"]
    lower_quantile: float = 0.01
    upper_quantile: float = 0.99


@dataclass(frozen=True)
class GroupNode:
    node_id: str
    operator: GroupOperator
    input_node_id: str
    group_field_id: str
    kind: Literal["group"]


@dataclass(frozen=True)
class ComparisonNode:
    node_id: str
    operator: FactorComparisonOperator
    left_node_id: str
    right_node_id: str
    kind: Literal["comparison"]


@dataclass(frozen=True)
class ConditionalNode:
    node_id: str
    predicate_node_id: str
    true_node_id: str
    false_node_id: str
    kind: Literal["conditional"]


@dataclass(frozen=True)
class SavedFactorNode:
    node_id: str
    factor_id: str
    kind: Literal["saved_factor"]


@dataclass(frozen=True)
class SavedSubgraphNode:
    node_id: str
    subgraph_id: str
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
    nodes: tuple[ExpressionNode, ...]
    output_node_id: str
    missing_policy: MissingPolicy = MissingPolicy.DROP


# `kind` discriminator → node type. The authoring schema (P1-05) and hydrate dispatch on this map;
# no other layer restates the union.
EXPRESSION_NODE_KINDS: dict[str, type] = {
    "field": FieldNode,
    "constant": ConstantNode,
    "parameter": ParameterNode,
    "unary": UnaryNode,
    "binary": BinaryNode,
    "time_series": TimeSeriesNode,
    "cross_sectional": CrossSectionalNode,
    "group": GroupNode,
    "comparison": ComparisonNode,
    "conditional": ConditionalNode,
    "saved_factor": SavedFactorNode,
    "saved_subgraph": SavedSubgraphNode,
}
