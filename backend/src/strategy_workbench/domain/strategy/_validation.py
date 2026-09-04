from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from strategy_workbench.domain.factor.facade.validation import (
    FactorValidationSeverity,
    validate_factor_graph,
)

from ._constraints import SEMANTIC_ONLY_CODES, STRATEGY_SCALAR_CONSTRAINTS, resolve_scalar
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


@dataclass(frozen=True)
class StrategyValidation:
    valid: bool
    issues: tuple[ValidationIssue, ...]


_CATALOG_CODES = frozenset(constraint.code for constraint in STRATEGY_SCALAR_CONSTRAINTS)


def _issue(code: str, path: str, message: str) -> ValidationIssue:
    if code not in SEMANTIC_ONLY_CODES and code not in _CATALOG_CODES:
        raise ValueError(
            "validation code has no owner — add it to SEMANTIC_ONLY_CODES or the scalar catalog: "
            f"code={code!r}"
        )
    return ValidationIssue(code=code, path=path, message=message, kind=ValidationKind.SEMANTIC)


def validate_strategy(spec: StrategySpec) -> StrategyValidation:
    issues: list[ValidationIssue] = []
    if not spec.title.strip():
        issues.append(_issue("strategy.title.empty", "title", "전략 이름을 입력하세요."))
    if spec.data.start > spec.data.end:
        issues.append(
            _issue("strategy.data.date_order", "data.end", "종료일은 시작일 이후여야 합니다.")
        )
    if not spec.data.universe_id.strip():
        issues.append(
            _issue("strategy.data.universe_empty", "data.universe_id", "유니버스를 선택하세요.")
        )
    if not spec.factors.factors:
        issues.append(_issue("strategy.factor.required", "factors", "팩터를 하나 이상 추가하세요."))
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
            issues.append(_issue(constraint.code, constraint.path, constraint.message))
    if spec.portfolio.minimum_liquidity is not None and spec.portfolio.liquidity_field_id is None:
        issues.append(
            _issue(
                "strategy.portfolio.liquidity_field",
                "portfolio.liquidity_field_id",
                "최소 유동성을 쓰려면 유동성 필드를 지정해야 합니다.",
            )
        )
    if abs(spec.risk.net_exposure) > spec.risk.gross_exposure:
        issues.append(
            _issue(
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
            _issue(
                "strategy.risk.long_only_exposure",
                "risk.net_exposure",
                "롱온리 전략은 총 익스포저와 순 익스포저가 같아야 합니다.",
            )
        )
    if spec.portfolio.weighting is WeightingMethod.RISK and spec.risk.risk_field_id is None:
        issues.append(
            _issue(
                "strategy.risk.risk_field",
                "risk.risk_field_id",
                "리스크 가중 방식을 쓰려면 리스크 필드를 지정해야 합니다.",
            )
        )
    if spec.risk.sector_neutral and spec.portfolio.side is PortfolioSide.LONG_ONLY:
        issues.append(
            _issue(
                "strategy.risk.sector_neutral_side",
                "risk.sector_neutral",
                "섹터 중립화는 롱숏 전략에서만 사용할 수 있습니다.",
            )
        )
    if spec.signal.regime_field_id is None and spec.signal.regime_minimum is not None:
        issues.append(
            _issue(
                "strategy.signal.regime_field",
                "signal.regime_field_id",
                "레짐 기준값을 쓰려면 레짐 필드를 지정해야 합니다.",
            )
        )

    parameter_ids = [parameter.parameter_id for parameter in spec.parameters]
    if len(parameter_ids) != len(set(parameter_ids)):
        issues.append(
            _issue(
                "strategy.parameter.duplicate", "parameters", "파라미터 ID는 중복될 수 없습니다."
            )
        )
    for index, parameter in enumerate(spec.parameters):
        path = f"parameters.{index}"
        if isinstance(parameter, (FloatParameter, IntegerParameter)):
            if parameter.minimum > parameter.maximum:
                issues.append(
                    _issue("strategy.parameter.bounds", path, "최솟값은 최댓값 이하여야 합니다.")
                )
            if not parameter.minimum <= parameter.default <= parameter.maximum:
                issues.append(
                    _issue(
                        "strategy.parameter.default", path, "기본값은 탐색 범위 안에 있어야 합니다."
                    )
                )
            if parameter.step is not None and parameter.step <= 0:
                issues.append(
                    _issue("strategy.parameter.step", path, "탐색 간격은 0보다 커야 합니다.")
                )
        elif isinstance(parameter, ChoiceParameter):
            if not parameter.choices or parameter.default not in parameter.choices:
                issues.append(
                    _issue(
                        "strategy.parameter.choice",
                        path,
                        "기본값은 비어 있지 않은 선택지에 포함되어야 합니다.",
                    )
                )

    factor_ids = [factor.factor_id for factor in spec.factors.factors]
    if len(factor_ids) != len(set(factor_ids)):
        issues.append(
            _issue("strategy.factor.duplicate", "factors", "팩터 ID는 중복될 수 없습니다.")
        )
    code_aliases = {
        "factor.graph.duplicate_node": "strategy.expression.duplicate_node",
        "factor.graph.output_missing": "strategy.expression.output_missing",
        "factor.graph.input_missing": "strategy.expression.input_missing",
        "factor.graph.parameter_missing": "strategy.expression.parameter_missing",
        "factor.graph.lag_periods": "strategy.expression.lag_periods",
    }
    for factor_index, factor in enumerate(spec.factors.factors):
        base = f"factors.factors.{factor_index}"
        validation = validate_factor_graph(
            factor.graph,
            parameter_ids=tuple(parameter_ids),
            factor_ids=tuple(factor_ids),
        )
        issues.extend(
            ValidationIssue(
                code=code_aliases.get(factor_issue.code, factor_issue.code),
                path=f"{base}.graph.{factor_issue.path}",
                message=factor_issue.message,
                kind=ValidationKind.SEMANTIC,
                severity=(
                    ValidationSeverity.ERROR
                    if factor_issue.severity is FactorValidationSeverity.ERROR
                    else ValidationSeverity.WARNING
                ),
            )
            for factor_issue in validation.issues
        )

    return StrategyValidation(
        valid=not any(issue.severity is ValidationSeverity.ERROR for issue in issues),
        issues=tuple(issues),
    )
