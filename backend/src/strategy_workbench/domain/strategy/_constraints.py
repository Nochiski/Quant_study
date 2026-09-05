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


STRATEGY_SCALAR_CONSTRAINTS: tuple[ScalarConstraint, ...] = (
    ScalarConstraint(
        pointer="/signal/entry_percentile",
        code="strategy.signal.percentile",
        stage=AppliedStage.SIGNAL,
        unit=ContractUnit.RATIO,
        display_unit="%",
        minimum=0.0,
        exclusive_minimum=True,
        maximum=1.0,
        example=0.1,
        description_key="strategy.contract.signal.entry_percentile",
        message="선택 비율은 0보다 크고 1 이하여야 합니다.",
    ),
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
    ScalarConstraint(
        pointer="/execution/participation_rate",
        code="strategy.execution.participation",
        stage=AppliedStage.EXECUTION,
        unit=ContractUnit.RATIO,
        display_unit="%",
        minimum=0.0,
        exclusive_minimum=True,
        maximum=1.0,
        example=0.1,
        description_key="strategy.contract.execution.participation_rate",
        message="참여율은 0보다 크고 1 이하여야 합니다.",
    ),
    ScalarConstraint(
        pointer="/execution/fee_bps",
        code="strategy.execution.cost",
        stage=AppliedStage.EXECUTION,
        unit=ContractUnit.BASIS_POINTS,
        display_unit="bp",
        minimum=0.0,
        example=15.0,
        description_key="strategy.contract.execution.fee_bps",
        message="수수료는 0 이상의 숫자여야 합니다.",
    ),
    ScalarConstraint(
        pointer="/execution/slippage_bps",
        code="strategy.execution.cost",
        stage=AppliedStage.EXECUTION,
        unit=ContractUnit.BASIS_POINTS,
        display_unit="bp",
        minimum=0.0,
        example=10.0,
        description_key="strategy.contract.execution.slippage_bps",
        message="슬리피지는 0 이상의 숫자여야 합니다.",
    ),
)

# Validation codes that are cross-field, graph or parameter rules: owned by the validator only.
SEMANTIC_ONLY_CODES: frozenset[str] = frozenset(
    {
        "strategy.title.empty",
        "strategy.data.date_order",
        "strategy.data.universe_empty",
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
