"""stage 규칙의 타입·컬럼 종류·헬퍼 (STAGE_DESIGN v2.2 §3·§5). 테이블 선언은 rules_<source>.py.

(p,s) 는 survey v2 전수 측정(`survey_out/v2/ps_table.json`) + 여유 자릿수 PS_HEADROOM_DIGITS.
컬럼 참조는 원장 실명(`pragma_table_info` 로 G0 가 선검증)이며, 식별자는 숫자 캐스팅·zfill 금지.
"""
from __future__ import annotations

from dataclasses import dataclass

RULES_VERSION = "2.2.3"
PS_HEADROOM_DIGITS = 2   # survey 최대 자릿수 + 2 (성장 여유). 초과 = cast_failed → G2

KIND_TEXT = "text"
KIND_NUMERIC = "numeric"
KIND_DATE_YMD8 = "date_yyyymmdd"
KIND_DATE_ISO = "date_iso"          # YYYY-MM-DD
KIND_DATE_SLASH = "date_slash"      # YYYY/MM/DD (WISE 관측 라벨)
KIND_DATE_KOREAN = "date_korean"    # YYYY년 MM월 DD일 (DART DS005)
KIND_DATE_DOT = "date_dot"          # YYYY.MM.DD (dart_capital.isu_dcrs_de — SPEC §2-14)
KIND_DATE_YY_SLASH = "date_yy_slash"  # YY/MM/DD (WISE cTB24 최종일자 — %y: 00~68 → 20xx)
KIND_BOOL = "bool"
DATE_FORMATS: dict[str, str] = {KIND_DATE_YMD8: "%Y%m%d", KIND_DATE_ISO: "%Y-%m-%d",
                                KIND_DATE_SLASH: "%Y/%m/%d", KIND_DATE_KOREAN: "%Y년 %m월 %d일",
                                KIND_DATE_DOT: "%Y.%m.%d", KIND_DATE_YY_SLASH: "%y/%m/%d"}


def is_castable(kind: str) -> bool:
    """캐스팅(=miss_kind 기록) 대상 종류 — text 만 제외."""
    return kind != KIND_TEXT


@dataclass(frozen=True)
class ColumnRule:
    src: str                    # 원장 컬럼 실명
    name: str                   # stage 컬럼명 (단위 접미사 규약: _krw · _shr · _pct)
    kind: str
    precision: int | None = None
    scale: int | None = None
    sign: str = "keep"          # abs | strip_plus | keep (§5 부호 정책)
    unit_scale: int | None = None   # 단위 스케일 (백만원 ×1_000_000 → _krw). 캐스트 전 곱
    expected_len: int | None = None
    zero_is_missing: bool = False   # 원문 '0'·'0.00'·'00000000' → NULL + ledger_zero (KRX OHL·KIS)
    normalize_text: bool = False    # §5 문자열 정규화 — 식별자·조인 키에는 금지
    strip_tags: bool = False        # 정규화 뒤 <…> 제거 — WISE 라벨만. DART '<주1>' 은 각주
    key: bool = False
    required: bool = False          # 키는 아니지만 NULL 이면 행이 무의미 → reject(required_null)
    nonempty_flag: str | None = None  # 빈값 여부 불린 컬럼 병기 (예: sect_available)
    blank_is_value: bool = False    # 키의 '' 를 값으로 인정(key_missing 아님) — ws_call_log.pkey

    @property
    def decimal_type(self) -> str:
        if self.kind != KIND_NUMERIC or self.precision is None or self.scale is None:
            raise ValueError(f"decimal_type on non-numeric column: {self.name} kind={self.kind}")
        return f"DECIMAL({self.precision},{self.scale})"


@dataclass(frozen=True)
class ExtraColumn:
    """categorize 파생 컬럼 — 같은 행의 원장 컬럼만 참조하는 SQL (`s."원장컬럼"`). §1 1:1 안."""

    name: str
    sql: str


@dataclass(frozen=True)
class SourceRef:
    db: str
    table: str
    src_tag: str


@dataclass(frozen=True)
class Invariant:
    """G3 불변식 — stage 컬럼 위 '위반 행' 술어. 0 이어야 통과."""

    key: str
    violation_sql: str


@dataclass(frozen=True)
class CrossCheck:
    """G9 교차 소스 회귀 — 원장 직접 대조 (stage 간 아님)."""

    db: str
    table: str
    join_sql: str          # o = 원장, s = stage
    close_match_sql: str
    volume_match_sql: str


@dataclass(frozen=True)
class BlobSource:
    """§1 예외 (c)·(e) — 원장 blob 을 파이썬 파서로 N행 언네스트. sources[0] 이 원장(ATTACH·G0)."""

    db: str
    table: str
    eps: tuple[str, ...]
    parser: str                                  # parsers.PARSERS 키
    required_columns: tuple[str, ...] = ("cmp_cd", "ep", "pkey", "fetched_date", "body",
                                         "fetched_at")


@dataclass(frozen=True)
class AvailableRule:
    """available_date 부여 규칙 (§6).

    column = 내용일 그대로(default) / lookup = 참조표(derived, 미스는 unknown+NULL) / none = 비부여.
    """

    kind: str                       # column | lookup | none
    column: str | None = None       # kind=column: stage 컬럼명
    table: str | None = None        # kind=lookup: 참조 stage 테이블
    local_key: str | None = None    # kind=lookup: 이 테이블의 조인 컬럼(stage 이름)
    lookup_key: str | None = None   # kind=lookup: 참조 테이블 키 컬럼
    lookup_value: str | None = None  # kind=lookup: 참조 테이블 날짜 컬럼
    basis: str = "default"          # kind=column: default(내용일 대용) | measured(수집일 등 실재)
    fallback_column: str | None = None   # kind=column: column 이 NULL 이면 이 컬럼 + basis default


AVAILABLE_NONE = AvailableRule("none")


@dataclass(frozen=True)
class TableRule:
    name: str
    sources: tuple[SourceRef, ...]
    columns: tuple[ColumnRule, ...]
    natural_key: tuple[str, ...]        # stage 컬럼명
    partition_class: str                # date_axis | receipt_axis | whole
    partition_expr: str | None          # 원장 컬럼 기준 표현식 (§4-파티션). whole = None
    partition_src: str | None           # partition_expr 가 참조하는 원장 컬럼
    observed_src: str | None            # 시각 컬럼 실명. None = 면제(corp_map)
    write_mode: str                     # append_only | upsert | first_write_wins
    fanout: int
    payload_exclude: tuple[str, ...]    # payload 투영에서 빼는 원장 컬럼 (req_* 는 항상 제외)
    lag_known: bool
    available: AvailableRule
    payload_columns: tuple[str, ...] | None = None   # 명시하면 이 원장 컬럼들만 payload
    key_unique: bool = False            # G3: natural_key 유일성 (원장 실측으로 확정된 테이블만)
    extras: tuple[ExtraColumn, ...] = ()
    invariants: tuple[Invariant, ...] = ()
    cross_check: CrossCheck | None = None
    blob_source: BlobSource | None = None
    coverage_from: str | None = None    # §3 temporality ⓑ — 누적 스냅샷 관측 시작일 (ka10099 09-01)
    versioned: bool = True              # False = 판본 없는 로그(콜·유닛) → G6 skip(unversioned)

    def column(self, name: str) -> ColumnRule:
        for c in self.columns:
            if c.name == name:
                return c
        raise KeyError(f"no such stage column: table={self.name} column={name}")

    @property
    def castable_columns(self) -> tuple[ColumnRule, ...]:
        return tuple(c for c in self.columns if is_castable(c.kind))

    @property
    def key_columns(self) -> tuple[ColumnRule, ...]:
        return tuple(c for c in self.columns if c.key)


def p_headroom(survey_max_digits: int, scale: int = 0) -> tuple[int, int]:
    """survey v2 최대 자릿수 + 여유 → (precision, scale)."""
    return survey_max_digits + PS_HEADROOM_DIGITS, scale
