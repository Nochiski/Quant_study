"""Strategy scalar constraint catalog: the single owner of per-field bounds and contract metadata.

WORKFLOW P1-04. The semantic validator (`_validation.py`) consumes these declarations to emit
scalar issues, and the runtime schema/contract API (P1-05) reads the same declarations to expose
range, unit, stage and description metadata. Defaults are not repeated here: they live on the
dataclass fields and are read through `dataclasses.fields`.

Cross-field and graph rules (exposure relations, required companion fields, parameter bounds,
factor graph validity) stay in the validator and are listed in `SEMANTIC_ONLY_CODES` so a test can
prove every validation code has exactly one owner.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import get_type_hints

from ._models import StrategySpec


class ContractUnit(StrEnum):
    RATIO = "ratio"
    COUNT = "count"
    SESSIONS = "sessions"
    BASIS_POINTS = "bps"
    # Compared against a referenced dataset field, so the unit is that field's unit.
    FIELD = "field"


class AppliedStage(StrEnum):
    # `DATA`·`EXECUTION` 은 실행 설정(`domain/backtest`)이 자기 제약 행에 붙이는 단계다.
    # 전략 문서에는 1.2 부터 해당 행이 없다.
    DATA = "data"
    ELIGIBILITY = "eligibility"
    SIGNAL = "signal"
    PORTFOLIO = "portfolio"
    RISK = "risk"
    EXECUTION = "execution"


@dataclass(frozen=True)
class ScalarConstraint:
    """Bounds and contract metadata for one numeric authoring field.

    `pointer` is the JSON Pointer inside the authoring document (identity-free canonical shape).
    `code` is the validation issue code the validator emits when the bound is violated.
    """

    pointer: str
    code: str
    stage: AppliedStage
    unit: ContractUnit
    message: str
    minimum: float | None = None
    maximum: float | None = None
    exclusive_minimum: bool = False
    exclusive_maximum: bool = False
    display_unit: str | None = None
    example: float | int | None = None
    description_key: str = ""

    @property
    def path(self) -> str:
        """Dotted path used by `StrategyValidation.issues[].path` (wire compatibility)."""
        return self.pointer.strip("/").replace("/", ".")

    def satisfied_by(self, value: float | int) -> bool:
        if isinstance(value, float) and not math.isfinite(value):
            return False  # NaN and infinities satisfy no contract, even a one-sided bound.
        if self.minimum is not None:
            if self.exclusive_minimum and not value > self.minimum:
                return False
            if not self.exclusive_minimum and not value >= self.minimum:
                return False
        if self.maximum is not None:
            if self.exclusive_maximum and not value < self.maximum:
                return False
            if not self.exclusive_maximum and not value <= self.maximum:
                return False
        return True


@dataclass(frozen=True)
class ApplicabilityCondition:
    """When a field is read by the pipeline: another field `equals` a value, or is `not_null`.

    The condition is data so the runtime schema can publish it (`x-applicable-when`) and the
    validator can evaluate it from the same row; neither restates the rule.
    """

    pointer: str
    equals: str | None = None
    not_null: bool = False

    def __post_init__(self) -> None:
        if (self.equals is None) == (not self.not_null):
            raise ValueError(
                "applicability condition must be exactly one of equals/not_null — "
                f"pointer={self.pointer!r} equals={self.equals!r} not_null={self.not_null}"
            )
        if self.equals is not None and not isinstance(self.equals, str):
            # 프론트는 `x-applicable-when.equals`를 JSON 값 그대로 비교한다. 문자열(enum 값)만
            # 허용해야 Python 쪽 `str(value)` 비교와 어긋나지 않는다.
            raise TypeError(
                "applicability `equals` must be the enum's string value — "
                f"pointer={self.pointer!r} equals={self.equals!r}"
            )

    @property
    def path(self) -> str:
        return self.pointer.strip("/").replace("/", ".")

    def describe(self) -> str:
        return f"{self.path} = {self.equals}" if self.equals is not None else f"{self.path} 설정"

    def holds_for(self, spec: StrategySpec) -> bool:
        value = resolve_scalar(spec, self.pointer)
        if self.not_null:
            return value is not None
        return value is not None and str(getattr(value, "value", value)) == self.equals


@dataclass(frozen=True)
class FieldApplicability:
    """A flat field that the pipeline reads only in one mode (spec D4).

    Writing it in another mode is not an error — the flat 1.1 shape keeps defaults for every
    field — but the value has no effect, so compile reports a warning when the document sets it
    explicitly.
    """

    pointer: str
    # 모두 성립해야 읽힌다(AND). 예: short_selection_count는 long_short이면서 top_n일 때만.
    conditions: tuple[ApplicabilityCondition, ...]
    description_key: str
    # 이미 blocking error 규칙이 같은 관계를 소유하는 행: 스키마에는 조건을 노출하되 validator는
    # 그 error 하나만 낸다(같은 사실을 warning으로 두 번 보고하지 않는다).
    owned_by_error: str | None = None

    @property
    def path(self) -> str:
        return self.pointer.strip("/").replace("/", ".")

    def __post_init__(self) -> None:
        if not self.conditions:
            raise ValueError(f"field applicability needs a condition — pointer={self.pointer!r}")

    def applies_to(self, spec: StrategySpec) -> bool:
        return all(condition.holds_for(spec) for condition in self.conditions)


FIELD_APPLICABILITY: tuple[FieldApplicability, ...] = (
    # `_compiler.py::_selection_counts`: top_n이면 selection_count(+long_short이면
    # short_selection_count),
    # percentile이면 selection_percentile로 양쪽 count를 계산한다.
    FieldApplicability(
        "/portfolio/selection_count",
        (ApplicabilityCondition("/portfolio/selection_method", equals="top_n"),),
        "strategy.contract.applicable.selection_count",
    ),
    FieldApplicability(
        "/portfolio/short_selection_count",
        (
            ApplicabilityCondition("/portfolio/side", equals="long_short"),
            ApplicabilityCondition("/portfolio/selection_method", equals="top_n"),
        ),
        "strategy.contract.applicable.short_selection_count",
    ),
    FieldApplicability(
        "/portfolio/selection_percentile",
        (ApplicabilityCondition("/portfolio/selection_method", equals="percentile"),),
        "strategy.contract.applicable.selection_percentile",
    ),
    FieldApplicability(
        "/portfolio/rebalance_every_n_sessions",
        (ApplicabilityCondition("/portfolio/rebalance", equals="every_n_sessions"),),
        "strategy.contract.applicable.rebalance_every_n_sessions",
    ),
    FieldApplicability(
        "/portfolio/minimum_liquidity",
        (ApplicabilityCondition("/portfolio/liquidity_field_id", not_null=True),),
        "strategy.contract.applicable.minimum_liquidity",
        owned_by_error="strategy.portfolio.liquidity_field",
    ),
    FieldApplicability(
        "/risk/sector_neutral",
        (ApplicabilityCondition("/portfolio/side", equals="long_short"),),
        "strategy.contract.applicable.sector_neutral",
        owned_by_error="strategy.risk.sector_neutral_side",
    ),
    FieldApplicability(
        "/risk/risk_field_id",
        (ApplicabilityCondition("/portfolio/weighting", equals="risk"),),
        "strategy.contract.applicable.risk_field_id",
    ),
    FieldApplicability(
        "/signal/regime_minimum",
        (ApplicabilityCondition("/signal/regime_field_id", not_null=True),),
        "strategy.contract.applicable.regime_minimum",
        owned_by_error="strategy.signal.regime_field",
    ),
)


STRATEGY_SCALAR_CONSTRAINTS: tuple[ScalarConstraint, ...] = (
    ScalarConstraint(
        pointer="/portfolio/selection_count",
        code="strategy.portfolio.selection_count",
        stage=AppliedStage.PORTFOLIO,
        unit=ContractUnit.COUNT,
        minimum=1,
        example=20,
        description_key="strategy.contract.portfolio.selection_count",
        message="선택 종목 수는 1 이상이어야 합니다.",
    ),
    ScalarConstraint(
        pointer="/portfolio/short_selection_count",
        code="strategy.portfolio.short_selection_count",
        stage=AppliedStage.PORTFOLIO,
        unit=ContractUnit.COUNT,
        minimum=1,
        example=20,
        description_key="strategy.contract.portfolio.short_selection_count",
        message="숏 선택 종목 수는 1 이상이어야 합니다.",
    ),
    ScalarConstraint(
        pointer="/portfolio/selection_percentile",
        code="strategy.portfolio.selection_percentile",
        stage=AppliedStage.PORTFOLIO,
        unit=ContractUnit.RATIO,
        display_unit="%",
        minimum=0.0,
        exclusive_minimum=True,
        maximum=0.5,
        example=0.1,
        description_key="strategy.contract.portfolio.selection_percentile",
        message="선택 분위수는 0보다 크고 0.5 이하여야 합니다.",
    ),
    ScalarConstraint(
        pointer="/portfolio/rebalance_every_n_sessions",
        code="strategy.portfolio.rebalance_every_n_sessions",
        stage=AppliedStage.PORTFOLIO,
        unit=ContractUnit.SESSIONS,
        minimum=1,
        example=21,
        description_key="strategy.contract.portfolio.rebalance_every_n_sessions",
        message="리밸런싱 세션 간격은 1 이상이어야 합니다.",
    ),
    ScalarConstraint(
        pointer="/portfolio/turnover_buffer_count",
        code="strategy.portfolio.turnover_buffer_count",
        stage=AppliedStage.PORTFOLIO,
        unit=ContractUnit.COUNT,
        minimum=0,
        example=0,
        description_key="strategy.contract.portfolio.turnover_buffer_count",
        message="회전율 버퍼는 음수일 수 없습니다.",
    ),
    ScalarConstraint(
        pointer="/portfolio/minimum_trade_weight",
        code="strategy.portfolio.minimum_trade_weight",
        stage=AppliedStage.PORTFOLIO,
        unit=ContractUnit.RATIO,
        display_unit="%",
        minimum=0.0,
        maximum=1.0,
        example=0.0,
        description_key="strategy.contract.portfolio.minimum_trade_weight",
        message="최소 거래 비중은 0 이상 1 이하여야 합니다.",
    ),
    ScalarConstraint(
        pointer="/portfolio/minimum_liquidity",
        code="strategy.portfolio.minimum_liquidity",
        # Applied while filtering candidates (eligibility), in the liquidity field's unit.
        stage=AppliedStage.ELIGIBILITY,
        unit=ContractUnit.FIELD,
        minimum=0.0,
        description_key="strategy.contract.portfolio.minimum_liquidity",
        message="최소 유동성은 음수일 수 없습니다.",
    ),
    ScalarConstraint(
        pointer="/risk/gross_exposure",
        code="strategy.risk.gross_exposure",
        stage=AppliedStage.RISK,
        unit=ContractUnit.RATIO,
        display_unit="%",
        minimum=0.0,
        exclusive_minimum=True,
        example=1.0,
        description_key="strategy.contract.risk.gross_exposure",
        message="총 익스포저는 0보다 커야 합니다.",
    ),
    ScalarConstraint(
        pointer="/risk/max_name_weight",
        code="strategy.risk.max_name_weight",
        stage=AppliedStage.RISK,
        unit=ContractUnit.RATIO,
        display_unit="%",
        minimum=0.0,
        exclusive_minimum=True,
        maximum=1.0,
        example=0.05,
        description_key="strategy.contract.risk.max_name_weight",
        message="종목 한도는 0보다 크고 1 이하여야 합니다.",
    ),
    ScalarConstraint(
        pointer="/risk/max_sector_weight",
        code="strategy.risk.max_sector_weight",
        stage=AppliedStage.RISK,
        unit=ContractUnit.RATIO,
        display_unit="%",
        minimum=0.0,
        exclusive_minimum=True,
        maximum=1.0,
        example=0.25,
        description_key="strategy.contract.risk.max_sector_weight",
        message="섹터 한도는 0보다 크고 1 이하여야 합니다.",
    ),
)

# Validation codes that are cross-field, graph or parameter rules: owned by the validator only.
SEMANTIC_ONLY_CODES: frozenset[str] = frozenset(
    {
        "strategy.schema_version.unsupported",
        "strategy.field.inapplicable",
        "strategy.title.empty",
        "strategy.factor.required",
        "strategy.factor.duplicate",
        "strategy.portfolio.liquidity_field",
        "strategy.risk.net_exposure",
        "strategy.risk.long_only_exposure",
        "strategy.risk.risk_field",
        "strategy.risk.sector_neutral_side",
        "strategy.signal.regime_field",
        "strategy.parameter.duplicate",
        "strategy.parameter.bounds",
        "strategy.parameter.default",
        "strategy.parameter.step",
        "strategy.parameter.choice",
        "strategy.number.non_finite",
    }
)


# Codes for FactorGraph expression issues. Two producers share them: the strategy validator
# (aliasing `domain.factor` graph issues) and the portfolio pipeline, which rejects a graph it
# cannot evaluate yet. They are listed apart from SEMANTIC_ONLY_CODES because they are not
# "owned by the validator only" — the registry, not a call site, is what owns them.
EXPRESSION_CODES: frozenset[str] = frozenset(
    {
        "strategy.expression.duplicate_node",
        "strategy.expression.output_missing",
        "strategy.expression.input_missing",
        "strategy.expression.parameter_missing",
        "strategy.expression.lag_periods",
        "strategy.expression.parameter_type",
        "strategy.expression.reference_unsupported",
        "strategy.expression.output_type",
        "strategy.expression.calculation_non_finite",
    }
)


def field_applicability_index() -> Mapping[str, FieldApplicability]:
    return {row.pointer: row for row in FIELD_APPLICABILITY}


def scalar_constraint_index() -> Mapping[str, ScalarConstraint]:
    return {constraint.pointer: constraint for constraint in STRATEGY_SCALAR_CONSTRAINTS}


def field_default(pointer: str) -> object:
    """Dataclass default for an authoring pointer, read from the model (never restated here)."""
    segments = pointer.strip("/").split("/")
    current: type = StrategySpec
    for segment in segments[:-1]:
        hints = get_type_hints(current)
        current = hints[segment]
    for field in dataclasses.fields(current):
        if field.name == segments[-1]:
            if field.default is not dataclasses.MISSING:
                return field.default
            if field.default_factory is not dataclasses.MISSING:
                return field.default_factory()
            # A required field has no default: never publish None as if it were one.
            raise KeyError(f"required field has no default — pointer={pointer!r}")
    raise KeyError(f"unknown authoring pointer — pointer={pointer!r}")


def resolve_scalar(spec: StrategySpec, pointer: str) -> object:
    """Read the value at a scalar constraint pointer from a typed spec."""
    current: object = spec
    for segment in pointer.strip("/").split("/"):
        current = getattr(current, segment)
    return current
