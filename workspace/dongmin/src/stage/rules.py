"""stage 테이블 선언 (STAGE_DESIGN v2.2 §3·§4·§5). 코드가 아니라 목록이다.

(p,s) 는 survey v2 전수 측정(`survey_out/v2/ps_table.json`) + 여유 자릿수 PS_HEADROOM_DIGITS.
컬럼 참조는 원장 실명(`pragma_table_info` 로 G0 가 선검증)이며, 식별자는 숫자 캐스팅·zfill 금지.
"""
from __future__ import annotations

from dataclasses import dataclass

RULES_VERSION = "2.2.0"
PS_HEADROOM_DIGITS = 2   # survey 최대 자릿수 + 2 (성장 여유). 초과 = cast_failed → G2

KIND_TEXT = "text"
KIND_NUMERIC = "numeric"
KIND_DATE_YMD8 = "date_yyyymmdd"


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
    nonempty_flag: str | None = None  # 빈값 여부 불린 컬럼 병기 (예: sect_available)

    @property
    def decimal_type(self) -> str:
        if self.kind != KIND_NUMERIC or self.precision is None or self.scale is None:
            raise ValueError(f"decimal_type on non-numeric column: {self.name} kind={self.kind}")
        return f"DECIMAL({self.precision},{self.scale})"


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
class TableRule:
    name: str
    sources: tuple[SourceRef, ...]
    columns: tuple[ColumnRule, ...]
    natural_key: tuple[str, ...]        # stage 컬럼명
    partition_class: str                # date_axis | receipt_axis | whole
    partition_expr: str                 # 원장 컬럼 기준 표현식 (§4-파티션)
    observed_src: str | None            # 시각 컬럼 실명. None = 면제(corp_map)
    write_mode: str                     # append_only | upsert | first_write_wins
    fanout: int
    payload_exclude: tuple[str, ...]    # payload 투영에서 빼는 원장 컬럼 (req_*·collected_at 등)
    lag_known: bool
    date_axis_src: str                  # G7 관측일 축 원장 컬럼
    invariants: tuple[Invariant, ...] = ()
    cross_check: CrossCheck | None = None

    def column(self, name: str) -> ColumnRule:
        for c in self.columns:
            if c.name == name:
                return c
        raise KeyError(f"no such stage column: table={self.name} column={name}")

    @property
    def numeric_or_date_columns(self) -> tuple[ColumnRule, ...]:
        return tuple(c for c in self.columns if c.kind in (KIND_NUMERIC, KIND_DATE_YMD8))


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
    observed_src="collected_at",
    write_mode="upsert",
    fanout=1,
    payload_exclude=("bas_dd_req", "collected_at"),
    lag_known=True,                      # 가격 = 당일 실시간 관측 실증 (결정 ⑦)
    date_axis_src="BAS_DD",
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

RULES: dict[str, TableRule] = {STG_PRICE_DAILY.name: STG_PRICE_DAILY}
