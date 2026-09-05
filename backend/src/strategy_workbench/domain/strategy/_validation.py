from __future__ import annotations

import dataclasses
import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum

from strategy_workbench.domain.factor.facade.expression import ParameterNode
from strategy_workbench.domain.factor.facade.validation import (
    FactorValidationSeverity,
    validate_factor_graph,
)

from ._constraints import (
    EXPRESSION_CODES,
    SEMANTIC_ONLY_CODES,
    STRATEGY_SCALAR_CONSTRAINTS,
    resolve_scalar,
)
from ._models import (
    ChoiceParameter,
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
    registry row describes, which the schema API and the UI could not explain. Codes outside the
    `strategy.` namespace pass through unchecked — they belong to another registry (today
    `domain.factor` graph codes, forwarded verbatim when the validator has no alias for them).
    """
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


def validate_strategy(spec: StrategySpec) -> StrategyValidation:
    issues: list[ValidationIssue] = []
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
    if not spec.title.strip():
        issues.append(semantic_issue("strategy.title.empty", "title", "전략 이름을 입력하세요."))
    if spec.data.start > spec.data.end:
        issues.append(
            semantic_issue(
                "strategy.data.date_order", "data.end", "종료일은 시작일 이후여야 합니다."
            )
        )
    if not spec.data.universe_id.strip():
        issues.append(
            semantic_issue(
                "strategy.data.universe_empty", "data.universe_id", "유니버스를 선택하세요."
            )
        )
    if not spec.factors.factors:
        issues.append(
            semantic_issue("strategy.factor.required", "factors", "팩터를 하나 이상 추가하세요.")
        )
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

    factor_ids = [factor.factor_id for factor in spec.factors.factors]
    if len(factor_ids) != len(set(factor_ids)):
        issues.append(
            semantic_issue("strategy.factor.duplicate", "factors", "팩터 ID는 중복될 수 없습니다.")
        )
    code_aliases = {
        "factor.graph.duplicate_node": "strategy.expression.duplicate_node",
        "factor.graph.output_missing": "strategy.expression.output_missing",
        "factor.graph.input_missing": "strategy.expression.input_missing",
        "factor.graph.parameter_missing": "strategy.expression.parameter_missing",
        "factor.graph.lag_periods": "strategy.expression.lag_periods",
    }
    numeric_parameter_ids = {
        parameter.parameter_id
        for parameter in spec.parameters
        if not isinstance(parameter, ChoiceParameter)
        or all(_is_number(choice) for choice in (parameter.default, *parameter.choices))
    }
    for factor_index, factor in enumerate(spec.factors.factors):
        base = f"factors.factors.{factor_index}"
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
                code_aliases.get(factor_issue.code, factor_issue.code),
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
