from __future__ import annotations

import dataclasses
import math
from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum

from strategy_workbench.domain.factor.facade.expression import ParameterNode
from strategy_workbench.domain.factor.facade.validation import (
    FactorValidationSeverity,
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
    CROSS_SECTIONAL_ELIGIBILITY_OPERATORS,
    ChoiceParameter,
    EligibilityOperator,
    FloatParameter,
    IntegerParameter,
    PortfolioSide,
    StrategySpec,
    WeightingMethod,
)


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
) -> ValidationIssue:
    """Build a semantic `ValidationIssue`, checking the code against its registry (D-007).

    This is the only sanctioned way to mint a `strategy.*` issue, in the domain and in the
    application alike: constructing `ValidationIssue` directly would let a code exist that no
    registry row describes, which the schema API and the UI could not explain.

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
        kind=ValidationKind.SEMANTIC,
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


def validate_strategy(
    spec: StrategySpec, *, written_pointers: Collection[str] | None = None
) -> StrategyValidation:
    """Semantic validation of a typed spec.

    `written_pointers` names the JSON Pointers the authoring document set explicitly. The typed
    spec cannot tell a written value from a default, so the applicability warning (spec D4) is
    only emitted for pointers in this set; callers without a document pass nothing. A written
    value equal to the model default is silent too: canonical documents (JSON projection, legacy
    generated source, format conversion) spell out every default and must not warn.
    """
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
    if spec.portfolio.weighting is WeightingMethod.RISK and spec.risk.risk_field_id is None:
        issues.append(
            semantic_issue(
                "strategy.risk.risk_field",
                "risk.risk_field_id",
                "리스크 가중 방식을 쓰려면 리스크 필드를 지정해야 합니다.",
            )
        )
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
            if not parameter.minimum <= parameter.default <= parameter.maximum:
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
            if not parameter.choices or parameter.default not in parameter.choices:
                issues.append(
                    semantic_issue(
                        "strategy.parameter.choice",
                        path,
                        "기본값은 비어 있지 않은 선택지에 포함되어야 합니다.",
                    )
                )

    factor_ids = [factor.factor_id for factor in spec.factors]
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
            factor_ids=tuple(factor_ids),
        )
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
        )

    return StrategyValidation(
        valid=not any(issue.severity is ValidationSeverity.ERROR for issue in issues),
        issues=tuple(issues),
    )
