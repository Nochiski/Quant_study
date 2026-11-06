"""duckdb 어댑터의 개별종목차트 행(`ChartPricePort`) — 종목·지수 목록과 일봉.

원장 행만 나른다. 검색 순위·주봉·월봉 묶기·표시 배율·분석은 `application.stock_chart`와
`domain.chart_analysis`가 한다. 표는 부팅 때 고정한 판본(`TableBuild.parquet_source`)으로 읽는다.

- 목록: `security`(티커·화면 이름 = 약칭, 없으면 정식 이름·증권 종류)와 `index_daily`의 (시장 구분,
  지수명). 같은 이름의 업종 지수가 코스피·코스닥에 하나씩 있어 지수 코드는
  `<index_class>:<index_name>` 이다. `price_adj_daily`가 없으면 종목은 목록에 없다(그릴 가격이
  없다).
- 종목 일봉: `price_adj_daily`의 전방 조정 OHLC를 `security.list_date`(지금 상장일) 이후로 읽는다 —
  같은 티커를 쓰던 옛 종목의 가격을 섞지 않는다. `price_daily.price_kind =
  'reference'`(기준가·정지일)와 `basis <> 'krx'`(저녁 잠정) 행, 가격이 빈 행은 봉이 없는 날이라
  싣지 않는다. 같은 행의 `price_daily.close`(원주가)가 마지막 실제 종가다.
- 지수 일봉: `index_daily`의 `open_idx/high_idx/low_idx/close_idx`. 원장은 옛 구간에서 시가·고가·
  저가를 비우거나 0으로 싣는다 — 둘 다 그날 값이 없다는 뜻이라 봉으로 싣지 않는다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from strategy_workbench.application.stock_chart.facade.ports import (
    ChartHistory,
    ChartInstrument,
    InstrumentKind,
    InstrumentRef,
)
from strategy_workbench.domain.chart_analysis.facade.bars import PriceBar

from ._adapter import CONFIRMED_BASIS, REFERENCE_KIND, EquityDuckdbAdapter, _as_date, _open
from ._specs import ADJ_TABLE, INDEX_TABLE, PRICE_TABLE, SECURITY_TABLE

# 지수 코드 `<index_class>:<index_name>`의 구분자. 시장 구분(KOSPI·KOSDAQ)에는 없는 글자다.
INDEX_CODE_SEP = ":"


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        raise TypeError(f"{label} must be numeric — got {type(value).__name__}: {value!r}")
    return float(value)


def _columns(adapter: EquityDuckdbAdapter, table: str) -> set[str]:
    con = _open(None)
    try:
        rows = con.execute(
            f"DESCRIBE SELECT * FROM {adapter._source(table)}"  # noqa: SLF001  # reason: 같은 노드의 판본 고정표
        ).fetchall()
    finally:
        con.close()
    return {str(row[0]) for row in rows}


def chart_instruments(adapter: EquityDuckdbAdapter) -> tuple[ChartInstrument, ...]:
    tables = adapter._tables  # noqa: SLF001  # reason: 같은 노드의 판본 고정표
    items: list[ChartInstrument] = []
    con = _open(None)
    try:
        if SECURITY_TABLE in tables and ADJ_TABLE in tables:
            columns = _columns(adapter, SECURITY_TABLE)
            full = "name_current" if "name_current" in columns else "NULL"
            short = "name_abbrv_current" if "name_abbrv_current" in columns else "NULL"
            rows = con.execute(
                f"SELECT ticker, {short}, {full}, sec_type "
                f"FROM {adapter._source(SECURITY_TABLE)} ORDER BY ticker"  # noqa: SLF001
            ).fetchall()
            for ticker, short_name, full_name, sec_type in rows:
                name = str(short_name or full_name or ticker)
                aliases = (str(full_name),) if full_name and str(full_name) != name else ()
                items.append(
                    ChartInstrument(
                        InstrumentRef(InstrumentKind.STOCK, str(ticker)),
                        name,
                        None if sec_type is None else str(sec_type),
                        aliases,
                    )
                )
        if INDEX_TABLE in tables:
            rows = con.execute(
                "SELECT DISTINCT index_class, index_name "
                f"FROM {adapter._source(INDEX_TABLE)} ORDER BY 1, 2"  # noqa: SLF001
            ).fetchall()
            items += [
                ChartInstrument(
                    InstrumentRef(InstrumentKind.INDEX, f"{index_class}{INDEX_CODE_SEP}{name}"),
                    str(name),
                    str(index_class),
                )
                for index_class, name in rows
            ]
    finally:
        con.close()
    return tuple(items)


def chart_history(
    adapter: EquityDuckdbAdapter, instrument: InstrumentRef, end: date
) -> ChartHistory | None:
    if instrument.kind is InstrumentKind.INDEX:
        return _index_history(adapter, instrument.code, end)
    return _stock_history(adapter, instrument.code, end)


def _index_history(adapter: EquityDuckdbAdapter, code: str, end: date) -> ChartHistory | None:
    index_class, sep, name = code.partition(INDEX_CODE_SEP)
    if not sep or INDEX_TABLE not in adapter._tables:  # noqa: SLF001
        return None
    con = _open(None)
    try:
        rows = con.execute(
            "SELECT date, open_idx, high_idx, low_idx, close_idx "
            f"FROM {adapter._source(INDEX_TABLE)} "  # noqa: SLF001
            "WHERE index_class = ? AND index_name = ? AND date <= ? "
            "AND open_idx > 0 AND high_idx > 0 AND low_idx > 0 AND close_idx > 0 ORDER BY date",
            [index_class, name, end],
        ).fetchall()
        known = (
            rows
            or con.execute(
                f"SELECT 1 FROM {adapter._source(INDEX_TABLE)} "  # noqa: SLF001
                "WHERE index_class = ? AND index_name = ? LIMIT 1",
                [index_class, name],
            ).fetchall()
        )
    finally:
        con.close()
    if not known:
        return None
    return ChartHistory(tuple(_bar(row, INDEX_TABLE) for row in rows), None)


def _stock_history(adapter: EquityDuckdbAdapter, ticker: str, end: date) -> ChartHistory | None:
    tables = adapter._tables  # noqa: SLF001
    if SECURITY_TABLE not in tables or ADJ_TABLE not in tables:
        return None
    security = _columns(adapter, SECURITY_TABLE)
    basis = "a.basis" if "basis" in _columns(adapter, ADJ_TABLE) else f"'{CONFIRMED_BASIS}'"
    listed = "list_date" if "list_date" in security else "NULL::DATE"
    con = _open(None)
    try:
        found = con.execute(
            f"SELECT {listed} FROM {adapter._source(SECURITY_TABLE)} WHERE ticker = ?",  # noqa: SLF001
            [ticker],
        ).fetchall()
        if not found:
            return None
        rows = con.execute(
            "SELECT a.date, a.adj_open, a.adj_high, a.adj_low, a.adj_close, p.close "
            f"FROM {adapter._source(ADJ_TABLE)} a "  # noqa: SLF001
            f"JOIN {adapter._source(PRICE_TABLE)} p "  # noqa: SLF001
            "  ON p.ticker = a.ticker AND p.date = a.date "
            "WHERE a.ticker = ? AND a.date <= ? AND (? IS NULL OR a.date >= ?) "
            f"  AND p.price_kind IS DISTINCT FROM '{REFERENCE_KIND}' "
            f"  AND {basis} = '{CONFIRMED_BASIS}' "
            "  AND a.adj_open IS NOT NULL AND a.adj_high IS NOT NULL "
            "  AND a.adj_low IS NOT NULL AND a.adj_close IS NOT NULL AND p.close IS NOT NULL "
            "ORDER BY a.date",
            [ticker, end, found[0][0], found[0][0]],
        ).fetchall()
    finally:
        con.close()
    bars = tuple(_bar(row, ADJ_TABLE) for row in rows)
    last_close = _number(rows[-1][5], "price_daily.close") if rows else None
    return ChartHistory(bars, last_close)


def _bar(row: tuple[object, ...], table: str) -> PriceBar:
    return PriceBar(
        session=_as_date(row[0], f"{table}.date"),
        open=_number(row[1], f"{table}.open"),
        high=_number(row[2], f"{table}.high"),
        low=_number(row[3], f"{table}.low"),
        close=_number(row[4], f"{table}.close"),
    )
