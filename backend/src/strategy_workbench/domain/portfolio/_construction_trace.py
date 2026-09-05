"""Immutable audit projection emitted by the portfolio compiler.

The objects in this module explain the arithmetic that already creates ``TargetTape``. They are
not a second portfolio calculator and deliberately stay outside the tape's canonical payload, so
turning tracing on cannot change a backtest input or its hash.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from strategy_workbench.domain.strategy.facade.specification import FactorDirection

from ._models import CandidateSide, ExclusionReason, TargetTape


class FactorContributionStatus(StrEnum):
    OK = "ok"
    MISSING = "missing"
    FUTURE_DATA = "future_data"


class PortfolioConstraintEffect(StrEnum):
    NOT_SELECTED = "not_selected"
    UNCHANGED = "unchanged"
    ADJUSTED = "adjusted"
    REMOVED = "removed"


@dataclass(frozen=True)
class FactorContributionTrace:
    """One term of the compiler-owned normalized weighted-sum score."""

    factor_id: str
    value: float | None
    configured_weight: float
    direction: FactorDirection
    weighted_value: float | None
    normalized_contribution: float | None
    status: FactorContributionStatus


@dataclass(frozen=True)
class PortfolioCandidateTrace:
    """The linked score -> selection -> constrained target path for one security."""

    as_of: date
    security_id: str
    factor_contributions: tuple[FactorContributionTrace, ...]
    composite_score: float | None
    rank: int | None
    eligible: bool
    selected: bool
    side: CandidateSide | None
    unconstrained_target_weight: float | None
    constrained_target_weight: float
    previous_weight: float | None
    estimated_order_delta: float | None
    constraint_effect: PortfolioConstraintEffect
    exclusion_reasons: tuple[ExclusionReason, ...]


@dataclass(frozen=True)
class PortfolioConstructionTrace:
    signal_as_of: date
    execution_on: date
    candidates: tuple[PortfolioCandidateTrace, ...]


@dataclass(frozen=True)
class PortfolioTraceSelection:
    """Bound the optional audit without changing the compiler's executable scope."""

    as_of: date
    security_ids: tuple[str, ...]
    include_order_delta: bool = False

    def __post_init__(self) -> None:
        if not self.security_ids:
            raise ValueError("portfolio trace selection requires at least one security id")
        if any(not item.strip() for item in self.security_ids):
            raise ValueError("portfolio trace selection must not contain blank security ids")
        if len(self.security_ids) != len(set(self.security_ids)):
            raise ValueError("portfolio trace selection security ids must be unique")


@dataclass(frozen=True)
class TargetTapeTraceResult:
    """Exact executable tape plus an out-of-band audit for one selected frame."""

    tape: TargetTape
    trace: PortfolioConstructionTrace | None
