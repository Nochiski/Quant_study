"""equity 테이블 선언의 타입 (EQUITY_DESIGN v1.2 §2·§3). 선언은 rules_<단계>.py 가 한다.

stage `model.TableRule` 과 달리 캐스팅 축(`ColumnRule.kind/precision`)이 없다 — 입력이 이미
타입 있는 parquet 이기 때문이다. 대신 조인층의 축이 들어간다:
  inputs(무엇을 읽는가) · eg1_*_sql(EG1 등식) · available_rule(EG2) · reject_reasons(EG3 어휘).
stage 컬럼명은 `stage.rules.RULES[<table>].columns[].name` 이 정본이고 EG0 가 실물 대조한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:                       # 순환 import 회피 — gates 가 model 을 읽는다
    from collections.abc import Callable

    import duckdb
    from stage.gates import GateResult

    from .gates import EquityGateContext

    ExtraGate = Callable[[EquityGateContext], GateResult]
    DeclareHook = Callable[["duckdb.DuckDBPyConnection", "EquityTable"], None]

RULES_VERSION = "e1.5.0"                # BuildRecord.rules_version 에 실린다.
# 규칙(sql/*.sql·rules_*.py·게이트 술어)이 산출을 바꾸는 변경이면 반드시 올린다 — EG5a 는 같은
# 판본의 직전 빌드하고만 해시를 비교하고, 판본이 다르면 skip(rules_changed) 한다(09-05 corp_event
# 4차·S05-4 실측).
# e1.1.0: EG5a rules_changed 도입 · e1.2.0: corp_event 종류 어휘 대응표(S05-4)
# e1.3.0: S06-2 기준가 원천 · e1.3.1: S03C 기업행위 창에서 unknown_price_only 제외
# e1.3.0: S06-2 — price_daily +change_krw·base_price_krw · adj_factor 원천 krx_base_price(사건 교체·
#         unknown_krx·unknown_price_only 신규 행)
# e1.4.0: S19·S20 — `FieldProfile` 선언면 신설(`EquityTable.field_profiles`·`declarations` 훅) +
#         `dataset_profile`·`factor_readiness` 두 선언표. 기존 22테이블의 **산출은 바뀌지 않지만**
#         선언이 늘어 EG5a 비교 대상이 달라지므로 판본을 올린다(GATES §2-4 규약).
# e1.5.0: S19-2 — 격자 3테이블(`flow_daily`·`short_daily`·`credit_daily`)이 `field_profiles` 로
#         6필드를 선언하고 `rules_s19.SOURCE_TABLES` 가 22 → 25 로 늘었다. 세 격자 테이블의 산출은
#         그대로지만 `dataset_profile` 66 → 72행 · `factor_readiness` 판정(31/23 → 35/19)이
#         바뀐다.

# DESIGN §1 — stage 4종 + equity 신설 convention
BASIS_VOCAB: tuple[str, ...] = ("measured", "derived", "convention", "default", "unknown")
# DESIGN §3 — 격자 테이블의 fill_kind STRUCT(kind, evidence)
FILL_KINDS: tuple[str, ...] = ("measured", "src_omitted", "empty_response", "not_collected")
FILL_EVIDENCE: tuple[str, ...] = ("shard_done", "unit_ok", "shard_empty", "unit_empty", "none")
# DESIGN §2 — 물리 파티션 축
PARTITION_CLASSES: tuple[str, ...] = ("date_axis", "receipt_axis", "whole")

AVAILABLE_NONE = "none"                 # 차원 테이블 — available_date 를 부여하지 않는다

# ── S19 `dataset_profile` 선언 어휘 (DESIGN §4-7 · FIELD_MAP §1) ──────────────────
# 엔진 `strategy_workbench.domain.equity.FieldValueType` 의 값을 그대로 쓴다 — 어댑터가
# `DatasetFieldProfile.value_type` 으로 그대로 올린다.
VALUE_TYPES: tuple[str, ...] = ("price", "amount", "ratio", "count", "category")
# 엔진 `CellKind` 값. FIELD_MAP §1 결측 어휘 대응표의 우변이다.
CELL_KINDS: tuple[str, ...] = (
    "observed", "source_omitted_zero", "missing", "not_collected", "coverage_gap")
# 관측 빈도 — 어댑터 `DatasetFieldProfile.frequency`
FIELD_FREQUENCIES: tuple[str, ...] = ("session", "monthly", "report", "event", "static")
# 커버율을 재는 축.
#   grid_session  = universe_daily (ticker, date) 격자 셀 분모 — 세션 빈도 필드(가격·유니버스)
#   grid_security = 격자의 **종목** 분모 — 월·이벤트 빈도 필드(컨센서스·의견)는 일별 격자로 나누면
#                   구조적으로 낮게 나온다. FIELD_MAP §3 의 "커버 종목 804 / 810" 이 이 축이다
#   table_rows    = 소유 테이블 행수 분모 — 법인·접수 축(`fin_std`·4B)은 종목 격자가 없다
#   static_label  = 차원표 현재값 라벨(캘린더 창)
COVERAGE_AXES: tuple[str, ...] = ("grid_session", "grid_security", "table_rows", "static_label")
GRID_AXES: tuple[str, ...] = ("grid_session", "grid_security")
# field_id 가 FIELD_MAP §2 의 42 어휘인가, equity 내부 스코프인가 (`price.adj_close` 부류).
FIELD_SCOPES: tuple[str, ...] = ("field_map", "internal")


@dataclass(frozen=True)
class FieldProfile:
    """소비자에게 나가는 필드 하나의 선언 (S19 `dataset_profile` 한 행의 선언 부분).

    **문서가 아니라 이 선언이 정본이다** — `rules_s19` 가 `RULES` 를 훑어 모으고, 랙·PIT·cell
    kind 는 여기서만 온다. `source_stage_tables`·`available_date_basis` 는 소유 `EquityTable` 에서
    기계적으로 유도하고(중복 선언 금지), `coverage_*` 는 실측이라 여기에 없다.
    """

    field_id: str
    columns: tuple[str, ...]          # 소유 테이블(또는 `view_name`)의 컬럼. 둘 이상이면 전부 필요
    label: str
    unit: str                         # 'KRW' · '주' · '%' · '배' · '억원' · '' (무단위·범주)
    value_type: str                   # VALUE_TYPES
    frequency: str                    # FIELD_FREQUENCIES
    recommended_lag_sessions: int | None   # **세션** 정본. None = 랙 미확정(소비 금지)
    recommended_lag_days: int | None       # 병기(FIELD_MAP §1). 어댑터는 세션을 쓴다
    point_in_time: bool
    requires_confirmation: bool
    """소비 전에 **사람이 골라야 할 미결 조건**이 남아 있는가 (엔진
    `DataLoadStatus.CONFIRMATION_REQUIRED` 축). 규율: 다른 프로파일 컬럼으로 이미 표현되는 제약은
    여기에 넣지 않는다 — 짧은 이력은 `coverage_from` 이, 결측 다발은 `estimated_coverage_pct` 가,
    시점 없음은 `point_in_time` 이 말한다. 남는 것은 **단위 미측정 · 산출 규칙 미확정 · 축 선택
    (`src`·합성) · 값의 기준 미공표** 뿐이고, S20 이 이 참값을
    `blocked(partial_support)` 로 옮긴다."""
    disclosure_basis: str              # 언제 공개되는가 (사람 문장)
    evidence: str                      # 어디서 온 값인가 · 판정 근거 (사람 문장)
    coverage_axis: str                 # COVERAGE_AXES
    scope: str = "field_map"           # FIELD_SCOPES
    row_filter: str | None = None      # 값 행을 고르는 SQL 술어 (`consensus_daily` metric 축)
    view_name: str | None = None       # 뷰로 노출되는 필드(`price.adj_close` → v_adj_price_fwd)
    coverage_table: str | None = None  # 커버 측정 테이블. None = 소유 테이블
    coverage_columns: tuple[str, ...] = ()   # 비면 `columns`
    axis_columns: tuple[str, str] | None = None   # 격자 축 (ticker 컬럼, 날짜 컬럼)

    def __post_init__(self) -> None:
        if not self.field_id or "." not in self.field_id:
            raise ValueError(f"field_id must be '<domain>.<name>': got={self.field_id!r}")
        if not self.columns:
            raise ValueError(f"FieldProfile.columns is empty: field_id={self.field_id}")
        for name, value, vocab in (("value_type", self.value_type, VALUE_TYPES),
                                   ("frequency", self.frequency, FIELD_FREQUENCIES),
                                   ("coverage_axis", self.coverage_axis, COVERAGE_AXES),
                                   ("scope", self.scope, FIELD_SCOPES)):
            if value not in vocab:
                raise ValueError(f"{name} outside vocabulary: field_id={self.field_id} "
                                 f"got={value!r} allowed={list(vocab)}")
        for name, lag in (("recommended_lag_sessions", self.recommended_lag_sessions),
                          ("recommended_lag_days", self.recommended_lag_days)):
            if lag is not None and lag < 0:
                raise ValueError(f"{name} must be >= 0: field_id={self.field_id} got={lag}")
        if (self.recommended_lag_sessions is None) != (self.recommended_lag_days is None):
            raise ValueError(f"두 랙 축은 함께 미확정이거나 함께 확정: field_id={self.field_id} "
                             f"sessions={self.recommended_lag_sessions} "
                             f"days={self.recommended_lag_days}")
        if not self.disclosure_basis.strip() or not self.evidence.strip():
            raise ValueError(f"disclosure_basis·evidence 는 비울 수 없다: field_id={self.field_id}")
        if self.coverage_axis in GRID_AXES and self.axis_columns is None:
            raise ValueError(f"{self.coverage_axis} 축은 axis_columns 가 필요하다: "
                             f"field_id={self.field_id}")

    @property
    def measured_table(self) -> str | None:
        """커버율을 재는 테이블. None 이면 소유 `EquityTable.name` 을 쓴다."""
        return self.coverage_table

    @property
    def measured_columns(self) -> tuple[str, ...]:
        return self.coverage_columns or self.columns

    @property
    def column_scope(self) -> str:
        """산출 컬럼 표기 — 둘 이상이면 `,` 로 잇는다(`est_min,est_max`)."""
        return ",".join(self.columns)


@dataclass(frozen=True)
class EquityTable:
    """테이블 1개의 선언. 이 객체가 곧 게이트의 입력이다(stage `TableRule` 의 역할 계승)."""

    name: str
    grain: tuple[str, ...]                      # PK — EG3-P01 유일성 축
    columns: dict[str, str]                     # 산출 컬럼명 → duckdb 타입. 선언 순서 = SELECT 순서
    inputs: tuple[str, ...]                     # 입력 실명 — stage(`stg_*`) 또는 앞선 equity 테이블
    partition_class: str                        # PARTITION_CLASSES
    partition_key_expr: str | None              # `year` 를 만드는 식. whole 이면 None
    available_rule: str                         # EG2 축 설명. AVAILABLE_NONE = 차원 테이블
    eg1_lhs_sql: str                            # EG1 좌변 — 산출(`out_ok`) 위 단일 값
    eg1_rhs_sql: str                            # EG1 우변 — stage 입력 뷰 위 단일 값
    sql_path: Path                              # sql/<table>.sql
    build_by_year: bool = False                 # 연도 파티션 단위 루프 (T7 이후)
    input_columns: dict[str, tuple[str, ...]] = field(default_factory=dict)
    """stage 테이블 → 선언 컬럼. 비어 있으면 전 컬럼 투영. EG0-P02 의 대조축이다."""
    available_basis: tuple[str, ...] = ()       # 이 테이블이 쓰는 basis. 비면 BASIS_VOCAB 전체
    content_date_column: str | None = None      # EG2-P02 `available_date >= 내용일` 축
    reject_reasons: tuple[str, ...] = ()        # `_reject/` 로 나갈 사유 어휘 (EG3 폐쇄)
    consts: tuple[str, ...] = ()                # baseline 에서 `_const` 로 주입할 metric 키
    extra_gates: tuple[ExtraGate, ...] = ()     # 테이블 특화 술어 (EG6·EG8·EG9·EG10~)
    declaration_table: bool = False
    """행수 등식이 정의되지 않는 선언표(`universe_policy`·`dataset_profile`) — EG1 은
    `skip(declaration_table)` (GATES §0-2·§2). 등식 SQL 은 비워 둔다."""
    field_profiles: tuple[FieldProfile, ...] = ()
    """이 테이블이 소비자에게 내는 필드 선언 (S19 `dataset_profile` 의 원천, DESIGN §4-7).
    비어 있으면 축·파생 전용 테이블이다(`security_span`·`trading_calendar`·`universe_policy`…)."""
    declarations: DeclareHook | None = None
    """`.sql` 실행 직전에 선언 레지스트리를 TEMP TABLE 로 올리는 훅(S19·S20 선언표 전용).
    `_const` 와 같은 통로다 — 파이썬 선언을 `.sql` 이 조인으로만 보고, `__main__ gate` 재판정도
    같은 훅을 돌려 빌드와 같은 테이블 위에서 판정한다."""

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("EquityTable.name is empty")
        if not self.columns:
            raise ValueError(f"EquityTable.columns is empty: table={self.name}")
        missing = [g for g in self.grain if g not in self.columns]
        if missing:
            raise ValueError(f"grain column not declared in columns: table={self.name} "
                             f"missing={missing} columns={sorted(self.columns)}")
        if self.partition_class not in PARTITION_CLASSES:
            raise ValueError(f"unknown partition_class: table={self.name} "
                             f"got={self.partition_class!r} allowed={list(PARTITION_CLASSES)}")
        whole = self.partition_class == "whole"
        if whole and self.partition_key_expr is not None:
            raise ValueError(f"whole 파티션은 partition_key_expr 를 갖지 않는다: table={self.name} "
                             f"got={self.partition_key_expr!r}")
        if not whole and self.partition_key_expr is None:
            raise ValueError(f"partition_key_expr is required: table={self.name} "
                             f"partition_class={self.partition_class}")
        bad_basis = [b for b in self.available_basis if b not in BASIS_VOCAB]
        if bad_basis:
            raise ValueError(f"available_basis outside vocabulary: table={self.name} "
                             f"got={bad_basis} allowed={list(BASIS_VOCAB)}")
        if self.content_date_column is not None and self.content_date_column not in self.columns:
            raise ValueError(f"content_date_column not declared: table={self.name} "
                             f"got={self.content_date_column!r} columns={sorted(self.columns)}")
        if self.name in self.inputs:
            raise ValueError(f"table cannot read itself as an input: table={self.name} "
                             f"inputs={list(self.inputs)}")
        unknown_inputs = [t for t in self.input_columns if t not in self.inputs]
        if unknown_inputs:
            raise ValueError(f"input_columns declares a table that is not an input: "
                             f"table={self.name} got={unknown_inputs} inputs={list(self.inputs)}")
        seen: set[str] = set()
        for fp in self.field_profiles:
            if fp.field_id in seen:
                raise ValueError(f"duplicate field_id in one table: table={self.name} "
                                 f"field_id={fp.field_id}")
            seen.add(fp.field_id)
            # 뷰 필드(`view_name`)는 이 테이블의 컬럼이 아니다 — 측정 컬럼만 대조한다.
            check = fp.measured_columns if fp.view_name else fp.columns + fp.measured_columns
            if fp.measured_table is None:
                absent = [c for c in check if c not in self.columns]
                if absent:
                    raise ValueError(f"field_profile column not declared: table={self.name} "
                                     f"field_id={fp.field_id} missing={absent}")

    @property
    def is_fact(self) -> bool:
        """팩트 테이블(EG2 대상)인가. 차원 테이블은 available_rule 이 AVAILABLE_NONE."""
        return self.available_rule != AVAILABLE_NONE

    @property
    def basis_vocab(self) -> tuple[str, ...]:
        return self.available_basis or BASIS_VOCAB

    def declared_columns(self, stage_table: str) -> tuple[str, ...]:
        """그 stage 입력에서 읽겠다고 선언한 컬럼. 선언이 없으면 빈 튜플(= 전 컬럼)."""
        return self.input_columns.get(stage_table, ())


# 레지스트리 — rules_<단계>.py 가 register() 로 채운다 (stage `rules.py:_MODULES` 패턴 계승).
RULES: dict[str, EquityTable] = {}


def register(table: EquityTable) -> EquityTable:
    """RULES 에 등록하고 그대로 돌려준다. 같은 이름 재등록은 오타 신호이므로 거절한다."""
    prior = RULES.get(table.name)
    if prior is not None and prior is not table:
        raise ValueError(f"duplicate equity table registration: table={table.name} "
                         f"already={prior.sql_path} new={table.sql_path}")
    RULES[table.name] = table
    return table
