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
    # 값이 규칙을 어긴 것이 아니라 횡단면 순위에서 잘렸다(`top_percent`·`top_count`, spec D3 S5).
    # `ELIGIBILITY_FAILED` 와 한 값을 쓰면 "상위 20%에 못 들었다"가 "조건을 어겼다"로 읽힌다.
    ELIGIBILITY_RANK_CUT = "eligibility_rank_cut"
    MISSING_FACTOR = "missing_factor"
    SCORE_THRESHOLD = "score_threshold"
    REGIME_BLOCKED = "regime_blocked"
    LIQUIDITY_FAILED = "liquidity_failed"
    OUTSIDE_SELECTION = "outside_selection"
    MISSING_RISK = "missing_risk"
    TURNOVER_BUFFER = "turnover_buffer"
    MINIMUM_TRADE = "minimum_trade"


class PortfolioWarningCode(StrEnum):
    """tape 를 막지는 않지만 결과 해석을 바꾸는 컴파일러 경고의 코드.

    값은 실행 결과 매니페스트의 `DataWarning.code` 로 그대로 나간다.
    """

    # 섹터를 모르는 종목(`sector_id is None`)을 섹터 상한·섹터 중립 계산에서 뺐다(이슈 #203).
    SECTOR_UNKNOWN_EXCLUDED = "portfolio.sector_unknown_excluded"


@dataclass(frozen=True)
class PortfolioWarning:
    """컴파일러가 tape 를 만들며 알린 경고 한 건. `message` 는 한글로 완성된 진단 문장이다."""

    code: PortfolioWarningCode
    message: str


PortfolioInputValue = float | str | bool | None


@dataclass(frozen=True)
class PortfolioFieldValue:
    field_id: str
    value: PortfolioInputValue
    available_date: date
    # 원장이 가린 셀이라 값이 없다(#350). 결측 탈락 중 원장이 가린 몫을 기준일 요약이 센다.
    masked: bool = False


@dataclass(frozen=True)
class PortfolioFactorValue:
    factor_id: str
    value: float | None
    available_date: date
    # 원장이 가린 칸 때문에 팩터 값이 없다(평가기 판정 `FactorValue.masked`, #350).
    masked: bool = False


@dataclass(frozen=True)
class PortfolioObservation:
    """One (as_of, security) row the compiler scores.

    `universe_member` and `sector_id` carry no publication date, so the FUTURE_DATA guard cannot
    reach them: both are point-in-time facts the observation adapter owns (see the
    `RawObservationPort` contract). A retroactive index reconstitution or sector reclassification
    is therefore invisible here and shows up as silent look-ahead in selection and in the sector
    exposure constraint.

    `previous_weight` seeds the *first* rebalance frame only. Later frames read the book that
    `compile_target_tape` folds forward from the previous frame's targets, not this field.
    """

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
    # 해시 밖의 파생 사실이다. `tape_hash` 는 frames 로 계산하고, 경고는 같은 입력에서 결정적으로
    # 다시 나오므로 해시에 넣지 않는다(construction trace 와 같은 out-of-band 원칙).
    warnings: tuple[PortfolioWarning, ...] = ()
