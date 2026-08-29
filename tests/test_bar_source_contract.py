"""BarSource 어댑터 공통 계약 테스트 (D3).

CSV·KRX parquet·sqlite 세 어댑터가 같은 시나리오에서 같은 LoadResult를 내야 한다.
새 어댑터는 아래 `BUILDERS`에 픽스처 빌더 하나만 추가하면 계약 전체를 통과해야 한다
— 그렇지 않으면 포트 설계를 재검토한다.
"""

from __future__ import annotations

import csv
import sqlite3
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

from backtest_engine.adapters.csv_bars import CsvBarSource
from backtest_engine.adapters.sqlite_bars import SqliteBarSource
from backtest_engine.ports.market_data import BarQuery, BarSource, LoadStatus, OhlcPolicy
from tests.conftest import make_instrument

Row = tuple[date, float, float, float, float, int]  # session, o, h, l, c, v
Rows = dict[str, list[Row]]
Builder = Callable[[Path, Rows], BarSource]

A = make_instrument("005930")
B = make_instrument("000660")


def d(n: int) -> date:
    return date(2026, 8, n)


def build_csv(root: Path, rows: Rows) -> BarSource:
    for symbol, symbol_rows in rows.items():
        with (root / f"{symbol}.csv").open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(("date", "open", "high", "low", "close", "volume"))
            for session, o, h, lo, c, v in symbol_rows:
                writer.writerow((session.isoformat(), o, h, lo, c, v))
    return CsvBarSource(root)


def build_sqlite(root: Path, rows: Rows) -> BarSource:
    path = root / "bars.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE bars (symbol TEXT, session TEXT, open REAL, high REAL, "
            "low REAL, close REAL, volume INTEGER)"
        )
        connection.executemany(
            "INSERT INTO bars VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (symbol, session.isoformat(), o, h, lo, c, v)
                for symbol, symbol_rows in rows.items()
                for session, o, h, lo, c, v in symbol_rows
            ],
        )
    return SqliteBarSource(path)


def build_krx(root: Path, rows: Rows) -> BarSource:
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    from backtest_engine.adapters.krx_parquet import KOSPI_TRADES_FILE, KrxParquetBarSource

    flat = [
        (symbol, session, o, h, lo, c, v)
        for symbol, symbol_rows in rows.items()
        for session, o, h, lo, c, v in symbol_rows
    ]
    table = pa.table(
        {
            "bas_dd": pa.array([r[1] for r in flat], type=pa.date32()),
            "isu_cd": pa.array([r[0] for r in flat], type=pa.string()),
            "tdd_opnprc": pa.array([int(r[2]) for r in flat], type=pa.int64()),
            "tdd_hgprc": pa.array([int(r[3]) for r in flat], type=pa.int64()),
            "tdd_lwprc": pa.array([int(r[4]) for r in flat], type=pa.int64()),
            "tdd_clsprc": pa.array([int(r[5]) for r in flat], type=pa.int64()),
            "acc_trdvol": pa.array([r[6] for r in flat], type=pa.int64()),
        }
    )
    pq.write_table(table, root / KOSPI_TRADES_FILE)
    return KrxParquetBarSource(root)


BUILDERS: dict[str, Builder] = {"csv": build_csv, "sqlite": build_sqlite, "krx_parquet": build_krx}


@pytest.fixture(params=sorted(BUILDERS), ids=sorted(BUILDERS))
def build(request: pytest.FixtureRequest, tmp_path: Path) -> Callable[[Rows], BarSource]:
    builder = BUILDERS[request.param]
    return lambda rows: builder(tmp_path, rows)


NORMAL: list[Row] = [
    (d(1), 100.0, 110.0, 95.0, 105.0, 1_000),
    (d(2), 105.0, 112.0, 101.0, 110.0, 1_200),
    (d(3), 110.0, 115.0, 108.0, 109.0, 900),
]


def test_loads_sorted_bars_with_values(build: Callable[[Rows], BarSource]) -> None:
    result = build({"005930": NORMAL}).load_bars(BarQuery(instruments=(A,)))
    assert result.ok, result.detail
    assert [(b.ts.day, b.open, b.high, b.low, b.close, b.volume) for b in result.bars] == [
        (1, 100.0, 110.0, 95.0, 105.0, 1_000),
        (2, 105.0, 112.0, 101.0, 110.0, 1_200),
        (3, 110.0, 115.0, 108.0, 109.0, 900),
    ]
    assert all(b.instrument == A for b in result.bars)
    assert (result.repaired_rows, result.dropped_rows) == (0, 0)


def test_date_range_is_inclusive(build: Callable[[Rows], BarSource]) -> None:
    result = build({"005930": NORMAL}).load_bars(BarQuery(instruments=(A,), start=d(2), end=d(3)))
    assert result.ok, result.detail
    assert [b.ts.day for b in result.bars] == [2, 3]


def test_multi_instrument_query_merges_all(build: Callable[[Rows], BarSource]) -> None:
    result = build({"005930": NORMAL, "000660": NORMAL[:2]}).load_bars(BarQuery(instruments=(A, B)))
    assert result.ok, result.detail
    assert {(b.instrument.symbol, b.ts.day) for b in result.bars} == {
        ("005930", 1),
        ("005930", 2),
        ("005930", 3),
        ("000660", 1),
        ("000660", 2),
    }


def test_missing_instrument_fails_whole_query(build: Callable[[Rows], BarSource]) -> None:
    result = build({"005930": NORMAL}).load_bars(BarQuery(instruments=(A, B)))
    assert result.status is LoadStatus.NO_DATA
    assert result.bars == ()
    assert "000660" in (result.detail or "")


def test_duplicate_session_is_format_error(build: Callable[[Rows], BarSource]) -> None:
    result = build({"005930": [NORMAL[0], NORMAL[0]]}).load_bars(BarQuery(instruments=(A,)))
    assert result.status is LoadStatus.FORMAT_ERROR
    assert "005930" in (result.detail or "")


def test_strict_rejects_ohlc_violation(build: Callable[[Rows], BarSource]) -> None:
    bad: Row = (d(1), 100.0, 90.0, 95.0, 105.0, 1_000)  # high < open
    result = build({"005930": [bad]}).load_bars(
        BarQuery(instruments=(A,), ohlc_policy=OhlcPolicy.STRICT)
    )
    assert result.status is LoadStatus.FORMAT_ERROR


def test_clamp_drops_non_positive_price_rows_and_reports(
    build: Callable[[Rows], BarSource],
) -> None:
    halt: Row = (d(2), 0.0, 0.0, 0.0, 0.0, 0)
    result = build({"005930": [NORMAL[0], halt, NORMAL[2]]}).load_bars(
        BarQuery(instruments=(A,), ohlc_policy=OhlcPolicy.CLAMP)
    )
    assert result.ok, result.detail
    assert [b.ts.day for b in result.bars] == [1, 3]
    assert result.dropped_rows == 1


# --- sqlite 고유 -----------------------------------------------------------------


def test_sqlite_missing_table_is_no_data(tmp_path: Path) -> None:
    path = tmp_path / "empty.sqlite"
    sqlite3.connect(path).close()
    result = SqliteBarSource(path).load_bars(BarQuery(instruments=(A,)))
    assert result.status is LoadStatus.NO_DATA
    assert "bars" in (result.detail or "")


def test_sqlite_missing_file_is_no_data(tmp_path: Path) -> None:
    result = SqliteBarSource(tmp_path / "nope.sqlite").load_bars(BarQuery(instruments=(A,)))
    assert result.status is LoadStatus.NO_DATA


def test_sqlite_bad_session_text_is_format_error(tmp_path: Path) -> None:
    path = tmp_path / "bars.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE bars (symbol TEXT, session TEXT, open REAL, high REAL, "
            "low REAL, close REAL, volume INTEGER)"
        )
        connection.execute("INSERT INTO bars VALUES ('005930', 'not-a-date', 1, 1, 1, 1, 1)")
    result = SqliteBarSource(path).load_bars(BarQuery(instruments=(A,)))
    assert result.status is LoadStatus.FORMAT_ERROR
    assert "not-a-date" in (result.detail or "")
