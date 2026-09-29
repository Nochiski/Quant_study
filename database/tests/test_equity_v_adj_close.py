"""`v_adj_close` — 원장이 그날 사건을 접지 못한 적용일 행의 수정주가를 결측으로 낸다 (#220).

수정주가(`price_adj_daily`, S23)는 공개 전 계수를 접지 않는다(fold_date = greatest(apply, available)).
그래서 계수가 적용일 다음 세션에 공개되면 적용일 하루는 원주가가 이미 사건 뒤 척도인데 누적 계수는
사건 전이라 스파이크가 서고, `krx_base_inconsistent`(기준가는 바뀌었는데 계수를 못 낸 사건)는 적용일에
층이 영구히 바뀐다. 뷰는 그 적용일 행만 결측으로 내고 나머지 행은 표 값 그대로 둔다. 적용일의 가격
불연속은 그날 가격 데이터에 이미 보이므로 공개일과 무관하게 가린다(값은 바꾸지 않는다).
"""
from __future__ import annotations

from datetime import date, timedelta

import duckdb
from equity import views

_DAYS = tuple(d for d in (date(2024, 1, 1) + timedelta(days=n) for n in range(20)) if d.weekday() < 5)
_LAST = _DAYS[-1]
# (티커, 적용일 index, 공개일 index, factor_ok, factor_source)
_EVENTS = (
    ("LATE", 3, 4, True, "mktcap_neutral"),               # 늦게 공개된 ok 계수 — 적용일 스파이크
    ("ONTIME", 3, 3, True, "mktcap_neutral"),             # 적용일에 공개·접힌 계수 — 공백 없음
    ("INCON", 5, 6, False, "krx_base_inconsistent"),      # 계수를 못 낸 기준가 재설정 — 층 이동
    ("RIGHTS", 5, 6, False, "unknown_price_only"),        # MVP 밖 기준가 변화 — 가리지 않는다
    ("NOMINAL", 5, 5, False, "no_price_match"),           # 명목일 사건 — 가리지 않는다
)


def _connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("CREATE TEMP TABLE price_adj_daily (ticker VARCHAR, date DATE, adj_close DOUBLE, "
                "available_date DATE)")
    con.executemany("INSERT INTO price_adj_daily VALUES (?, ?, ?, ?)",
                    [(e[0], d, 100.0 + i, d) for e in _EVENTS for i, d in enumerate(_DAYS)])
    con.execute("CREATE TEMP TABLE adj_factor (ticker VARCHAR, apply_date DATE, "
                "available_date DATE, factor_ok BOOLEAN, factor_source VARCHAR)")
    con.executemany("INSERT INTO adj_factor VALUES (?, ?, ?, ?, ?)",
                    [(t, _DAYS[a], _DAYS[v], ok, src) for t, a, v, ok, src in _EVENTS])
    made = views.install_temp_macros(
        con, {"price_adj_daily": "price_adj_daily", "adj_factor": "adj_factor"})
    assert "v_adj_close" in made
    return con


def _gaps(con: duckdb.DuckDBPyConnection, as_of: date = _LAST) -> dict[str, list[date]]:
    rows = con.execute("SELECT ticker, date, adj_close, adj_gap FROM v_adj_close(?) "
                       "ORDER BY ticker, date", [as_of]).fetchall()
    for ticker, d, adj, gap in rows:
        # 가린 행만 결측이고, 나머지 행은 표 값 그대로다
        assert (adj is None) == gap and (gap or adj == 100.0 + _DAYS.index(d)), (ticker, d)
    return {e[0]: [d for t, d, _, gap in rows if t == e[0] and gap] for e in _EVENTS}


def test_늦은_계수와_계수를_못_낸_기준가_재설정의_적용일_행만_가린다() -> None:
    assert _gaps(_connect()) == {
        "LATE": [_DAYS[3]], "ONTIME": [], "INCON": [_DAYS[5]], "RIGHTS": [], "NOMINAL": []}


def test_as_of_는_행만_자르고_가림은_공개일과_무관하다() -> None:
    """적용일 당일 as_of 에도 가린다 — 공개일(다음 세션)을 기다리지 않는다(값을 바꾸지 않는 결측이다)."""
    con = _connect()
    assert _gaps(con, _DAYS[3])["LATE"] == [_DAYS[3]]
    assert max(r[0] for r in con.execute(
        "SELECT date FROM v_adj_close(?)", [_DAYS[3]]).fetchall()) == _DAYS[3]
