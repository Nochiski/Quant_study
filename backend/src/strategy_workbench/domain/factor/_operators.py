"""연산자 정의 레지스트리 — `(kind, operator)` 하나가 화면에서 무엇으로 보이는지의 정본 (P1-03).

`_nodes.py`가 문법을 소유한다: 어떤 노드 kind에 어떤 operator enum이 붙는지, 노드가 어떤
property를 갖는지. `_evaluation.py`가 계산을, `_validation.py`가 출력 타입·단위 추론을 소유한다.
이 모듈은 그 셋 위에 **설명 계층**을 얹는다 — 팔레트·드롭다운·Contract Inspector가 연산자를
사람 말로 보이려면 필요한데 어느 모듈도 답하지 않던 사실들이다(spec D8).

- 문장은 소비자가 소유한다. 여기서는 키(`description_key`·`formula_key`)만 발행하고 한국어·영어
  문장은 frontend i18n이 렌더한다. 적용 조건(`x-applicable-when`)과 같은 소유 규칙이다.
- 파생 가능한 것은 적지 않는다. `kind`·`arity`·`params`의 필수 여부·operator enum은 전부 노드
  dataclass에서 읽는다. 손으로 적는 것은 "이 연산자가 무엇을 읽고 무엇을 내는가"뿐이다.
- `availability`는 정의 값이다. 연결된 어댑터 capability로 실제 가용성을 판정하는 것은 P2-04이며
  여기서는 "GROUP_SERIES 필드를 주는 어댑터가 아직 없다"는 spec D5의 정적 사실만 담는다.

`tests/domain/test_factor_operators.py`가 스키마 enum과 이 표의 키를 양방향으로 대조하고,
`output_type_rule`·`unit_rule`을 실제 `validate_factor_graph` 결과와 대조한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import MISSING, dataclass
from dataclasses import fields as dataclass_fields
from enum import StrEnum
from types import MappingProxyType
from typing import get_type_hints

from ._nodes import (
    BinaryNode,
    BinaryOperator,
    ComparisonNode,
    CrossSectionalNode,
    CrossSectionalOperator,
    FactorComparisonOperator,
    GroupNode,
    GroupOperator,
    TimeSeriesNode,
    TimeSeriesOperator,
    UnaryNode,
    UnaryOperator,
    _kind_of,
)


class OperatorAvailability(StrEnum):
    """정의 시점의 가용성. 실제 판정(어댑터 capability)은 P2-04이 추가한다."""

    AVAILABLE = "available"
    UNSUPPORTED = "unsupported"


class OutputTypeRule(StrEnum):
    """`_validation.py`가 이 연산자의 출력 `NodeValueType`을 정하는 방식."""

    SAME_AS_INPUT = "same_as_input"  # 입력 타입을 그대로 물려준다 (negate)
    NUMERIC_SERIES = "numeric_series"  # 입력이 scalar여도 숫자 시계열이 된다
    # 한쪽이 시계열이면 시계열, 둘 다 scalar면 scalar
    NUMERIC_IF_ANY_SERIES = "numeric_if_any_series"
    BOOLEAN_SERIES = "boolean_series"  # 참/거짓 시계열


class UnitRule(StrEnum):
    """`_validation.py`가 이 연산자의 출력 단위를 정하는 방식."""

    # 첫 입력의 단위 그대로. 더하기·빼기는 좌우 단위가 다르면 error다(검증이 막는다).
    SAME_AS_INPUT = "same_as_input"
    COMBINED = "combined"  # 두 입력 단위를 곱/나눗셈으로 합친다 — `(a*b)`, `(a/b)`
    BOOLEAN = "boolean"  # `bool`


@dataclass(frozen=True)
class OperatorParameter:
    """연산자가 읽는 노드 property 하나. 필수 여부는 노드 dataclass의 기본값이 정한다."""

    property_name: str
    required: bool


@dataclass(frozen=True)
class OperatorDefinition:
    """`(kind, operator)` 조합 하나에 대한 화면용 정의."""

    kind: str
    operator: str
    arity: int  # 입력 노드 개수 (`x-reference: node` property 수)
    params: tuple[OperatorParameter, ...]
    output_type_rule: OutputTypeRule
    unit_rule: UnitRule
    availability: OperatorAvailability
    description_key: str  # i18n 키 stem — `<stem>`은 이름, `<stem>.description`은 한 줄 설명
    formula_key: str  # `<stem>.formula`
    example: str  # YAML flow 표기 한 줄


def _node_input_count(node_type: type) -> int:
    return sum(
        1 for item in dataclass_fields(node_type) if item.metadata.get("reference") == "node"
    )


def _parameter(node_type: type, property_name: str) -> OperatorParameter:
    field = next(item for item in dataclass_fields(node_type) if item.name == property_name)
    has_default = field.default is not MISSING or field.default_factory is not MISSING
    return OperatorParameter(property_name=property_name, required=not has_default)


def _definition(
    node_type: type,
    operator: StrEnum,
    *,
    params: tuple[str, ...] = (),
    output_type_rule: OutputTypeRule,
    unit_rule: UnitRule,
    availability: OperatorAvailability = OperatorAvailability.AVAILABLE,
    example: str,
) -> OperatorDefinition:
    kind = _kind_of(node_type)
    stem = f"strategy.operator.{kind}.{operator.value}"
    return OperatorDefinition(
        kind=kind,
        operator=operator.value,
        arity=_node_input_count(node_type),
        params=tuple(_parameter(node_type, name) for name in params),
        output_type_rule=output_type_rule,
        unit_rule=unit_rule,
        availability=availability,
        description_key=stem,
        formula_key=f"{stem}.formula",
        example=example,
    )


_DEFINITIONS: tuple[OperatorDefinition, ...] = (
    _definition(
        UnaryNode,
        UnaryOperator.NEGATE,
        output_type_rule=OutputTypeRule.SAME_AS_INPUT,
        unit_rule=UnitRule.SAME_AS_INPUT,
        example="{ kind: unary, node_id: inverted, operator: negate, input_node_id: pbr }",
    ),
    _definition(
        UnaryNode,
        UnaryOperator.LAG,
        params=("periods",),
        output_type_rule=OutputTypeRule.NUMERIC_SERIES,
        unit_rule=UnitRule.SAME_AS_INPUT,
        example=(
            "{ kind: unary, node_id: close_5d_ago, operator: lag, input_node_id: close,"
            " periods: 5 }"
        ),
    ),
    _definition(
        BinaryNode,
        BinaryOperator.ADD,
        output_type_rule=OutputTypeRule.NUMERIC_IF_ANY_SERIES,
        unit_rule=UnitRule.SAME_AS_INPUT,
        example=(
            "{ kind: binary, node_id: total, operator: add, left_node_id: equity,"
            " right_node_id: debt }"
        ),
    ),
    _definition(
        BinaryNode,
        BinaryOperator.SUBTRACT,
        output_type_rule=OutputTypeRule.NUMERIC_IF_ANY_SERIES,
        unit_rule=UnitRule.SAME_AS_INPUT,
        example=(
            "{ kind: binary, node_id: spread, operator: subtract, left_node_id: close,"
            " right_node_id: ma_20 }"
        ),
    ),
    _definition(
        BinaryNode,
        BinaryOperator.MULTIPLY,
        output_type_rule=OutputTypeRule.NUMERIC_IF_ANY_SERIES,
        unit_rule=UnitRule.COMBINED,
        example=(
            "{ kind: binary, node_id: traded_value, operator: multiply, left_node_id: close,"
            " right_node_id: volume }"
        ),
    ),
    _definition(
        BinaryNode,
        BinaryOperator.DIVIDE,
        output_type_rule=OutputTypeRule.NUMERIC_IF_ANY_SERIES,
        unit_rule=UnitRule.COMBINED,
        example=(
            "{ kind: binary, node_id: pbr, operator: divide, left_node_id: market_cap,"
            " right_node_id: book_value }"
        ),
    ),
    _definition(
        TimeSeriesNode,
        TimeSeriesOperator.MEAN,
        params=("window", "lag"),
        output_type_rule=OutputTypeRule.NUMERIC_SERIES,
        unit_rule=UnitRule.SAME_AS_INPUT,
        example=(
            "{ kind: time_series, node_id: ma_20, operator: mean, input_node_id: close,"
            " window: 20 }"
        ),
    ),
    _definition(
        TimeSeriesNode,
        TimeSeriesOperator.STANDARD_DEVIATION,
        params=("window", "lag"),
        output_type_rule=OutputTypeRule.NUMERIC_SERIES,
        unit_rule=UnitRule.SAME_AS_INPUT,
        example=(
            "{ kind: time_series, node_id: vol_60, operator: std, input_node_id: daily_return,"
            " window: 60 }"
        ),
    ),
    _definition(
        TimeSeriesNode,
        TimeSeriesOperator.MOMENTUM,
        params=("window", "lag"),
        output_type_rule=OutputTypeRule.NUMERIC_SERIES,
        unit_rule=UnitRule.SAME_AS_INPUT,
        example=(
            "{ kind: time_series, node_id: mom_12_1, operator: momentum, input_node_id: close,"
            " window: 252, lag: 21 }"
        ),
    ),
    _definition(
        TimeSeriesNode,
        TimeSeriesOperator.DELTA,
        params=("window", "lag"),
        output_type_rule=OutputTypeRule.NUMERIC_SERIES,
        unit_rule=UnitRule.SAME_AS_INPUT,
        example=(
            "{ kind: time_series, node_id: growth, operator: delta, input_node_id: revenue,"
            " window: 252 }"
        ),
    ),
    _definition(
        TimeSeriesNode,
        TimeSeriesOperator.MINIMUM,
        params=("window", "lag"),
        output_type_rule=OutputTypeRule.NUMERIC_SERIES,
        unit_rule=UnitRule.SAME_AS_INPUT,
        example=(
            "{ kind: time_series, node_id: low_52w, operator: min, input_node_id: close,"
            " window: 252 }"
        ),
    ),
    _definition(
        TimeSeriesNode,
        TimeSeriesOperator.MAXIMUM,
        params=("window", "lag"),
        output_type_rule=OutputTypeRule.NUMERIC_SERIES,
        unit_rule=UnitRule.SAME_AS_INPUT,
        example=(
            "{ kind: time_series, node_id: high_52w, operator: max, input_node_id: close,"
            " window: 252 }"
        ),
    ),
    _definition(
        CrossSectionalNode,
        CrossSectionalOperator.RANK,
        output_type_rule=OutputTypeRule.NUMERIC_SERIES,
        unit_rule=UnitRule.SAME_AS_INPUT,
        example=(
            "{ kind: cross_sectional, node_id: ranked, operator: rank, input_node_id: score }"
        ),
    ),
    _definition(
        CrossSectionalNode,
        CrossSectionalOperator.ZSCORE,
        output_type_rule=OutputTypeRule.NUMERIC_SERIES,
        unit_rule=UnitRule.SAME_AS_INPUT,
        example=(
            "{ kind: cross_sectional, node_id: standardized, operator: zscore,"
            " input_node_id: score }"
        ),
    ),
    _definition(
        CrossSectionalNode,
        CrossSectionalOperator.WINSORIZE,
        params=("lower_quantile", "upper_quantile"),
        output_type_rule=OutputTypeRule.NUMERIC_SERIES,
        unit_rule=UnitRule.SAME_AS_INPUT,
        example=(
            "{ kind: cross_sectional, node_id: clipped, operator: winsorize,"
            " input_node_id: score, lower_quantile: 0.01, upper_quantile: 0.99 }"
        ),
    ),
    _definition(
        CrossSectionalNode,
        CrossSectionalOperator.DEMEAN,
        output_type_rule=OutputTypeRule.NUMERIC_SERIES,
        unit_rule=UnitRule.SAME_AS_INPUT,
        example=(
            "{ kind: cross_sectional, node_id: centered, operator: demean, input_node_id: score }"
        ),
    ),
    _definition(
        GroupNode,
        GroupOperator.NEUTRALIZE,
        params=("group_field_id",),
        output_type_rule=OutputTypeRule.NUMERIC_SERIES,
        unit_rule=UnitRule.SAME_AS_INPUT,
        availability=OperatorAvailability.UNSUPPORTED,
        example=(
            "{ kind: group, node_id: sector_neutral, operator: neutralize, input_node_id: score,"
            " group_field_id: sector }"
        ),
    ),
    _definition(
        GroupNode,
        GroupOperator.RANK,
        params=("group_field_id",),
        output_type_rule=OutputTypeRule.NUMERIC_SERIES,
        unit_rule=UnitRule.SAME_AS_INPUT,
        availability=OperatorAvailability.UNSUPPORTED,
        example=(
            "{ kind: group, node_id: sector_rank, operator: rank, input_node_id: score,"
            " group_field_id: sector }"
        ),
    ),
    _definition(
        ComparisonNode,
        FactorComparisonOperator.GREATER_THAN,
        output_type_rule=OutputTypeRule.BOOLEAN_SERIES,
        unit_rule=UnitRule.BOOLEAN,
        example=(
            "{ kind: comparison, node_id: liquid, operator: gt, left_node_id: traded_value,"
            " right_node_id: threshold }"
        ),
    ),
    _definition(
        ComparisonNode,
        FactorComparisonOperator.GREATER_THAN_OR_EQUAL,
        output_type_rule=OutputTypeRule.BOOLEAN_SERIES,
        unit_rule=UnitRule.BOOLEAN,
        example=(
            "{ kind: comparison, node_id: at_least, operator: gte, left_node_id: roe,"
            " right_node_id: threshold }"
        ),
    ),
    _definition(
        ComparisonNode,
        FactorComparisonOperator.LESS_THAN,
        output_type_rule=OutputTypeRule.BOOLEAN_SERIES,
        unit_rule=UnitRule.BOOLEAN,
        example=(
            "{ kind: comparison, node_id: cheap, operator: lt, left_node_id: pbr,"
            " right_node_id: threshold }"
        ),
    ),
    _definition(
        ComparisonNode,
        FactorComparisonOperator.LESS_THAN_OR_EQUAL,
        output_type_rule=OutputTypeRule.BOOLEAN_SERIES,
        unit_rule=UnitRule.BOOLEAN,
        example=(
            "{ kind: comparison, node_id: at_most, operator: lte, left_node_id: debt_ratio,"
            " right_node_id: threshold }"
        ),
    ),
    _definition(
        ComparisonNode,
        FactorComparisonOperator.EQUAL,
        output_type_rule=OutputTypeRule.BOOLEAN_SERIES,
        unit_rule=UnitRule.BOOLEAN,
        example=(
            "{ kind: comparison, node_id: exactly, operator: eq, left_node_id: flag,"
            " right_node_id: one }"
        ),
    ),
)


OPERATOR_DEFINITIONS: Mapping[tuple[str, str], OperatorDefinition] = MappingProxyType(
    {(definition.kind, definition.operator): definition for definition in _DEFINITIONS}
)


def operator_definitions() -> tuple[OperatorDefinition, ...]:
    """선언 순서(노드 kind 순 × enum 선언 순)의 정의 전부. 팔레트 표시 순서다."""
    return _DEFINITIONS


_KEYS_BY_ENUM: Mapping[type, Mapping[str, str]] = MappingProxyType(
    {
        get_type_hints(node_type)["operator"]: MappingProxyType(
            {
                definition.operator: definition.description_key
                for definition in _DEFINITIONS
                if definition.kind == kind
            }
        )
        for kind, node_type in (
            (_kind_of(node_type), node_type)
            for node_type in (
                UnaryNode,
                BinaryNode,
                TimeSeriesNode,
                CrossSectionalNode,
                GroupNode,
                ComparisonNode,
            )
        )
    }
)


def operator_description_keys(enum_type: type | None) -> Mapping[str, str] | None:
    """노드 연산자 enum이면 `값 → 설명 키 stem`, 아니면 None.

    runtime schema가 `operator` property에 `x-operator`를 붙일 때 쓴다. 소비자가 enum 값에서
    키를 조립하지 않게 하려는 것이므로 매핑을 그대로 내려준다.
    """
    if enum_type is None:
        return None
    return _KEYS_BY_ENUM.get(enum_type)
