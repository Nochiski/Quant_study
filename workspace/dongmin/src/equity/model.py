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

    from stage.gates import GateResult

    from .gates import EquityGateContext

    ExtraGate = Callable[[EquityGateContext], GateResult]

RULES_VERSION = "e1.1.0"                # BuildRecord.rules_version 에 실린다

# DESIGN §1 — stage 4종 + equity 신설 convention
BASIS_VOCAB: tuple[str, ...] = ("measured", "derived", "convention", "default", "unknown")
# DESIGN §3 — 격자 테이블의 fill_kind STRUCT(kind, evidence)
FILL_KINDS: tuple[str, ...] = ("measured", "src_omitted", "empty_response", "not_collected")
FILL_EVIDENCE: tuple[str, ...] = ("shard_done", "unit_ok", "shard_empty", "unit_empty", "none")
# DESIGN §2 — 물리 파티션 축
PARTITION_CLASSES: tuple[str, ...] = ("date_axis", "receipt_axis", "whole")

AVAILABLE_NONE = "none"                 # 차원 테이블 — available_date 를 부여하지 않는다


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
