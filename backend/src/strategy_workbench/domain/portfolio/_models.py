from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class CandidateSide(StrEnum):
    LONG = "long"
    SHORT = "short"


class ExclusionReason(StrEnum):
    NOT_IN_UNIVERSE = "not_in_universe"
    FUTURE_DATA = "future_data"
    MISSING_ELIGIBILITY = "missing_eligibility"
    ELIGIBILITY_FAILED = "eligibility_failed"
    MISSING_FACTOR = "missing_factor"
    SCORE_THRESHOLD = "score_threshold"
    REGIME_BLOCKED = "regime_blocked"
    LIQUIDITY_FAILED = "liquidity_failed"
    OUTSIDE_SELECTION = "outside_selection"
    MISSING_RISK = "missing_risk"
    TURNOVER_BUFFER = "turnover_buffer"
    MINIMUM_TRADE = "minimum_trade"


PortfolioInputValue = float | str | bool | None


@dataclass(frozen=True)
class PortfolioFieldValue:
    field_id: str
    value: PortfolioInputValue
    available_date: date


@dataclass(frozen=True)
class PortfolioFactorValue:
    factor_id: str
    value: float | None
    available_date: date


@dataclass(frozen=True)
class PortfolioObservation:
    as_of: date
    security_id: str
    universe_member: bool
    factor_values: tuple[PortfolioFactorValue, ...]
    fields: tuple[PortfolioFieldValue, ...] = ()
    sector_id: str | None = None
    previous_weight: float = 0.0


@dataclass(frozen=True)
class CandidateDecision:
    as_of: date
    security_id: str
    eligible: bool
    selected: bool
    composite_score: float | None
    rank: int | None
    side: CandidateSide | None
    target_weight: float
    sector_id: str | None
    exclusion_reasons: tuple[ExclusionReason, ...]


@dataclass(frozen=True)
class TargetPosition:
    security_id: str
    weight: float
    composite_score: float
    rank: int
    side: CandidateSide


@dataclass(frozen=True)
class TargetFrame:
    signal_as_of: date
    execution_on: date
    targets: tuple[TargetPosition, ...]
    candidates: tuple[CandidateDecision, ...]


@dataclass(frozen=True)
class TargetTape:
    data_snapshot_id: str
    strategy_hash: str
    tape_hash: str
    frames: tuple[TargetFrame, ...]
    execution_timing: str = "next_open"
