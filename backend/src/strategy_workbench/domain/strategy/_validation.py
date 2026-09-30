from __future__ import annotations

import dataclasses
import math
from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum

from strategy_workbench.domain.factor.facade.expression import (
    ExpressionNode,
    FactorGraph,
    FieldMetadata,
    FieldNode,
    NodeValueType,
    ParameterNode,
)
from strategy_workbench.domain.factor.facade.operators import (
    OperatorAvailability,
    operator_availability,
    operator_reads_past_sessions,
    required_field_value_type,
)
from strategy_workbench.domain.factor.facade.validation import (
    FactorGraphValidation,
    FactorValidationSeverity,
    node_dependencies,
    unavailable_field_message,
    validate_factor_graph,
)

from ._constraints import (
    EXPRESSION_CODES,
    FIELD_APPLICABILITY,
    SEMANTIC_ONLY_CODES,
    STRATEGY_SCALAR_CONSTRAINTS,
    expression_code,
    field_default,
    resolve_scalar,
)
from ._hydrate import SUPPORTED_SCHEMA_VERSIONS
from ._models import (
    CATALOG_EQUITY_FIELD,
    CROSS_SECTIONAL_ELIGIBILITY_OPERATORS,
    ChoiceParameter,
    EligibilityOperator,
    FloatParameter,
    IntegerParameter,
    PortfolioSide,
    SignalNormalization,
    StrategySpec,
    WeightingMethod,
    composite_factors,
    inverse_risk_factor_id,
    parameter_value_allowed,
)
from ._promotion import PROMOTION_NODE_PREFIX, is_reserved_node_id, promotion_node_ids


class ValidationKind(StrEnum):
    SYNTAX = "syntax"
    SEMANTIC = "semantic"
    CAPABILITY = "capability"


class ValidationSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    path: str
    message: str
    kind: ValidationKind
    severity: ValidationSeverity = ValidationSeverity.ERROR
    node_id: str | None = None  # FactorGraph node the issue is about (graph issues only)


@dataclass(frozen=True)
class StrategyValidation:
    valid: bool
    issues: tuple[ValidationIssue, ...]


# 전략 문서 진단에 그대로 실릴 수 없는 네임스페이스(`semantic_issue` 게이트).
_FACTOR_CODE_PREFIX = "factor."


def _owned_codes() -> frozenset[str]:
    """The registry of `strategy.*` codes, read live so a monkeypatched catalog still applies."""
    return (
        SEMANTIC_ONLY_CODES
        | EXPRESSION_CODES
        | frozenset(constraint.code for constraint in STRATEGY_SCALAR_CONSTRAINTS)
    )


def semantic_issue(
    code: str,
    path: str,
    message: str,
    *,
    severity: ValidationSeverity = ValidationSeverity.ERROR,
    node_id: str | None = None,
    kind: ValidationKind = ValidationKind.SEMANTIC,
) -> ValidationIssue:
    """Build a semantic `ValidationIssue`, checking the code against its registry (D-007).

    This is the only sanctioned way to mint a `strategy.*` issue, in the domain and in the
    application alike: constructing `ValidationIssue` directly would let a code exist that no
    registry row describes, which the schema API and the UI could not explain.

    `kind` 는 진단 분류다. 문서가 틀린 것이 아니라 연결된 데이터가 못 하는 것(어댑터 capability)은
    `CAPABILITY` 로 낸다(P2-07 `strategy.operator.unsupported`).

    `factor.*` 코드는 전략 문서 진단으로 그대로 나갈 수 없다(P1-05). 전에는 alias가 없는 그래프
    코드가 검사 없이 통과해, frontend가 모르는 네임스페이스의 코드가 문제 목록에 섞였다. 지금은
    호출자가 `expression_code()`로 먼저 옮겨야 하고, 옮긴 코드는 `EXPRESSION_CODES`가 검사한다.
    """
    if code.startswith(_FACTOR_CODE_PREFIX):
        raise ValueError(
            "factor graph codes never reach a strategy document — alias them with "
            f"expression_code() first: code={code!r} path={path!r}"
        )
    if code.startswith("strategy.") and code not in _owned_codes():
        raise ValueError(
            "validation code has no owner — add it to SEMANTIC_ONLY_CODES, EXPRESSION_CODES or "
            f"the scalar catalog: code={code!r} path={path!r}"
        )
    return ValidationIssue(
        code=code,
        path=path,
        message=message,
        kind=kind,
        severity=severity,
        node_id=node_id,
    )


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _numeric_leaves(value: object, path: str = "") -> Iterator[tuple[str, float]]:
    """Walk every typed StrategySpec number without duplicating its model shape.

    Bounds remain owned by ``STRATEGY_SCALAR_CONSTRAINTS``. This traversal owns the orthogonal
    invariant that every floating-point leaf is finite, including repeated factor/rule/parameter
    rows and FactorGraph node values that cannot be addressed by today's fixed-pointer catalog.
    """
    if isinstance(value, float):
        yield path, value
        return
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        for model_field in dataclasses.fields(value):
            child_path = f"{path}.{model_field.name}" if path else model_field.name
            yield from _numeric_leaves(getattr(value, model_field.name), child_path)
        return
    if isinstance(value, (tuple, list)):
        for index, item in enumerate(value):
            child_path = f"{path}.{index}" if path else str(index)
            yield from _numeric_leaves(item, child_path)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            yield from _numeric_leaves(item, child_path)


def _eligibility_rule_issues(spec: StrategySpec) -> Iterator[ValidationIssue]:
    """`top_*` 규칙의 `value` 가 cut 크기로 쓸 수 있는 값인지 검사한다 (spec D3 S5).

    절대 규칙(`gt`~`eq`)의 `value` 는 비교 임계값이라 어떤 실수든 뜻이 있지만, `top_percent` 는
    비율이고 `top_count` 는 개수다. 범위를 안 걸면 "상위 20%"를 `20` 으로 적은 문서가 예외도
    진단도 없이 **모집단 전체를 통과**시킨다 — 필터가 있는데 아무것도 거르지 않는 상태로
    백테스트가 완주한다. 이 규칙은 포인터가 배열 항목이라 스칼라 제약 카탈로그
    (`_constraints.py`, 평면 포인터 전용)가 담을 수 없어 validator 가 소유한다.
    """
    for index, rule in enumerate(spec.eligibility.rules):
        if rule.operator not in CROSS_SECTIONAL_ELIGIBILITY_OPERATORS:
            continue
        path = f"eligibility.rules.{index}.value"
        # 분기는 exhaustive 다. 횡단면 연산자가 하나 더 늘었을 때 catch-all 이 그것을 조용히
        # 개수 규칙으로 검사하면, 이 PR 이 `_compare` 에서 없앤 실패 모양이 validator 로 옮겨온다.
        if rule.operator is EligibilityOperator.TOP_PERCENT:
            satisfied = math.isfinite(rule.value) and 0 < rule.value <= 1
            expectation = "상위 비율은 0보다 크고 1 이하인 비율이어야 합니다(20%는 0.2)"
        elif rule.operator is EligibilityOperator.TOP_COUNT:
            satisfied = (
                math.isfinite(rule.value) and rule.value >= 1 and float(rule.value).is_integer()
            )
            expectation = "상위 개수는 1 이상의 정수여야 합니다"
        else:
            raise ValueError(
                "cross-sectional eligibility operator has no value rule — "
                f"operator={rule.operator!r} path={path!r} "
                f"known={[member.value for member in CROSS_SECTIONAL_ELIGIBILITY_OPERATORS]}"
            )
        if not satisfied:
            yield semantic_issue(
                "strategy.eligibility.rule_value",
                path,
                f"{expectation}: got={rule.value!r} field_id={rule.field_id!r}",
            )


def _risk_source_issues(spec: StrategySpec) -> Iterator[ValidationIssue]:
    """리스크 역가중의 원천(필드 또는 팩터)과 그 팩터를 합성에서 뺀 결과를 검사한다 (spec D3 S6).

    원천 배타(`risk_source_conflict`)와 참조 확인(`risk_factor_missing`)은 `weighting` 과 무관한
    문서 규칙이다 — 적용 조건은 `FIELD_APPLICABILITY` 행이 따로 warning 으로 낸다. 제외 warning 과
    알파 0개 error 는 제외가 실제로 일어나는 경우(`inverse_risk_factor_id`)에만 낸다.
    """
    factor_ids = [factor.factor_id for factor in spec.factors]
    risk_factor_id = spec.risk.risk_factor_id
    if risk_factor_id is not None and spec.risk.risk_field_id is not None:
        yield semantic_issue(
            "strategy.risk.risk_source_conflict",
            "risk.risk_factor_id",
            "리스크 역가중 원천은 필드와 팩터 중 하나만 지정할 수 있습니다: "
            f"risk_field_id={spec.risk.risk_field_id!r} risk_factor_id={risk_factor_id!r}",
        )
    if risk_factor_id is not None and risk_factor_id not in factor_ids:
        yield semantic_issue(
            "strategy.risk.risk_factor_missing",
            "risk.risk_factor_id",
            "리스크 팩터가 이 문서의 팩터 목록에 없습니다: "
            f"risk_factor_id={risk_factor_id!r} factors={factor_ids!r}",
        )
    if (
        spec.portfolio.weighting is WeightingMethod.RISK
        and spec.risk.risk_field_id is None
        and risk_factor_id is None
    ):
        yield semantic_issue(
            "strategy.risk.risk_field",
            "risk.risk_field_id",
            "리스크 가중 방식을 쓰려면 리스크 필드나 리스크 팩터를 지정해야 합니다: "
            f"weighting={spec.portfolio.weighting.value!r} "
            f"risk_field_id={spec.risk.risk_field_id!r} risk_factor_id={risk_factor_id!r}",
        )
    excluded = inverse_risk_factor_id(spec)
    if excluded is None or excluded not in factor_ids:
        return
    yield semantic_issue(
        "strategy.risk.risk_factor_excluded",
        "risk.risk_factor_id",
        "리스크 팩터는 역가중에만 쓰이고 합성 점수에서는 빠집니다(가중치 무시): "
        f"risk_factor_id={excluded!r}",
        severity=ValidationSeverity.WARNING,
    )
    if not composite_factors(spec):
        # 막지 않으면 모든 후보의 합성 점수가 `None` 이 되어 정렬이 `security_id` 사전순으로
        # 떨어진다 — 예외도 진단도 없이 엉뚱한 종목이 선정되는 silent wrong result 다.
        yield semantic_issue(
            "strategy.signal.no_alpha_factor",
            "factors",
            "리스크 팩터를 빼고 나면 점수를 낼 알파 팩터가 없습니다. 알파 팩터를 하나 이상 "
            f"추가하세요: risk_factor_id={excluded!r} factors={factor_ids!r}",
        )


def _output_type_issue(
    factor_index: int, factor_id: str, graph: FactorGraph, validation: FactorGraphValidation
) -> ValidationIssue | None:
    """팩터 출력이 종목별 숫자 점수(`numeric_series`)가 아니면 compile error (P2-07, spec D5).

    실행 경계의 `_reject_non_numeric_factor_outputs` 와 같은 판정을 compile 로 앞당긴 것이다.
    boolean 출력은 hydrate 가 이미 0/1 로 승격했으므로 여기 오는 boolean 은 승격이 막힌 경우
    (예약 node_id 충돌, 또는 문서를 거치지 않은 JSON spec)뿐이다. group 출력은 필드 계약이 있어야
    보인다 — 계약 없이 필드 노드는 숫자로 추론된다.
    """
    output = next(
        (
            contract
            for contract in validation.node_contracts
            if contract.node_id == graph.output_node_id
        ),
        None,
    )
    if output is None or output.value_type is NodeValueType.NUMERIC_SERIES:
        return None
    detail = f"factor_id={factor_id!r} actual={output.value_type.value!r} expected='numeric_series'"
    reason = "팩터 출력은 종목별 숫자 점수여야 합니다"
    if output.value_type is NodeValueType.BOOLEAN_SERIES:
        taken = sorted({node.node_id for node in graph.nodes} & set(promotion_node_ids(factor_id)))
        if taken:
            reason = (
                "참/거짓 출력을 0/1 점수로 바꾸는 데 쓰는 예약 node_id 를 이미 다른 노드가 쓰고 "
                "있습니다. 그 노드의 이름을 바꾸세요"
            )
            detail = f"{detail} reserved={taken!r}"
    return semantic_issue(
        "strategy.factor.output_type",
        f"factors.{factor_index}.graph.output_node_id",
        f"{reason}: {detail}",
        node_id=graph.output_node_id,
    )


def _reserved_node_id_issues(
    factor_index: int, graph: FactorGraph, written: frozenset[str]
) -> list[ValidationIssue]:
    """문서가 쓴 node_id 가 승격 예약 접두사로 시작하면 error (리뷰 #232 DEFECT-232-01).

    승격 노드는 compile 이 문서 그래프 **끝에** 붙이므로 문서의 j 번째 노드는 spec 의 j 번째
    노드다. 그래서 문서가 적은 자리(`written`)의 노드만 검사하고 붙인 노드는 건너뛴다. 이 규칙이
    없으면 사용자가 승격 모양을 예약 id 로 그대로 쓴 그래프가 compile 이 승격한 그래프와 바이트까지
    같아져, 화면이 문서 노드를 "compile 이 붙인 노드"로 숨긴다. 문서 없이 검증하는 호출자(JSON
    spec API)는 `written` 이 비어 있어 판정하지 않는다.
    """
    issues: list[ValidationIssue] = []
    for node_index, node in enumerate(graph.nodes):
        pointer = f"/factors/{factor_index}/graph/nodes/{node_index}/node_id"
        if pointer not in written or not is_reserved_node_id(node.node_id):
            continue
        issues.append(
            semantic_issue(
                "strategy.factor.reserved_node_id",
                f"factors.{factor_index}.graph.nodes.{node_index}.node_id",
                f"{PROMOTION_NODE_PREFIX} 로 시작하는 노드 이름은 참/거짓 출력을 점수로 바꿀 때 "
                "compile 이 쓰는 예약 이름입니다. 다른 이름을 쓰세요: "
                f"node_id={node.node_id!r} reserved_prefix={PROMOTION_NODE_PREFIX!r}",
                node_id=node.node_id,
            )
        )
    return issues


# 어댑터 capability 가 없어 unsupported 인 노드에서는 같은 원인(그 필드 타입이 없다)을 그래프 검증이
# 다시 말한다. unsupported 한 줄만 남긴다.
_CAPABILITY_SHADOWED_CODES = frozenset(
    {"factor.graph.group_field_missing", "factor.graph.group_field_type"}
)


def _unsupported_operator_issues(
    factor_index: int, graph: FactorGraph, provided: frozenset[NodeValueType]
) -> list[ValidationIssue]:
    """연결된 어댑터가 요구 필드 타입을 주지 않는 연산자 노드마다 capability error (spec D5)."""
    issues: list[ValidationIssue] = []
    for node_index, node in enumerate(graph.nodes):
        if operator_availability(node.kind, provided) is OperatorAvailability.AVAILABLE:
            continue
        required = required_field_value_type(node.kind)
        operator = getattr(node, "operator", None)
        issues.append(
            semantic_issue(
                "strategy.operator.unsupported",
                f"factors.{factor_index}.graph.nodes.{node_index}",
                "연결된 데이터가 이 연산에 필요한 필드를 제공하지 않아 실행할 수 없습니다: "
                f"node_id={node.node_id!r} kind={node.kind!r} "
                f"operator={getattr(operator, 'value', operator)!r} "
                f"required={getattr(required, 'value', required)!r} "
                f"provided={sorted(item.value for item in provided)!r}",
                node_id=node.node_id,
                kind=ValidationKind.CAPABILITY,
            )
        )
    return issues


def _equity_field_references(value: object, path: str = "") -> Iterator[tuple[str, str]]:
    """팩터 그래프 밖의 equity 필드 참조 `(path, field_id)` 전부.

    대상은 모델 필드 metadata `catalog: equity-field`(`CATALOG_EQUITY_FIELD`)가 붙은 자리다. runtime
    schema 의 `x-catalog: equity-field` 도 같은 metadata 에서 나오므로 목록을 따로 적지 않는다.
    팩터 그래프 안의 필드·그룹 필드는 그래프 검증(`validate_factor_graph`)이 계약으로 본다.
    """
    if isinstance(value, FactorGraph):
        return
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        for model_field in dataclasses.fields(value):
            child_path = f"{path}.{model_field.name}" if path else model_field.name
            child = getattr(value, model_field.name)
            if model_field.metadata.get("catalog") == CATALOG_EQUITY_FIELD["catalog"]:
                if isinstance(child, str):
                    yield child_path, child
                continue
            yield from _equity_field_references(child, child_path)
        return
    if isinstance(value, (tuple, list)):
        for index, item in enumerate(value):
            yield from _equity_field_references(item, f"{path}.{index}" if path else str(index))


def _field_reference_issues(
    spec: StrategySpec, fields: tuple[FieldMetadata, ...], unavailable: Mapping[str, str]
) -> list[ValidationIssue]:
    """그래프 밖 필드 참조를 연결된 어댑터의 계약에서 찾는다(P2-07 리뷰 P1, spec D5).

    네 자리(eligibility 규칙·유동성·레짐·리스크 필드)는 모두 값을 숫자로 읽는다(비교·하한·역가중).
    그래서 없는 필드는 `strategy.field.missing`, 숫자 시계열이 아닌 필드는
    `strategy.field.value_type` 이다. 적용 조건과 무관하게 검사한다 — 문서 참조 무결성이며, 노드·
    팩터 참조가 모두 `*_missing` error 인 것과 같은 규칙이다(P2-06 결정 2).
    """
    by_id = {field.field_id: field for field in fields}
    issues: list[ValidationIssue] = []
    for path, field_id in _equity_field_references(spec):
        metadata = by_id.get(field_id)
        if metadata is None:
            reason = unavailable.get(field_id)
            message = (
                f"연결된 데이터에 없는 필드입니다. 필드 id 를 확인하세요: field_id={field_id!r}"
                if reason is None
                else unavailable_field_message(field_id, reason)
            )
            issues.append(
                semantic_issue("strategy.field.missing", path, f"{message} path={path!r}")
            )
        elif metadata.value_type is not NodeValueType.NUMERIC_SERIES:
            issues.append(
                semantic_issue(
                    "strategy.field.value_type",
                    path,
                    "이 자리는 값을 숫자로 읽으므로 숫자 필드를 써야 합니다: "
                    f"field_id={field_id!r} actual={metadata.value_type.value!r} "
                    "expected='numeric_series'",
                )
            )
    return issues


def _upstream_field_nodes(
    start: ExpressionNode, nodes: Mapping[str, ExpressionNode]
) -> Iterator[FieldNode]:
    """`start` 의 입력을 거슬러 올라가 닿는 필드 잎 전부. 없는 id·순환은 그래프 검증이 말한다."""
    seen: set[str] = set()
    pending = list(node_dependencies(start))
    while pending:
        node_id = pending.pop()
        if node_id in seen or node_id not in nodes:
            continue
        seen.add(node_id)
        node = nodes[node_id]
        if isinstance(node, FieldNode):
            yield node
        pending.extend(node_dependencies(node))


def _unadjusted_price_issues(
    factor_index: int, graph: FactorGraph, fields: Mapping[str, FieldMetadata]
) -> list[ValidationIssue]:
    """원주가 필드가 과거 세션을 읽는 연산자에 흘러들면 warning (BACKLOG-018, 이슈 #214).

    원주가는 분할·증자·병합 날 수준이 끊긴다. 기간 수익률·모멘텀·이평·변동성·지연처럼 과거 세션
    값을 읽는 연산에 넣으면 가짜 급등락·가짜 변동성·이평 돌파 소거가 생기는데, 값은 정상으로 보여
    결과가 조용히 틀린다. 같은 날 값끼리의 연산(가격 필터·거래대금·같은 날 비율)은 오염되지 않는다.

    두 성질은 손으로 적지 않는다: 어떤 연산자가 과거 세션을 읽는지는 연산자 카탈로그
    (`OperatorDefinition.reads_past_sessions`), 어떤 필드가 원주가인지와 그 조정 짝은 연결된
    어댑터의 필드 계약(`FieldMetadata.adjusted_field_id`)이 답한다. 막지 않고 알린다 — 절대 가격
    수준을 일부러 비교하는 전략도 있다. 잎마다 한 번, 고칠 자리인 `field_id` 를 짚는다.
    """
    nodes = {node.node_id: node for node in graph.nodes}
    readers: dict[str, ExpressionNode] = {}
    for node in graph.nodes:
        operator = getattr(node, "operator", None)
        if not operator_reads_past_sessions(node.kind, getattr(operator, "value", operator)):
            continue
        for leaf in _upstream_field_nodes(node, nodes):
            readers.setdefault(leaf.node_id, node)
    issues: list[ValidationIssue] = []
    for node_index, node in enumerate(graph.nodes):
        reader = readers.get(node.node_id)
        if reader is None or not isinstance(node, FieldNode):
            continue
        metadata = fields.get(node.field_id)
        if metadata is None or metadata.adjusted_field_id is None:
            continue
        operator = getattr(reader, "operator", None)
        issues.append(
            semantic_issue(
                "strategy.field.unadjusted_price",
                f"factors.{factor_index}.graph.nodes.{node_index}.field_id",
                "분할·증자·병합에 조정하지 않은 원주가를 과거 세션과 비교하는 연산에 넣어 결과가 "
                f"분할·증자에 오염될 수 있습니다. {metadata.adjusted_field_id} 를 쓰세요: "
                f"field_id={node.field_id!r} adjusted_field_id={metadata.adjusted_field_id!r} "
                f"reader={reader.node_id!r} operator={getattr(operator, 'value', operator)!r}",
                severity=ValidationSeverity.WARNING,
                node_id=node.node_id,
            )
        )
    return issues


# 필드 계약 없이 추론한 필드 노드의 단위(`domain/factor` `_infer_contract`). 판정할 수 없는 값이다.
_UNKNOWN_UNIT = "unknown"


def _unit_mismatch_issue(
    spec: StrategySpec, output_units: dict[str, str]
) -> ValidationIssue | None:
    """정규화 없이 단위가 다른 알파 팩터를 더하면 warning (P2-07, spec D4).

    `none` 은 원시 점수의 가중 합이라 큰 단위 팩터가 합성 점수를 지배한다. 1.1 에서 올라온 문서가
    예전 결과를 그대로 내는 모드라 막지 않고 알린다. 합성에 들어가는 팩터(`composite_factors`)만
    비교하고, 단위를 모르는 팩터(필드 계약 없음)는 판정에서 뺀다.
    """
    if spec.signal.normalization is not SignalNormalization.NONE:
        return None
    units = {
        factor.factor_id: output_units[factor.factor_id]
        for factor in composite_factors(spec)
        if output_units.get(factor.factor_id, _UNKNOWN_UNIT) != _UNKNOWN_UNIT
    }
    if len(set(units.values())) <= 1:
        return None
    return semantic_issue(
        "strategy.signal.unit_mismatch",
        "signal.normalization",
        "정규화 없이(`none`) 단위가 다른 팩터 점수를 그대로 더합니다. 단위가 큰 팩터가 합성 점수를 "
        "좌우하니 결합 정규화를 rank 나 zscore 로 바꾸는 것을 고려하세요: "
        f"normalization='none' units={units!r}",
        severity=ValidationSeverity.WARNING,
    )


def validate_strategy(
    spec: StrategySpec,
    *,
    written_pointers: Collection[str] | None = None,
    fields: Collection[FieldMetadata] | None = None,
    unavailable_fields: Mapping[str, str] | None = None,
) -> StrategyValidation:
    """Semantic validation of a typed spec.

    `written_pointers` names the JSON Pointers the authoring document set explicitly. The typed
    spec cannot tell a written value from a default, so the applicability warning (spec D4) is
    only emitted for pointers in this set; callers without a document pass nothing. 같은 집합으로
    문서가 쓴 노드와 compile 이 붙인 승격 노드를 가려 예약 접두사 node_id 를 거절한다
    (`strategy.factor.reserved_node_id`, 리뷰 #232). A written
    value equal to the model default is silent too: canonical documents (JSON projection, legacy
    generated source, format conversion) spell out every default and must not warn.

    `fields` 는 연결된 equity 어댑터가 제공하는 필드 계약 전부다(P2-07, spec D5). 주어지면 그래프
    검증이 계약을 요구해 없는 `field_id` 가 `strategy.expression.field_missing` 으로 저장 전에
    나고, 단위·그룹 타입도 실제 계약으로 추론한다. 어댑터가 없는 컨텍스트(CLI·테스트)는 None 을
    넘기고 지금과 같이 계약 없이 검증한다. `unavailable_fields` 는 어댑터가 선언했지만 주지 않는
    필드 → 사유다 — 없는 필드가 그 안에 있으면 진단이 "필드 id 를 확인하세요" 대신 그 사유(카탈로그
    재생성 같은 조치)를 싣는다(#316).
    """
    field_contracts = None if fields is None else tuple(fields)
    fields_by_id = {field.field_id: field for field in field_contracts or ()}
    provided = (
        None
        if field_contracts is None
        else frozenset(field.value_type for field in field_contracts)
    )
    issues: list[ValidationIssue] = []
    written = frozenset(written_pointers or ())
    for applicability in FIELD_APPLICABILITY:
        if applicability.owned_by_error is not None:
            continue  # 아래의 기존 error 규칙이 같은 관계를 보고한다
        if applicability.pointer not in written or applicability.applies_to(spec):
            continue
        if resolve_scalar(spec, applicability.pointer) == field_default(applicability.pointer):
            continue
        expectation = " 그리고 ".join(
            condition.describe() for condition in applicability.conditions
        )
        issues.append(
            semantic_issue(
                "strategy.field.inapplicable",
                applicability.path,
                f"이 필드는 현재 모드에서 읽히지 않습니다: {expectation}일 때만 적용됩니다.",
                severity=ValidationSeverity.WARNING,
            )
        )
    bounded_paths = {constraint.path for constraint in STRATEGY_SCALAR_CONSTRAINTS}
    issues.extend(
        semantic_issue(
            "strategy.number.non_finite",
            path,
            "StrategySpec numeric values must be finite before execution or hashing: "
            f"path={path!r} value={value!r}",
        )
        for path, value in _numeric_leaves(spec)
        if path not in bounded_paths and not math.isfinite(value)
    )
    if spec.identity.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        # 문서 경로는 envelope가 현재 버전을 넣으므로 여기 오지 않는다. JSON spec API가 identity를
        # 직접 받을 때 은퇴한 버전을 저장·실행하려는 요청을 저장소 무결성 오류(500)가 아니라
        # 검증 오류로 막는다.
        issues.append(
            semantic_issue(
                "strategy.schema_version.unsupported",
                "identity.schema_version",
                "지원하지 않는 schema_version입니다: "
                f"got={spec.identity.schema_version!r} supported={SUPPORTED_SCHEMA_VERSIONS}",
            )
        )
    if not spec.title.strip():
        issues.append(semantic_issue("strategy.title.empty", "title", "전략 이름을 입력하세요."))
    # 기간·유니버스 검증은 1.2 부터 실행 설정이 소유한다 — `RunEnvironment.__post_init__`
    # (`domain/backtest/_models.py`)이 같은 두 규칙을 건다. 전략 문서에는 그 필드가 없다.
    if not spec.factors:
        issues.append(
            semantic_issue("strategy.factor.required", "factors", "팩터를 하나 이상 추가하세요.")
        )
    issues.extend(_eligibility_rule_issues(spec))
    if field_contracts is not None:
        issues.extend(_field_reference_issues(spec, field_contracts, unavailable_fields or {}))
    # Scalar bounds are owned by the constraint catalog (P1-04); the schema API reads the same rows.
    for constraint in STRATEGY_SCALAR_CONSTRAINTS:
        value = resolve_scalar(spec, constraint.pointer)
        if value is None:
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise TypeError(
                "scalar constraint applied to a non-numeric field — "
                f"pointer={constraint.pointer!r} value={value!r}"
            )
        if not constraint.satisfied_by(value):
            issues.append(semantic_issue(constraint.code, constraint.path, constraint.message))
    if spec.portfolio.minimum_liquidity is not None and spec.portfolio.liquidity_field_id is None:
        issues.append(
            semantic_issue(
                "strategy.portfolio.liquidity_field",
                "portfolio.liquidity_field_id",
                "최소 유동성을 쓰려면 유동성 필드를 지정해야 합니다.",
            )
        )
    if abs(spec.risk.net_exposure) > spec.risk.gross_exposure:
        issues.append(
            semantic_issue(
                "strategy.risk.net_exposure",
                "risk.net_exposure",
                "순 익스포저 절댓값은 총 익스포저 이하여야 합니다.",
            )
        )
    if (
        spec.portfolio.side is PortfolioSide.LONG_ONLY
        and spec.risk.net_exposure != spec.risk.gross_exposure
    ):
        issues.append(
            semantic_issue(
                "strategy.risk.long_only_exposure",
                "risk.net_exposure",
                "롱온리 전략은 총 익스포저와 순 익스포저가 같아야 합니다.",
            )
        )
    issues.extend(_risk_source_issues(spec))
    if spec.risk.sector_neutral and spec.portfolio.side is PortfolioSide.LONG_ONLY:
        issues.append(
            semantic_issue(
                "strategy.risk.sector_neutral_side",
                "risk.sector_neutral",
                "섹터 중립화는 롱숏 전략에서만 사용할 수 있습니다.",
            )
        )
    if spec.signal.regime_field_id is None and spec.signal.regime_minimum is not None:
        issues.append(
            semantic_issue(
                "strategy.signal.regime_field",
                "signal.regime_field_id",
                "레짐 기준값을 쓰려면 레짐 필드를 지정해야 합니다.",
            )
        )

    parameter_ids = [parameter.parameter_id for parameter in spec.parameters]
    if len(parameter_ids) != len(set(parameter_ids)):
        issues.append(
            semantic_issue(
                "strategy.parameter.duplicate", "parameters", "파라미터 ID는 중복될 수 없습니다."
            )
        )
    for index, parameter in enumerate(spec.parameters):
        path = f"parameters.{index}"
        if isinstance(parameter, (FloatParameter, IntegerParameter)):
            if parameter.minimum > parameter.maximum:
                issues.append(
                    semantic_issue(
                        "strategy.parameter.bounds", path, "최솟값은 최댓값 이하여야 합니다."
                    )
                )
            if not parameter_value_allowed(parameter, parameter.default):
                issues.append(
                    semantic_issue(
                        "strategy.parameter.default", path, "기본값은 탐색 범위 안에 있어야 합니다."
                    )
                )
            if parameter.step is not None and parameter.step <= 0:
                issues.append(
                    semantic_issue(
                        "strategy.parameter.step", path, "탐색 간격은 0보다 커야 합니다."
                    )
                )
        elif isinstance(parameter, ChoiceParameter):
            if not parameter_value_allowed(parameter, parameter.default):
                issues.append(
                    semantic_issue(
                        "strategy.parameter.choice",
                        path,
                        "기본값은 비어 있지 않은 선택지에 포함되어야 합니다.",
                    )
                )

    factor_ids = [factor.factor_id for factor in spec.factors]
    output_units: dict[str, str] = {}
    if len(factor_ids) != len(set(factor_ids)):
        issues.append(
            semantic_issue("strategy.factor.duplicate", "factors", "팩터 ID는 중복될 수 없습니다.")
        )
    numeric_parameter_ids = {
        parameter.parameter_id
        for parameter in spec.parameters
        if not isinstance(parameter, ChoiceParameter)
        or all(_is_number(choice) for choice in (parameter.default, *parameter.choices))
    }
    for factor_index, factor in enumerate(spec.factors):
        base = f"factors.{factor_index}"
        issues.extend(_reserved_node_id_issues(factor_index, factor.graph, written))
        for node_index, node in enumerate(factor.graph.nodes):
            if (
                isinstance(node, ParameterNode)
                and node.parameter_id in parameter_ids
                and node.parameter_id not in numeric_parameter_ids
            ):
                issues.append(
                    semantic_issue(
                        "strategy.expression.parameter_type",
                        f"{base}.graph.nodes.{node_index}",
                        "팩터 그래프의 파라미터 노드는 숫자 파라미터만 참조할 수 있습니다: "
                        f"parameter_id={node.parameter_id!r}",
                    )
                )
        validation = validate_factor_graph(
            factor.graph,
            parameter_ids=tuple(parameter_ids),
            fields=field_contracts or (),
            require_field_metadata=field_contracts is not None,
            unavailable_fields=unavailable_fields,
        )
        unsupported = (
            []
            if provided is None
            else _unsupported_operator_issues(factor_index, factor.graph, provided)
        )
        unsupported_nodes = {issue.node_id for issue in unsupported}
        issues.extend(unsupported)
        issues.extend(
            semantic_issue(
                expression_code(factor_issue.code),
                f"{base}.graph.{factor_issue.path}",
                factor_issue.message,
                severity=(
                    ValidationSeverity.ERROR
                    if factor_issue.severity is FactorValidationSeverity.ERROR
                    else ValidationSeverity.WARNING
                ),
                node_id=factor_issue.node_id,
            )
            for factor_issue in validation.issues
            if not (
                factor_issue.node_id in unsupported_nodes
                and factor_issue.code in _CAPABILITY_SHADOWED_CODES
            )
        )
        output_issue = _output_type_issue(factor_index, factor.factor_id, factor.graph, validation)
        if output_issue is not None:
            issues.append(output_issue)
        issues.extend(_unadjusted_price_issues(factor_index, factor.graph, fields_by_id))
        output_units.update(
            (factor.factor_id, contract.unit)
            for contract in validation.node_contracts
            if contract.node_id == factor.graph.output_node_id
        )
    unit_issue = _unit_mismatch_issue(spec, output_units)
    if unit_issue is not None:
        issues.append(unit_issue)

    return StrategyValidation(
        valid=not any(issue.severity is ValidationSeverity.ERROR for issue in issues),
        issues=tuple(issues),
    )
