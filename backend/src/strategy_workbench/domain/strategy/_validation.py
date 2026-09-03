from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ._models import (
    BinaryNode,
    ChoiceParameter,
    FloatParameter,
    IntegerParameter,
    ParameterNode,
    StrategySpec,
    UnaryNode,
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


def _issue(code: str, path: str, message: str) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        path=path,
        message=message,
        kind=ValidationKind.SEMANTIC,
    )


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
    if not 0 < spec.signal.entry_percentile <= 1:
        issues.append(
            _issue(
                "strategy.signal.percentile",
                "signal.entry_percentile",
                "선택 비율은 0보다 크고 1 이하여야 합니다.",
            )
        )
    if spec.portfolio.selection_count <= 0:
        issues.append(
            _issue(
                "strategy.portfolio.selection_count",
                "portfolio.selection_count",
                "선택 종목 수는 1 이상이어야 합니다.",
            )
        )
    if spec.risk.gross_exposure <= 0:
        issues.append(
            _issue(
                "strategy.risk.gross_exposure",
                "risk.gross_exposure",
                "총 익스포저는 0보다 커야 합니다.",
            )
        )
    if not 0 < spec.risk.max_name_weight <= 1:
        issues.append(
            _issue(
                "strategy.risk.max_name_weight",
                "risk.max_name_weight",
                "종목 한도는 0보다 크고 1 이하여야 합니다.",
            )
        )
    if not 0 < spec.execution.participation_rate <= 1:
        issues.append(
            _issue(
                "strategy.execution.participation",
                "execution.participation_rate",
                "참여율은 0보다 크고 1 이하여야 합니다.",
            )
        )
    if spec.execution.fee_bps < 0 or spec.execution.slippage_bps < 0:
        issues.append(
            _issue("strategy.execution.cost", "execution", "거래 비용은 음수일 수 없습니다.")
        )

    parameter_ids = [parameter.parameter_id for parameter in spec.parameters]
    if len(parameter_ids) != len(set(parameter_ids)):
        issues.append(
            _issue(
                "strategy.parameter.duplicate", "parameters", "파라미터 ID는 중복될 수 없습니다."
            )
        )
    known_parameters = set(parameter_ids)
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
    for factor_index, factor in enumerate(spec.factors.factors):
        base = f"factors.factors.{factor_index}"
        nodes = {node.node_id: node for node in factor.graph.nodes}
        if len(nodes) != len(factor.graph.nodes):
            issues.append(
                _issue(
                    "strategy.expression.duplicate_node",
                    f"{base}.graph.nodes",
                    "노드 ID는 중복될 수 없습니다.",
                )
            )
        if factor.graph.output_node_id not in nodes:
            issues.append(
                _issue(
                    "strategy.expression.output_missing",
                    f"{base}.graph.output_node_id",
                    "출력 노드를 찾을 수 없습니다.",
                )
            )
        for node_index, node in enumerate(factor.graph.nodes):
            node_path = f"{base}.graph.nodes.{node_index}"
            references: tuple[str, ...] = ()
            if isinstance(node, UnaryNode):
                references = (node.input_node_id,)
                if node.operator.value == "lag" and (node.periods is None or node.periods <= 0):
                    issues.append(
                        _issue(
                            "strategy.expression.lag_periods",
                            node_path,
                            "lag 기간은 1 이상이어야 합니다.",
                        )
                    )
            elif isinstance(node, BinaryNode):
                references = (node.left_node_id, node.right_node_id)
            elif isinstance(node, ParameterNode) and node.parameter_id not in known_parameters:
                issues.append(
                    _issue(
                        "strategy.expression.parameter_missing",
                        node_path,
                        "참조한 파라미터를 찾을 수 없습니다.",
                    )
                )
            for reference in references:
                if reference not in nodes:
                    issues.append(
                        _issue(
                            "strategy.expression.input_missing",
                            node_path,
                            f"입력 노드 {reference!r}를 찾을 수 없습니다.",
                        )
                    )

    return StrategyValidation(
        valid=not any(issue.severity is ValidationSeverity.ERROR for issue in issues),
        issues=tuple(issues),
    )
