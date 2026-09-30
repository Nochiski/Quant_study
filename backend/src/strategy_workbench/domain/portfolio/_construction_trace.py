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
class PortfolioFrameSummary:
    """trace 한 프레임 전체의 선정 깔때기 수(기준일 미리보기, lang2 P4-03).

    유니버스 멤버만 센다. 한 종목이 사유를 여럿 가질 수 있어(규칙 탈락이면서 팩터 값 없음) 사유별
    수를 더해도 `universe` 가 되지 않으므로, 순위에 든 수(`eligible`)를 따로 싣는다. 소비자는 수를
    다시 세지 않는다.
    """

    universe: int
    eligible: int
    eligibility_failed: int
    eligibility_rank_cut: int
    # 값이 없어 뺀 종목(거르기 필드·팩터 값 없음)과 그중 원장이 가린 입력 때문인 종목(#350)
    missing: int
    masked: int


@dataclass(frozen=True)
class PortfolioConstructionTrace:
    signal_as_of: date
    execution_on: date
    candidates: tuple[PortfolioCandidateTrace, ...]
    summary: PortfolioFrameSummary


@dataclass(frozen=True)
class PortfolioTraceSelection:
    """Bound the optional audit without changing the compiler's executable scope.

    `security_ids` 가 비면 종목별 감사 없이 프레임 요약만 만든다(기준일 미리보기, lang2 P4-03).
    """

    # None resolves through PortfolioRebalanceSchedule, never through calendar arithmetic here.
    as_of: date | None
    security_ids: tuple[str, ...]
    include_order_delta: bool = False

    def __post_init__(self) -> None:
        if any(not item.strip() for item in self.security_ids):
            raise ValueError("portfolio trace selection must not contain blank security ids")
        if len(self.security_ids) != len(set(self.security_ids)):
            raise ValueError("portfolio trace selection security ids must be unique")


@dataclass(frozen=True)
class TargetTapeTraceResult:
    """Exact executable tape plus an out-of-band audit for one selected frame."""

    tape: TargetTape
    trace: PortfolioConstructionTrace | None
