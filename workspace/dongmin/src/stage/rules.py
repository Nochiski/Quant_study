"""stage 테이블 선언 (STAGE_DESIGN v2.2 §3·§4·§5). 코드가 아니라 목록이다.

(p,s) 는 survey v2 전수 측정(`survey_out/v2/ps_table.json`) + 여유 자릿수 PS_HEADROOM_DIGITS.
컬럼 참조는 원장 실명(`pragma_table_info` 로 G0 가 선검증)이며, 식별자는 숫자 캐스팅·zfill 금지.
"""
from __future__ import annotations

from dataclasses import dataclass

RULES_VERSION = "2.2.1"
# db alias → 원장 파일명. survey/targets.py DBS 와 같아야 한다(테스트 대조). wise 만 다르다.
LEDGER_FILES: dict[str, str] = {"krx": "krx.db", "kiwoom": "kiwoom.db", "kis": "kis.db",
                                "dart": "dart.db", "wise": "wisereport.db"}
PS_HEADROOM_DIGITS = 2   # survey 최대 자릿수 + 2 (성장 여유). 초과 = cast_failed → G2

KIND_TEXT = "text"
KIND_NUMERIC = "numeric"
KIND_DATE_YMD8 = "date_yyyymmdd"
KIND_DATE_ISO = "date_iso"          # YYYY-MM-DD
KIND_DATE_SLASH = "date_slash"      # YYYY/MM/DD (WISE 관측 라벨)
KIND_BOOL = "bool"
DATE_FORMATS: dict[str, str] = {KIND_DATE_YMD8: "%Y%m%d", KIND_DATE_ISO: "%Y-%m-%d",
                                KIND_DATE_SLASH: "%Y/%m/%d"}


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
    expected_len: int | None = None
    zero_is_missing: bool = False   # 원문 문자열 '0' → NULL + miss_kind=ledger_zero (KRX O/H/L)
    normalize_text: bool = False    # §5 문자열 정규화 — 식별자·조인 키에는 금지
    key: bool = False
    required: bool = False          # 키는 아니지만 NULL 이면 행이 무의미 → reject(required_null)
    nonempty_flag: str | None = None  # 빈값 여부 불린 컬럼 병기 (예: sect_available)

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


def _p(survey_max_digits: int, scale: int = 0) -> tuple[int, int]:
    return survey_max_digits + PS_HEADROOM_DIGITS, scale


# ── stg_price_daily (KRX stk+ksq bydd, 17컬럼 UNION) ──────────────────────────
_PRICE_P, _PRICE_S = _p(7)          # 가격 max 7자리
_FLUC_P, _FLUC_S = _p(9, 2)         # FLUC_RT max 정수 7 + 소수 2 (survey p=9)
STG_PRICE_DAILY = TableRule(
    name="stg_price_daily",
    sources=(SourceRef("krx", "krx_stk_bydd_trd", "stk"),
             SourceRef("krx", "krx_ksq_bydd_trd", "ksq")),
    columns=(
        ColumnRule("BAS_DD", "date", KIND_DATE_YMD8, key=True),
        ColumnRule("ISU_CD", "ticker", KIND_TEXT, expected_len=6, key=True),
        ColumnRule("ISU_NM", "name", KIND_TEXT, normalize_text=True),
        ColumnRule("MKT_NM", "market", KIND_TEXT, normalize_text=True),
        ColumnRule("SECT_TP_NM", "sect_tp", KIND_TEXT, normalize_text=True,
                   nonempty_flag="sect_available"),
        ColumnRule("TDD_CLSPRC", "close_krw", KIND_NUMERIC, _PRICE_P, _PRICE_S),
        ColumnRule("CMPPREVDD_PRC", "change_krw", KIND_NUMERIC, _PRICE_P, _PRICE_S),
        ColumnRule("FLUC_RT", "fluc_pct", KIND_NUMERIC, _FLUC_P, _FLUC_S),
        ColumnRule("TDD_OPNPRC", "open_krw", KIND_NUMERIC, _PRICE_P, _PRICE_S,
                   zero_is_missing=True),
        ColumnRule("TDD_HGPRC", "high_krw", KIND_NUMERIC, _PRICE_P, _PRICE_S,
                   zero_is_missing=True),
        ColumnRule("TDD_LWPRC", "low_krw", KIND_NUMERIC, _PRICE_P, _PRICE_S,
                   zero_is_missing=True),
        ColumnRule("ACC_TRDVOL", "volume_shr", KIND_NUMERIC, *_p(10)),
        ColumnRule("ACC_TRDVAL", "value_krw", KIND_NUMERIC, *_p(14)),
        ColumnRule("MKTCAP", "mktcap_krw", KIND_NUMERIC, *_p(16)),
        ColumnRule("LIST_SHRS", "list_shrs", KIND_NUMERIC, *_p(10)),
    ),
    natural_key=("ticker", "date"),
    partition_class="date_axis",
    partition_expr="substr(BAS_DD, 1, 4)",
    partition_src="BAS_DD",
    observed_src="collected_at",
    write_mode="upsert",
    fanout=1,
    payload_exclude=("bas_dd_req", "collected_at"),
    lag_known=True,                      # 가격 = 당일 실시간 관측 실증 (결정 ⑦)
    available=AvailableRule("column", column="date"),
    key_unique=True,                     # (ISU_CD, BAS_DD) 유일 — S1 실측 dedup 0
    invariants=(
        Invariant("mktcap", "mktcap_krw <> close_krw * list_shrs"),
        Invariant("market_src", "NOT ((market = 'KOSPI' AND _src = 'stk') "
                                "OR (market = 'KOSDAQ' AND _src = 'ksq'))"),
        Invariant("ohl_pattern", "((open_krw IS NULL)::INT + (high_krw IS NULL)::INT "
                                 "+ (low_krw IS NULL)::INT) NOT IN (0, 3)"),
    ),
    cross_check=CrossCheck(
        db="kiwoom", table="ka10060_investor_flows",
        join_sql="o.ticker = s.ticker AND o.dt = strftime(s.date, '%Y%m%d')",
        close_match_sql="abs(TRY_CAST(o.cur_prc AS BIGINT)) = s.close_krw",
        volume_match_sql="TRY_CAST(o.acc_trde_prica AS BIGINT) = s.volume_shr",
    ),
)

# ── stg_rcept_dt_map (참조표, §1 예외 d) — rcept_no → rcept_dt. disclosure 유래, 키당 값 불변 ──
STG_RCEPT_DT_MAP = TableRule(
    name="stg_rcept_dt_map",
    sources=(SourceRef("dart", "dart_disclosure", "disclosure"),),
    columns=(
        ColumnRule("rcept_no", "rcept_no", KIND_TEXT, expected_len=14, key=True),
        ColumnRule("rcept_dt", "rcept_dt", KIND_DATE_YMD8, required=True),
    ),
    natural_key=("rcept_no",),
    partition_class="whole",
    partition_expr=None,
    partition_src=None,
    observed_src="collected_at",
    write_mode="append_only",
    fanout=1,
    payload_exclude=("row_hash", "dup_seq", "collected_at"),
    lag_known=False,
    available=AVAILABLE_NONE,            # 참조표 — available_date 비부여 (§6)
    payload_columns=("rcept_no", "rcept_dt"),   # 페이지 경계 중복(618 그룹)은 이 투영에서 접힌다
    key_unique=True,                     # 실측: rcept_no 당 distinct rcept_dt > 1 = 0
)

# ── stg_fin (dart_fin_raw 28컬럼) — 골격 키 예외: ticker·date 없음, 키는 요청축 8컬럼 ─────────


def _amt(src: str) -> ColumnRule:
    """재무 금액 컬럼 — survey v2 전수: 정수 max 18·소수 2 → Decimal(38,4) (설계 확정)."""
    return ColumnRule(src, src, KIND_NUMERIC, 38, 4)


_SENTINEL = "-표준계정코드 미사용-"
STG_FIN = TableRule(
    name="stg_fin",
    sources=(SourceRef("dart", "dart_fin_raw", "fin"),),
    columns=(
        ColumnRule("req_corp_code", "corp_code", KIND_TEXT, expected_len=8, key=True),
        ColumnRule("req_bsns_year", "bsns_year", KIND_TEXT, expected_len=4, key=True),
        ColumnRule("req_reprt_code", "reprt_code", KIND_TEXT, expected_len=5, key=True),
        ColumnRule("req_fs_div", "fs_div", KIND_TEXT, key=True),
        ColumnRule("sj_div", "sj_div", KIND_TEXT, key=True),
        ColumnRule("account_id", "account_id", KIND_TEXT, key=True),        # 원문 — 정규화 금지
        ColumnRule("account_detail", "account_detail", KIND_TEXT, key=True),  # 원문 보존
        ColumnRule("ord", "ord", KIND_NUMERIC, *_p(3), key=True),
        ColumnRule("rcept_no", "rcept_no", KIND_TEXT, expected_len=14, required=True),
        ColumnRule("corp_code", "corp_code_resp", KIND_TEXT),
        ColumnRule("bsns_year", "bsns_year_resp", KIND_TEXT),
        ColumnRule("reprt_code", "reprt_code_resp", KIND_TEXT),
        ColumnRule("sj_nm", "sj_nm", KIND_TEXT, normalize_text=True),
        ColumnRule("account_nm", "account_nm", KIND_TEXT, normalize_text=True),
        ColumnRule("thstrm_nm", "thstrm_nm", KIND_TEXT, normalize_text=True),      # 날짜 파싱 금지
        _amt("thstrm_amount"),
        _amt("thstrm_add_amount"),
        ColumnRule("frmtrm_nm", "frmtrm_nm", KIND_TEXT, normalize_text=True),
        _amt("frmtrm_amount"),
        ColumnRule("frmtrm_q_nm", "frmtrm_q_nm", KIND_TEXT, normalize_text=True),
        _amt("frmtrm_q_amount"),
        _amt("frmtrm_add_amount"),
        ColumnRule("bfefrmtrm_nm", "bfefrmtrm_nm", KIND_TEXT, normalize_text=True),
        _amt("bfefrmtrm_amount"),
        ColumnRule("currency", "currency", KIND_TEXT),
    ),
    natural_key=("corp_code", "bsns_year", "reprt_code", "fs_div", "sj_div", "account_id",
                 "account_detail", "ord"),
    partition_class="receipt_axis",
    partition_expr="substr(rcept_no, 1, 4)",
    partition_src="rcept_no",
    observed_src="collected_at",
    write_mode="append_only",
    fanout=1,
    payload_exclude=("row_hash", "dup_seq", "collected_at"),
    lag_known=False,                     # 공개일은 참조표(derived)
    available=AvailableRule("lookup", table="stg_rcept_dt_map", local_key="rcept_no",
                            lookup_key="rcept_no", lookup_value="rcept_dt"),
    key_unique=True,                     # 실측: 8컬럼 자연키 위반 0 (15,375,024행)
    extras=(
        ExtraColumn("bsns_year_mismatch", 's."req_bsns_year" <> s."bsns_year"'),
        ExtraColumn("account_std", f"s.\"account_id\" <> '{_SENTINEL}'"),
        ExtraColumn("account_detail_path",
                    "CASE WHEN s.\"account_detail\" IN ('-', '') THEN NULL::VARCHAR[] "
                    "ELSE string_split(s.\"account_detail\", '|') END"),
        ExtraColumn("is_krw", "s.\"currency\" = 'KRW'"),
    ),
    invariants=(
        Invariant("currency_null", "currency IS NULL OR currency = ''"),
    ),
)

# ── stg_consensus_monthly (ws_raw cF5001+cF5002 blob 언네스트, §1 예외 c·e) ────────────────────
_CONS_P, _CONS_S = 20, 4                 # 실측 EPS 5자리·매출(억원) 7자리 + 소수 2 → 여유
STG_CONSENSUS_MONTHLY = TableRule(
    name="stg_consensus_monthly",
    sources=(SourceRef("wise", "ws_raw", "ws_raw"),),
    columns=(
        ColumnRule("cmp_cd", "ticker", KIND_TEXT, expected_len=6, key=True),
        ColumnRule("fetched_date", "fetched_date", KIND_DATE_ISO, key=True),
        ColumnRule("pkey", "target_period", KIND_TEXT, expected_len=6, key=True),
        ColumnRule("metric", "metric", KIND_TEXT, key=True),          # eps | revenue | parse_failed
        ColumnRule("obs_label", "obs_label", KIND_TEXT, key=True),    # 원문 라벨 보존
        ColumnRule("obs_label", "obs_date", KIND_DATE_SLASH),
        ColumnRule("unit", "unit", KIND_TEXT),                        # 데이터 값 — 스케일 변환 금지
        ColumnRule("consensus", "consensus", KIND_NUMERIC, _CONS_P, _CONS_S),
        ColumnRule("consensus_min", "consensus_min", KIND_NUMERIC, _CONS_P, _CONS_S),
        ColumnRule("consensus_max", "consensus_max", KIND_NUMERIC, _CONS_P, _CONS_S),
        ColumnRule("close_price_krw", "close_price_krw", KIND_NUMERIC, 14, 2),
        ColumnRule("target_price_krw", "target_price_krw", KIND_NUMERIC, 14, 2),
        ColumnRule("in_5001", "in_5001", KIND_BOOL),
        ColumnRule("in_5002", "in_5002", KIND_BOOL),
    ),
    natural_key=("ticker", "fetched_date", "target_period", "metric", "obs_label"),
    partition_class="date_axis",
    partition_expr="substr(fetched_date, 1, 4)",
    partition_src="fetched_date",
    observed_src="fetched_at",
    write_mode="append_only",
    fanout=1,                            # 파서 출력 행 기준. blob→행 계상은 G8
    payload_exclude=("fetched_at",),
    lag_known=True,                      # 06:00 KST 수집 = 그날 장 시작 전 가용 (측정된 수집 시각)
    available=AvailableRule("column", column="fetched_date", basis="measured"),
    key_unique=True,
    blob_source=BlobSource("wise", "ws_raw", ("cF5001", "cF5002"), "parse_consensus_monthly"),
)

RULES: dict[str, TableRule] = {
    r.name: r for r in (STG_PRICE_DAILY, STG_RCEPT_DT_MAP, STG_FIN, STG_CONSENSUS_MONTHLY)
}
