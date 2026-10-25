"""T1.1 — v3 `quant.db` 9표 DDL 고정 (플랜 `2026-09-24-v3-merge.md` §5 T1.1).

`src/compat/v3_schema.sql` 을 빈 sqlite 에 적용하고 `PRAGMA table_info` 로 읽은 컬럼 목록·타입·PK 를
**아래 리터럴**과 대조한다. 리터럴은 v3 스냅샷에서 직접 베껴 적었다:
  `backend/db/schema.py:4-14`(stocks) · `16-29`(daily_prices) · `31-49`(investor_detail_flows) ·
  `51-68`(consensus_annual) · `70-84`(consensus_revision_daily) ·
  `86-98`(consensus_revision_compare) ·
  `100-121`(financial_summary) · `155-167`(score_history) · `169-187`(score_history_v2)
  + `backend/db/migration_sql.py:3-72`(ALTER 로 붙은 열 — **ALTER 실행 순서가 곧 컬럼 순서**다).

이 테스트가 깨지면 둘 중 하나다: ① 우리 DDL 이 틀렸다 ② v3 가 마이그레이션으로 스키마를 바꿨다.
②라면 서버 `quant.db` 의 `PRAGMA table_info` 와 대조해 원인을 확정한 뒤에만 리터럴을 고친다
(플랜 §7 위험표 2행).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "src" / "compat" / "v3_schema.sql"

# ── v3 원본에서 베껴 적은 (컬럼명, 선언타입) 목록 ────────────────────────────────
_REAL = "REAL"
_INT = "INTEGER"
_TEXT = "TEXT"
_CODE = "TEXT(6)"


def _cols(*pairs: tuple[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(pairs)


def _compare_cols() -> tuple[tuple[str, str], ...]:
    """schema.py:89-97 — 9지표 × (1w, 1m, 3m, 1y). DDL 이 지표-major 순서로 적혀 있다."""
    out: list[tuple[str, str]] = []
    for metric, typ in (("opinion", _REAL), ("revenue", _REAL), ("op", _REAL), ("ni", _REAL),
                        ("eps", _INT), ("per", _REAL), ("bps", _INT), ("pbr", _REAL),
                        ("roe", _REAL)):
        out.extend((f"{metric}_{h}", typ) for h in ("1w", "1m", "3m", "1y"))
    return tuple(out)


EXPECTED: dict[str, tuple[tuple[tuple[str, str], ...], tuple[str, ...]]] = {
    # 표 이름 → (컬럼 (이름, 타입) 순서, PK 순서)
    "stocks": (
        _cols(("stock_code", _CODE), ("stock_name", _TEXT), ("market", _TEXT), ("sector", _TEXT),
              ("market_cap", _INT), ("listed_date", _TEXT), ("is_active", _INT),
              ("delisted_date", _TEXT), ("updated_at", _TEXT)),
        ("stock_code",),
    ),
    "daily_prices": (
        _cols(("stock_code", _CODE), ("trade_date", _TEXT), ("open", _INT), ("high", _INT),
              ("low", _INT), ("close", _INT), ("volume", _INT), ("amount", _INT),
              ("adj_close", _REAL)),
        ("stock_code", "trade_date"),
    ),
    "investor_detail_flows": (
        _cols(("stock_code", _CODE), ("trade_date", _TEXT), ("individual", _INT),
              ("foreign_investor", _INT), ("institution_total", _INT),
              ("financial_investment", _INT), ("insurance", _INT), ("investment_trust", _INT),
              ("etc_financial", _INT), ("bank", _INT), ("pension_fund", _INT),
              ("private_equity", _INT), ("nation", _INT), ("etc_corporation", _INT)),
        ("stock_code", "trade_date"),
    ),
    "consensus_annual": (
        _cols(("stock_code", _CODE), ("period", _TEXT), ("period_type", _TEXT),
              ("data_type", _TEXT), ("revenue", _REAL), ("yoy", _REAL), ("op", _REAL),
              ("ni", _REAL), ("eps", _INT), ("bps", _INT), ("per", _REAL), ("pbr", _REAL),
              ("roe", _REAL), ("ev_ebitda", _REAL), ("accounting_standard", _TEXT)),
        ("stock_code", "period", "period_type"),
    ),
    "consensus_revision_daily": (
        _cols(("stock_code", _CODE), ("base_date", _TEXT), ("target_period", _TEXT),
              ("opinion", _REAL), ("revenue", _REAL), ("op", _REAL), ("ni", _REAL),
              ("eps", _INT), ("per", _REAL), ("bps", _INT), ("pbr", _REAL), ("roe", _REAL),
              # migration_sql.py:30
              ("collected_date", _TEXT)),
        ("stock_code", "base_date"),
    ),
    "consensus_revision_compare": (
        _cols(("stock_code", _CODE), ("target_period", _TEXT)) + _compare_cols(),
        ("stock_code",),
    ),
    "financial_summary": (
        _cols(("stock_code", _CODE), ("period", _TEXT), ("period_type", _TEXT),
              ("revenue", _INT), ("op", _INT), ("ni", _INT), ("eps", _INT), ("bps", _INT),
              ("per", _REAL), ("pbr", _REAL), ("roe", _REAL), ("roa", _REAL),
              ("debt_ratio", _REAL), ("fcf", _INT), ("capex", _INT), ("op_margin", _REAL),
              ("ni_margin", _REAL), ("dividend_yield", _REAL), ("shares", _INT),
              # migration_sql.py:4-7 → 18-19 (ALTER 순서)
              ("ev_ebitda", _REAL), ("yoy", _REAL), ("data_type", _TEXT),
              ("accounting_standard", _TEXT), ("gross_profit", _INT), ("total_assets", _INT)),
        ("stock_code", "period", "period_type"),
    ),
    "score_history": (
        _cols(("stock_code", _CODE), ("score_date", _TEXT), ("momentum_score", _REAL),
              ("revision_score", _REAL), ("flow_score", _REAL), ("valuation_score", _REAL),
              ("composite_score", _REAL), ("rank", _INT),
              # migration_sql.py:9-15
              ("quality_score", _REAL), ("growth_score", _REAL), ("sentiment_score", _REAL),
              ("volatility_score", _REAL), ("size_score", _REAL), ("foreign_score", _REAL),
              ("shareholder_score", _REAL),
              # migration_sql.py:32-58 — raw 서브팩터
              ("r1m", _REAL), ("r3m", _REAL), ("r6m", _REAL), ("r9m", _REAL), ("r12m", _REAL),
              ("op_change_1w", _REAL), ("ni_change_1w", _REAL), ("op_change_1m", _REAL),
              ("ni_change_1m", _REAL), ("op_change_3m", _REAL), ("ni_change_3m", _REAL),
              ("flow_inst_5d", _REAL), ("flow_inst_20d", _REAL), ("flow_for_5d", _REAL),
              ("flow_for_20d", _REAL), ("flow_pe_5d", _REAL), ("flow_pe_20d", _REAL),
              ("qual_gpa", _REAL), ("qual_roa", _REAL), ("qual_fcf_assets", _REAL),
              ("qual_debt_ratio", _REAL), ("qual_gpa_change", _REAL), ("qual_std_20d", _REAL),
              ("val_per", _REAL), ("val_pbr", _REAL), ("val_ev_ebitda", _REAL),
              ("val_dividend_yield", _REAL),
              # migration_sql.py:60-65 — 흑전/적전 flag
              ("op_1w_flag", _TEXT), ("ni_1w_flag", _TEXT), ("op_1m_flag", _TEXT),
              ("ni_1m_flag", _TEXT), ("op_3m_flag", _TEXT), ("ni_3m_flag", _TEXT)),
        ("stock_code", "score_date"),
    ),
    "score_history_v2": (
        _cols(("stock_code", _CODE), ("score_date", _TEXT), ("momentum_score", _REAL),
              ("growth_score", _REAL), ("flow_score", _REAL), ("value_score", _REAL),
              ("total_score", _REAL), ("rank", _INT), ("r1m", _REAL), ("r3m", _REAL),
              ("r6m", _REAL), ("op_yoy_cur", _REAL), ("op_yoy_next", _REAL),
              ("ni_yoy_cur", _REAL), ("ni_yoy_next", _REAL), ("inst_5d", _REAL),
              ("inst_20d", _REAL), ("frgn_5d", _REAL), ("frgn_20d", _REAL),
              ("per_cur", _REAL), ("per_next", _REAL)),
        ("stock_code", "score_date"),
    ),
}


@pytest.fixture
def conn(tmp_path: Path):
    c = sqlite3.connect(str(tmp_path / "quant.db"))
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    yield c
    c.close()


def test_schema_creates_exactly_the_nine_v3_tables(conn: sqlite3.Connection) -> None:
    """9표만 만든다 — `_compat_meta` 는 exporter 몫이라 이 파일에 없다."""
    got = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    assert got == set(EXPECTED)


@pytest.mark.parametrize("table", sorted(EXPECTED))
def test_columns_and_pk_match_v3(conn: sqlite3.Connection, table: str) -> None:
    want_cols, want_pk = EXPECTED[table]
    info = conn.execute(f"PRAGMA table_info({table})").fetchall()
    assert tuple((r[1], r[2]) for r in info) == want_cols
    pk = tuple(r[1] for r in sorted((r for r in info if r[5] > 0), key=lambda r: r[5]))
    assert pk == want_pk


def test_score_history_column_counts() -> None:
    """플랜 §5 T1.1 2 — score_history 48열 · score_history_v2 21열."""
    assert len(EXPECTED["score_history"][0]) == 48
    assert len(EXPECTED["score_history_v2"][0]) == 21


def test_query_indexes_present(conn: sqlite3.Connection) -> None:
    """v3 인덱스 중 조회에 쓰는 넷(schema.py:29·49·167·187)."""
    got = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'")}
    assert got == {"idx_dp_date_code", "idx_idf_date", "idx_sh_date_score", "idx_shv2_date_score"}


def test_without_rowid_matches_v3(conn: sqlite3.Connection) -> None:
    """v3 DDL 에서 rowid 표는 `stocks`·`consensus_revision_compare` 둘뿐이다(schema.py:14·98)."""
    sql = {r[0]: r[1] for r in conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table'")}
    rowid_tables = {"stocks", "consensus_revision_compare"}
    for table in rowid_tables:
        assert "WITHOUT ROWID" not in sql[table], table
    for table in set(EXPECTED) - rowid_tables:
        assert "WITHOUT ROWID" in sql[table], table
