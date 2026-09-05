"""survey v2 — 전수 어휘 측정(캐스트 금지)·커버리지 판정·(p,s) 유도. 손계산 픽스처."""
import sqlite3
from pathlib import Path

import duckdb
import pytest
import survey_v2 as sv


def _make_sqlite(path: Path, ddl: str, rows: list[tuple[object, ...]], insert: str) -> None:
    con = sqlite3.connect(path)
    con.execute(ddl)
    con.executemany(insert, rows)
    con.commit()
    con.close()


@pytest.fixture
def ledger(tmp_path: Path) -> Path:
    db = tmp_path / "fx.db"
    _make_sqlite(
        db,
        "CREATE TABLE nums (amt TEXT)",
        [("1,234",), ("-56.78",), ("",), ("-",), ("+9",), ("abc",), ("0",), (None,)],
        "INSERT INTO nums VALUES (?)",
    )
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE dates (d8 TEXT, iso TEXT, dot TEXT, kor TEXT)")
    con.executemany(
        "INSERT INTO dates VALUES (?,?,?,?)",
        [
            ("20240101", "2024-01-01", "2013.04.01", "2016년 01월 08일"),
            ("20240102", "2024-01-02", "2013.04.02", "2016년 01월 09일"),
            ("", None, "-", ""),
        ],
    )
    con.execute("CREATE TABLE shapes (v TEXT)")
    con.executemany("INSERT INTO shapes VALUES (?)", [("20240101",), ("20240102",), ("abc",)])
    con.execute("CREATE INDEX ix_shapes ON shapes(v)")
    con.commit()
    con.close()
    return db


@pytest.fixture
def con(ledger: Path) -> duckdb.DuckDBPyConnection:
    c = duckdb.connect()
    sv.attach_sqlite(c, "fx", ledger)
    return c


def test_list_ledger_tables_returns_only_user_tables(ledger: Path) -> None:
    assert sv.list_ledger_tables(ledger) == ["dates", "nums", "shapes"]


def test_list_columns_uses_pragma_order(ledger: Path) -> None:
    assert sv.list_columns(ledger, "dates") == ["d8", "iso", "dot", "kor"]


def test_profile_counts_missing_markers_signs_and_digits(con: duckdb.DuckDBPyConnection) -> None:
    prof = sv.profile_table(con, "fx", "nums", ["amt"])
    s = prof.columns["amt"]
    assert prof.n_rows == 8
    assert s.n_null == 1
    assert s.n_blank == 1
    assert s.n_dash == 1
    assert s.n_zero == 1
    assert s.n_comma == 1  # '1,234'
    assert s.n_neg == 1  # '-56.78' ('-' 단독은 결측 마커)
    assert s.n_plus == 1  # '+9'
    assert s.n_nonnum == 1  # 'abc'
    assert s.max_int_digits == 4  # 1234
    assert s.max_scale == 2  # .78
    assert s.max_len == 6  # '-56.78'


def test_profile_counts_date_shapes(con: duckdb.DuckDBPyConnection) -> None:
    prof = sv.profile_table(con, "fx", "dates", ["d8", "iso", "dot", "kor"])
    assert prof.columns["d8"].n_ymd8 == 2
    assert prof.columns["iso"].n_iso == 2
    assert prof.columns["dot"].n_dot == 2
    assert prof.columns["kor"].n_kor == 2
    assert prof.columns["d8"].n_blank == 1
    assert prof.columns["iso"].n_null == 1
    assert prof.columns["dot"].n_dash == 1


def test_profile_top_patterns_collapse_digits_and_letters(con: duckdb.DuckDBPyConnection) -> None:
    prof = sv.profile_table(con, "fx", "shapes", ["v"])
    assert prof.columns["v"].patterns == {"99999999": 2, "AAA": 1}


def test_derive_ps_numeric_column() -> None:
    s = sv.ColumnStats(
        n_null=0, n_blank=0, n_dash=0, n_zero=0, n_comma=1, n_neg=1, n_plus=0, n_nonnum=0,
        max_int_digits=4, max_scale=2, max_len=6, n_ymd8=0, n_iso=0, n_dot=0, n_kor=0,
        patterns={}, n_rows=10,
    )
    ps = sv.derive_ps(s)
    assert ps.kind == "numeric"
    assert ps.precision == 6
    assert ps.scale == 2
    assert ps.has_sign is True
    assert ps.has_comma is True


def test_derive_ps_text_when_nonnumeric_present() -> None:
    s = sv.ColumnStats(
        n_null=0, n_blank=0, n_dash=0, n_zero=0, n_comma=0, n_neg=0, n_plus=0, n_nonnum=3,
        max_int_digits=8, max_scale=0, max_len=12, n_ymd8=0, n_iso=0, n_dot=0, n_kor=0,
        patterns={}, n_rows=10,
    )
    ps = sv.derive_ps(s)
    assert ps.kind == "text"
    assert ps.precision is None


def test_derive_ps_date_when_every_present_value_is_yyyymmdd() -> None:
    s = sv.ColumnStats(
        n_null=1, n_blank=2, n_dash=0, n_zero=0, n_comma=0, n_neg=0, n_plus=0, n_nonnum=0,
        max_int_digits=8, max_scale=0, max_len=8, n_ymd8=7, n_iso=0, n_dot=0, n_kor=0,
        patterns={}, n_rows=10,
    )
    assert sv.derive_ps(s).kind == "date_yyyymmdd"


def test_coverage_reports_missing_table_and_column() -> None:
    expected = {"krx": {"a": ["x", "y"], "b": ["z"]}}
    surveyed = {"krx": {"a": ["x"]}}
    r = sv.check_coverage(expected, surveyed)
    assert r.ok is False
    assert r.missing_tables == ["krx.b"]
    assert r.missing_columns == ["krx.a.y"]


def test_coverage_ok_when_everything_surveyed() -> None:
    expected = {"krx": {"a": ["x", "y"]}}
    r = sv.check_coverage(expected, {"krx": {"a": ["y", "x"]}})
    assert r.ok is True
    assert r.missing_tables == []
    assert r.missing_columns == []


def test_survey_ledgers_profiles_every_table_and_passes_coverage(
    ledger: Path, tmp_path: Path
) -> None:
    r = sv.survey_ledgers({"fx": ledger}, tmp_path / "out")
    assert r.status is sv.RunStatus.OK
    assert r.n_tables == 3
    assert r.coverage.ok is True
    assert r.ps["fx.nums"]["amt"].kind == "text"  # 'abc' 1행 때문에 숫자 아님
    assert r.ps["fx.dates"]["d8"].kind == "date_yyyymmdd"


def test_survey_ledgers_table_filter_breaks_coverage(ledger: Path, tmp_path: Path) -> None:
    r = sv.survey_ledgers({"fx": ledger}, tmp_path / "out", only_tables={"nums"})
    assert r.status is sv.RunStatus.INCOMPLETE
    assert r.coverage.missing_tables == ["fx.dates", "fx.shapes"]
