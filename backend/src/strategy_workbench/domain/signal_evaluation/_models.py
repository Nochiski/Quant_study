from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from math import isfinite


@dataclass(frozen=True)
class PriceLabelSpec:
    """공통 거래 달력 기준의 배당·비용 제외 조정종가 수익률 명세.

    진입은 신호일에서 entry_delay_sessions 뒤의 종가, 만기는 진입에서 horizon 뒤다.
    실제 다음 시가 매매 수익률과 별개이며 5/21/63은 후보 결과 선택 규칙이 아니다.
    """

    horizons_sessions: tuple[int, ...] = (5, 21, 63)
    entry_delay_sessions: int = 0
    minimum_pairs: int = 3

    def __post_init__(self) -> None:
        if (
            not self.horizons_sessions
            or any(type(value) is not int or value <= 0 for value in self.horizons_sessions)
            or len(set(self.horizons_sessions)) != len(self.horizons_sessions)
        ):
            raise ValueError("horizons_sessions must contain unique positive integers")
        if type(self.entry_delay_sessions) is not int or self.entry_delay_sessions < 0:
            raise ValueError("entry_delay_sessions must be a nonnegative integer")
        if type(self.minimum_pairs) is not int or self.minimum_pairs < 2:
            raise ValueError("minimum_pairs must be an integer >= 2")


@dataclass(frozen=True)
class AdjustedCloseObservation:
    """분할을 반영하고 현금배당을 제외한 종가. 원주가를 이 타입에 넣으면 안 된다."""

    session: date
    security_id: str
    close: float
    available_date: date

    def __post_init__(self) -> None:
        if not self.security_id or not isfinite(self.close) or self.close <= 0:
            raise ValueError("adjusted close requires a security id and a finite positive price")
        if self.available_date < self.session:
            raise ValueError("price cannot be available before its session")


@dataclass(frozen=True)
class ListingInterval:
    security_id: str
    first_session: date
    last_session: date

    def __post_init__(self) -> None:
        if not self.security_id or self.first_session > self.last_session:
            raise ValueError("listing interval requires an id and ordered sessions")


class PriceLabelStatus(StrEnum):
    OK = "ok"
    CALENDAR_INSUFFICIENT = "calendar_insufficient"
    NOT_MATURED = "not_matured"
    LISTING_STATUS_UNKNOWN = "listing_status_unknown"
    LISTING_ENDED_UNSETTLED = "listing_ended_unsettled"
    OUTSIDE_LISTING = "outside_listing"
    MISSING_ENTRY = "missing_entry"
    MISSING_EXIT = "missing_exit"
    PRICE_NOT_AVAILABLE = "price_not_available"


@dataclass(frozen=True)
class PriceLabel:
    entry_session: date | None
    label_end: date | None
    status: PriceLabelStatus
    return_fraction: float | None = None


class SignalStatus(StrEnum):
    OK = "ok"
    MISSING = "signal_missing"
    NOT_AVAILABLE = "signal_not_available"


@dataclass(frozen=True)
class SignalEvaluationSample:
    signal_session: date
    security_id: str
    horizon_sessions: int
    signal_value: float | None
    signal_status: SignalStatus
    label: PriceLabel

    @property
    def paired(self) -> bool:
        return self.signal_status is SignalStatus.OK and self.label.status is PriceLabelStatus.OK


class RankICStatus(StrEnum):
    OK = "ok"
    INSUFFICIENT_PAIRS = "insufficient_pairs"
    CONSTANT_SIGNAL = "constant_signal"
    CONSTANT_LABEL = "constant_label"


@dataclass(frozen=True)
class ExclusionCount:
    reason: str
    count: int


@dataclass(frozen=True)
class DailyRankIC:
    signal_session: date
    horizon_sessions: int
    status: RankICStatus
    rank_ic: float | None
    universe_count: int
    paired_count: int
    coverage_fraction: float
    exclusions: tuple[ExclusionCount, ...]
    signal_status_counts: tuple[ExclusionCount, ...]
    label_status_counts: tuple[ExclusionCount, ...]


@dataclass(frozen=True)
class SignalEvaluationResult:
    data_snapshot_id: str
    factor_id: str
    evaluation_as_of: date
    label_spec: PriceLabelSpec
    samples: tuple[SignalEvaluationSample, ...]
    daily: tuple[DailyRankIC, ...]
