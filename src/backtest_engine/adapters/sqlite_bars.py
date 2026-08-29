"""sqlite 어댑터: 표준 라이브러리 `sqlite3`로 읽는 BarSource (외부 DB 채널의 대표).

스키마 (기본 테이블 `bars`):
    symbol TEXT, session TEXT(ISO-8601 날짜), open REAL, high REAL, low REAL,
    close REAL, volume INTEGER

정렬은 SQL `ORDER BY session`, 정제는 `data.cleaning.clean_raw_bars` 공용이다.
파일·테이블 없음과 요청 종목 없음은 NO_DATA, 파싱 실패와 중복 세션은 FORMAT_ERROR.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

from backtest_engine.data.cleaning import RawBar, clean_raw_bars, merge_results
from backtest_engine.ports.market_data import BarQuery, LoadResult, LoadStatus
from backtest_engine.types.instruments import InstrumentId

_SELECT = (
    "SELECT session, open, high, low, close, volume FROM {table} "
    "WHERE symbol = ? {range_clause} ORDER BY session"
)


class SqliteBarSource:
    """sqlite 파일 하나의 테이블을 BarSource로 노출한다.

    Args:
        path: sqlite 파일 경로. 없으면 NO_DATA (sqlite3는 기본적으로 파일을 만들어 버리므로
            열기 전에 존재를 확인한다).
        table: Bar 테이블 이름. 기본 `bars`.
    """

    def __init__(self, path: Path, table: str = "bars") -> None:
        if not table.isidentifier():
            raise ValueError(f"table name must be an identifier — table={table!r}")
        self._path = path
        self._table = table

    def load_bars(self, query: BarQuery) -> LoadResult:
        if not self._path.exists():
            return LoadResult(
                bars=(),
                status=LoadStatus.NO_DATA,
                detail=f"sqlite file not found — path={self._path}",
            )
        with sqlite3.connect(f"file:{self._path}?mode=ro", uri=True) as connection:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (self._table,)
            ).fetchone()
            if exists is None:
                return LoadResult(
                    bars=(),
                    status=LoadStatus.NO_DATA,
                    detail=f"table not found — path={self._path} table={self._table}",
                )
            return merge_results(
                self._load_one(connection, instrument, query) for instrument in query.instruments
            )

    def _load_one(
        self, connection: sqlite3.Connection, instrument: InstrumentId, query: BarQuery
    ) -> LoadResult:
        clauses: list[str] = []
        params: list[object] = [instrument.symbol]
        if query.start is not None:
            clauses.append("AND session >= ?")
            params.append(query.start.isoformat())
        if query.end is not None:
            clauses.append("AND session <= ?")
            params.append(query.end.isoformat())
        sql = _SELECT.format(table=self._table, range_clause=" ".join(clauses))
        records = connection.execute(sql, params).fetchall()
        if not records:
            return LoadResult(
                bars=(),
                status=LoadStatus.NO_DATA,
                detail=(
                    f"no rows for instrument — symbol={instrument.symbol} path={self._path} "
                    f"table={self._table} start={query.start} end={query.end}"
                ),
            )
        rows: list[RawBar] = []
        for index, record in enumerate(records):
            try:
                session = date.fromisoformat(str(record[0]))
                rows.append(
                    RawBar(
                        ts=datetime.combine(session, datetime.min.time()),
                        open=float(record[1]),
                        high=float(record[2]),
                        low=float(record[3]),
                        close=float(record[4]),
                        volume=int(record[5]),
                        origin=f"table={self._table} row={index} session={record[0]!r}",
                    )
                )
            except (TypeError, ValueError) as error:
                return LoadResult(
                    bars=(),
                    status=LoadStatus.FORMAT_ERROR,
                    detail=(
                        f"invalid row — path={self._path} table={self._table} "
                        f"symbol={instrument.symbol} row={index} record={record!r} error={error!r}"
                    ),
                )
        return clean_raw_bars(
            rows, instrument, query.ohlc_policy, source=f"{self._path}:{self._table}"
        )
