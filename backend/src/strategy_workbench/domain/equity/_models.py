from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
from enum import Enum, StrEnum


class DataLoadStatus(Enum):
    OK = "ok"
    NO_DATA = "no_data"
    INVALID_QUERY = "invalid_query"
    CONFIRMATION_REQUIRED = "confirmation_required"


class CellKind(Enum):
    """A numeric zero and unavailable data must never collapse into one value."""

    OBSERVED = "observed"
    SOURCE_OMITTED_ZERO = "source_omitted_zero"
    MISSING = "missing"
    NOT_COLLECTED = "not_collected"
    COVERAGE_GAP = "coverage_gap"
    # 원장 뷰가 값이 틀려 일부러 가린 셀(무상증자 척도 창의 신용잔고 #249, 수정주가 조정 공백
    # 적용일 #220). 값은 없다. 가림 창 안이면 원래 결측 사유(not_collected 등)가 있던 행도 여기
    # 든다 — 창의 값은 척도가 섞여 있어 모르는 값을 채워도 틀린다. 모르는 값(MISSING)과 달리 실행
    # 결측 정책이 채우지 않는다 — 채우면 가리기 전보다 더 틀린다(#298).
    MASKED = "masked"


class FieldValueType(Enum):
    PRICE = "price"
    AMOUNT = "amount"
    RATIO = "ratio"
    COUNT = "count"
    CATEGORY = "category"


class FieldFrequency(StrEnum):
    """필드 값이 새로 나오는 주기. 워크벤치 어댑터가 `list_fields()` 로 내는 어휘다.

    원장 `dataset_profile` 의 빈도(session·report 등)와는 다른 어휘다 — 두 어댑터가 같은 필드에
    같은 빈도를 답하는지는 `tests/contract/test_equity_field_contract_parity.py` 가 본다. 화면은
    이 값마다 문구를 두므로(#350) 목록을 늘리면 frontend typecheck 가 문구를 요구한다.
    """

    DAILY = "daily"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"
    EVENT = "event"


@dataclass(frozen=True)
class DatasetRevision:
    dataset_id: str
    revision: str
    as_of: date


@dataclass(frozen=True)
class FieldCoverageCapability:
    starts_on: date
    ends_on: date
    venues: tuple[str, ...]
    estimated_coverage_pct: float
    supported_cell_kinds: tuple[CellKind, ...]
    point_in_time: bool
    requires_confirmation: bool = False

    def __post_init__(self) -> None:
        if self.starts_on > self.ends_on:
            raise ValueError(
                "field coverage start must be <= end — "
                f"starts_on={self.starts_on} ends_on={self.ends_on}"
            )
        if not 0 <= self.estimated_coverage_pct <= 100:
            raise ValueError(
                "field coverage percentage must be within [0, 100] — "
                f"estimated_coverage_pct={self.estimated_coverage_pct}"
            )
        if not self.venues:
            raise ValueError("field coverage requires at least one venue — venues=()")


@dataclass(frozen=True)
class DataSnapshot:
    snapshot_id: str
    schema_version: str
    built_at: datetime
    source: str
    point_in_time: bool
    dataset_revisions: tuple[DatasetRevision, ...]


# 워크벤치 데이터 스냅샷 id 는 "원천 판:필드 계약 판" 이다(#235). 같은 원장 빌드라도 필드를 읽는
# 규칙(어댑터 선언표·카탈로그 매크로 본문)이 바뀌면 값이 달라지는데, 원천 판만으로는 재현 지문·팩터
# 행렬 캐시 키·run manifest 가 옛 의미와 새 의미를 같은 데이터로 기록했다.
SNAPSHOT_CONTRACT_SEPARATOR = ":"
# 판에 싣지 않는 사람용 문장 칸. 선언의 뜻(식·단위·랙·값 타입·고르는 규칙)을 바꾸지 않으므로
# 문장만 고친 변경이 스냅샷 id·재현 지문·캐시 키를 흔들면 안 된다(#291 리뷰 P2-1). 새 문장 칸을
# 여기 빠뜨려도 판이 헛되이 바뀔 뿐 뜻의 변화를 놓치지는 않는다.
CONTRACT_PROSE_FIELDS = frozenset(
    {
        "label",
        "description",
        "evidence",
        "disclosure_basis",
        "lag_basis",
        "available_date_basis",
    }
)


def canonical_revision(material: object) -> str:
    """선언이나 fixture 데이터의 판 — canonical JSON 의 sha256 앞 16자리.

    dataclass 는 사람용 문장 칸(`CONTRACT_PROSE_FIELDS`)을 빼고 싣는다. 목록은 순서까지 판에
    들어가므로 선언은 id 를 키로 한 dict 로 넘긴다 — 그래야 선언 순서만 바꾼 변경이 판을 흔들지
    않는다.
    """
    text = json.dumps(
        material,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_contract_json_default,
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def field_contract_snapshot_id(source_snapshot_id: str, field_contract: object) -> str:
    """원천 판 id 뒤에 필드 계약 판(`canonical_revision`)을 붙인다.

    앞부분은 원천이 정한 id(원장 테이블 build 해시·mock fixture 데이터의 판) 그대로라
    `_catalog_meta.json`·`ledger_sync` 와 눈으로 대조된다. 소비자는 이 id 를 따로 조립하지 않고
    포트가 돌려주는 `data_snapshot_id` 로 받는다.
    """
    return f"{source_snapshot_id}{SNAPSHOT_CONTRACT_SEPARATOR}{canonical_revision(field_contract)}"


def _contract_json_default(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: getattr(value, item.name)
            for item in fields(value)
            if item.name not in CONTRACT_PROSE_FIELDS
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    raise TypeError(f"unsupported field contract value — type={type(value).__name__}")


@dataclass(frozen=True)
class DatasetFieldProfile:
    field_id: str
    dataset_id: str
    label: str
    unit: str
    value_type: FieldValueType
    frequency: FieldFrequency
    available_date_basis: str
    recommended_lag_sessions: int
    description: str
    disclosure_basis: str
    evidence: str
    coverage: FieldCoverageCapability

    def __post_init__(self) -> None:
        if not self.field_id or not self.dataset_id:
            raise ValueError(
                "dataset field identifiers must not be empty — "
                f"field_id={self.field_id!r} dataset_id={self.dataset_id!r}"
            )
        if self.recommended_lag_sessions < 0:
            raise ValueError(
                "recommended lag must be >= 0 — "
                f"field_id={self.field_id} lag_sessions={self.recommended_lag_sessions}"
            )


@dataclass(frozen=True)
class SecurityRef:
    security_id: str
    ticker: str
    name: str
    venue: str


@dataclass(frozen=True)
class UniversePoint:
    session: date
    members: tuple[SecurityRef, ...]
    coverage: CellKind = CellKind.OBSERVED


@dataclass(frozen=True)
class UniverseHistoryQuery:
    venue: str
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError(f"universe start must be <= end — start={self.start} end={self.end}")


@dataclass(frozen=True)
class UniverseHistoryResult:
    points: tuple[UniversePoint, ...]
    status: DataLoadStatus
    snapshot_id: str
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is DataLoadStatus.OK


@dataclass(frozen=True)
class FieldLag:
    field_id: str
    sessions: int

    def __post_init__(self) -> None:
        if self.sessions < 0:
            raise ValueError(
                f"field lag must be >= 0 — field_id={self.field_id} sessions={self.sessions}"
            )


@dataclass(frozen=True)
class ResearchPanelQuery:
    start: date
    end: date
    security_ids: tuple[str, ...]
    field_ids: tuple[str, ...]
    lag_overrides: tuple[FieldLag, ...] = ()

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError(f"panel start must be <= end — start={self.start} end={self.end}")
        if not self.security_ids:
            raise ValueError("panel query requires at least one security_id")
        if not self.field_ids:
            raise ValueError("panel query requires at least one field_id")
        for label, values in (
            ("security_ids", self.security_ids),
            ("field_ids", self.field_ids),
            ("lag_overrides", tuple(item.field_id for item in self.lag_overrides)),
        ):
            if len(set(values)) != len(values):
                raise ValueError(f"panel query has duplicate {label} — values={values}")


@dataclass(frozen=True)
class ResearchPanelCell:
    as_of: date
    security_id: str
    field_id: str
    source_effective_date: date
    available_date: date
    value: float | str | bool | None
    kind: CellKind

    def __post_init__(self) -> None:
        if self.kind in (CellKind.OBSERVED, CellKind.SOURCE_OMITTED_ZERO):
            if self.value is None:
                raise ValueError(
                    "observed panel cell requires a value — "
                    f"security_id={self.security_id} field_id={self.field_id} as_of={self.as_of}"
                )
        elif self.value is not None:
            raise ValueError(
                "unavailable panel cell must not carry a value — "
                f"security_id={self.security_id} field_id={self.field_id} "
                f"kind={self.kind.value} value={self.value}"
            )


@dataclass(frozen=True)
class ResearchPanelResult:
    cells: tuple[ResearchPanelCell, ...]
    status: DataLoadStatus
    snapshot_id: str
    warnings: tuple[str, ...] = ()
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is DataLoadStatus.OK
