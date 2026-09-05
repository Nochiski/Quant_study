"""테스트 전용 샘플 선언 — T0 인프라의 build→gate→commit 왕복을 돌리기 위한 최소 테이블 1개.

실제 1단계 테이블(`trading_calendar`·`corp`·`security`…)은 T1 부터 `rules_master.py` 에
등록한다. 이 모듈은 그때 지우지 말 것 — 인프라 회귀 테스트가 계속 이 테이블을 쓴다.
"""
from __future__ import annotations

from pathlib import Path

from .model import EquityTable, register

SQL_DIR = Path(__file__).parent / "sql"

SAMPLE_TABLE = register(EquityTable(
    name="sample_table",
    grain=("k",),
    columns={"k": "BIGINT", "val": "VARCHAR", "available_date": "DATE",
             "available_basis": "VARCHAR"},
    inputs=("stg_sample",),
    partition_class="whole",
    partition_key_expr=None,
    available_rule="column:available_date — 가짜 stage 입력이 그대로 실어 준다",
    eg1_lhs_sql="SELECT count(*) FROM out_pq",
    eg1_rhs_sql="SELECT count(*) FROM stg_sample",
    sql_path=SQL_DIR / "sample_table.sql",
    input_columns={"stg_sample": ("k", "val", "available_date", "available_basis")},
    available_basis=("measured",),
    reject_reasons=("off_grid",),
))

TABLES: tuple[EquityTable, ...] = (SAMPLE_TABLE,)
