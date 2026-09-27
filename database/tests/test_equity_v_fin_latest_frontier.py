"""`v_fin_latest.period_frontier` — 세션 컷오프에서 고를 행은 "가장 최근 회계기간" 이다 (#225).

restated 판본은 정정 재제출이 있으면 그 기간 행의 `available_date` 가 정정 접수일로 밀린다. 그래서
옛 기간 정정본이 더 늦은 기간보다 늦게 접수될 수 있고(실원장 2026-09-18 카탈로그 기준 수천 행),
"가장 늦게 접수된 행" 을 고르면 셀이 옛 기간으로 되돌아간다.

`period_frontier` 는 "이 행의 공개일에 이미 보이던 행 중 이 행이 가장 최근 회계기간인가" 다. 소비자가
이 열이 참인 행만 공개일 순으로 보고 컷오프 이하 마지막 행을 고르면, 그 행은 컷오프까지 공개된
행 중 가장 최근 기간 행과 같다. 판정에 쓰는 것은 그 행의 공개일 이하에 공개된 행뿐이라 look-ahead 가
없고, 뷰의 `as_of` 를 늦춰도 이미 보이던 행의 판정은 바뀌지 않는다.
"""
from __future__ import annotations

from datetime import date, timedelta

import duckdb
from equity import views

_FIN_COLUMNS = (
    "corp_code VARCHAR, period_end DATE, report_code VARCHAR, fs_div VARCHAR, bsns_year VARCHAR, "
    "rcept_no VARCHAR, period_start DATE, currency VARCHAR, revenue DECIMAL(24,4), "
    "revenue_basis VARCHAR, gross_profit DECIMAL(24,4), op_profit DECIMAL(24,4), "
    "net_income DECIMAL(24,4), total_asset DECIMAL(24,4), total_liab DECIMAL(24,4), "
    "total_equity DECIMAL(24,4), cf_operating_ytd DECIMAL(24,4), cf_operating_q DECIMAL(24,4), "
    "revenue_q4_derived DECIMAL(24,4), gross_profit_q4_derived DECIMAL(24,4), "
    "op_profit_q4_derived DECIMAL(24,4), net_income_q4_derived DECIMAL(24,4), "
    "available_date DATE, available_basis VARCHAR, vintage_kind VARCHAR"
)
_REPORT_BY_MONTH = {3: "11013", 6: "11012", 9: "11014", 12: "11011"}
_AS_OF = date(2023, 12, 29)


def _row(corp: str, period_end: date, available: date) -> tuple[object, ...]:
    """총자산만 채운 fin_std 1행. 값은 기간을 알아볼 수 있게 `YYYYMM` 으로 둔다."""
    report = _REPORT_BY_MONTH[period_end.month]
    return (corp, period_end, report, "CFS", str(period_end.year), f"R{corp}{period_end}",
            None, "KRW", None, "standard", None, None, None,
            period_end.year * 100 + period_end.month, None, None, None, None,
            None, None, None, None, available, "derived", "api_restated")


def _connect(rows: list[tuple[object, ...]]) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(f"CREATE TEMP TABLE fin_std ({_FIN_COLUMNS})")
    con.executemany(f"INSERT INTO fin_std VALUES ({', '.join('?' * 25)})", rows)
    con.execute("CREATE TEMP TABLE disclosure_version (rcept_no VARCHAR, first_correction_dt DATE)")
    start = date(2021, 1, 1)
    days = [start + timedelta(days=n) for n in range(365 * 3)]
    con.execute("CREATE TEMP TABLE trading_calendar (date DATE)")
    con.executemany("INSERT INTO trading_calendar VALUES (?)",
                    [(d,) for d in days if d.weekday() < 5])
    made = views.install_temp_macros(
        con, {"fin_std": "fin_std", "disclosure_version": "disclosure_version",
              "trading_calendar": "trading_calendar"})
    assert "v_fin_latest" in made
    return con


def _frontier(con: duckdb.DuckDBPyConnection, corp: str, as_of: date = _AS_OF,
              ) -> dict[date, bool]:
    rows = con.execute(
        "SELECT period_end, period_frontier FROM v_fin_latest(?) WHERE corp_code = ?",
        [as_of, corp]).fetchall()
    return {period_end: frontier for period_end, frontier in rows}


def _pick_at(con: duckdb.DuckDBPyConnection, corp: str, cutoff: date) -> date | None:
    """소비자 규칙 — frontier 행만 공개일 순으로 보고 컷오프 이하 마지막 행(같은 날은 최근 기간)."""
    row = con.execute(
        "SELECT period_end FROM v_fin_latest(?) WHERE corp_code = ? AND period_frontier "
        "AND available_date <= ? ORDER BY available_date DESC, period_end DESC LIMIT 1",
        [_AS_OF, corp, cutoff]).fetchone()
    return None if row is None else row[0]


def _latest_period_at(con: duckdb.DuckDBPyConnection, corp: str, cutoff: date) -> date | None:
    row = con.execute(
        "SELECT max(period_end) FROM v_fin_latest(?) WHERE corp_code = ? AND available_date <= ?",
        [_AS_OF, corp, cutoff]).fetchone()
    return None if row is None else row[0]


_LATE_CORRECTION = [
    _row("C1", date(2022, 12, 31), date(2023, 3, 20)),
    _row("C1", date(2023, 6, 30), date(2023, 8, 14)),
    _row("C1", date(2023, 3, 31), date(2023, 9, 1)),   # 1분기 정정본이 반기보다 늦다
    _row("C1", date(2023, 9, 30), date(2023, 11, 14)),
]


def test_옛_기간_정정본이_늦게_접수되면_그_행은_frontier_가_아니다() -> None:
    con = _connect(_LATE_CORRECTION)
    assert _frontier(con, "C1") == {
        date(2022, 12, 31): True,
        date(2023, 6, 30): True,
        date(2023, 3, 31): False,
        date(2023, 9, 30): True,
    }


def test_정정_접수일_이후에도_셀은_반기_기간을_유지한다() -> None:
    """이슈 인풋 — 반기(08-14) 뒤 1분기 정정본(09-01)이 와도 다음 정기보고서까지 반기다."""
    con = _connect(_LATE_CORRECTION)
    assert _pick_at(con, "C1", date(2023, 8, 31)) == date(2023, 6, 30)
    assert _pick_at(con, "C1", date(2023, 9, 1)) == date(2023, 6, 30)
    assert _pick_at(con, "C1", date(2023, 11, 13)) == date(2023, 6, 30)
    assert _pick_at(con, "C1", date(2023, 11, 14)) == date(2023, 9, 30)


def test_같은_날_여러_기간이_접수되면_최근_기간만_frontier_다() -> None:
    """정정 일괄 재제출 — 한 접수일에 두 기간이 실리면 최근 기간 행 하나만 남는다."""
    con = _connect([
        _row("C2", date(2022, 12, 31), date(2023, 3, 20)),
        _row("C2", date(2023, 3, 31), date(2023, 6, 1)),
        _row("C2", date(2023, 6, 30), date(2023, 6, 1)),
    ])
    assert _frontier(con, "C2") == {
        date(2022, 12, 31): True,
        date(2023, 3, 31): False,
        date(2023, 6, 30): True,
    }


def test_같은_기간_같은_날_보고서가_둘이면_보고서_종류가_큰_행만_frontier_다() -> None:
    """#233 리뷰 P3-1 — 결산월 변경 전후로 같은 period_end 에 보고서 종류가 둘인 경우(실원장 1건).

    frontier 키는 (period_end, report_code) 다. report_code 를 키에서 빼면 두 행이 다 frontier 가
    되어, 어댑터의 `pick_order`(report_code DESC)가 아니라 뷰 판정만으로는 한 행이 서지 않는다.
    """
    annual = _row("C5", date(2022, 12, 31), date(2023, 3, 20))
    third = (*annual[:2], "11014", *annual[3:5], "RC5Q3", *annual[6:])
    con = _connect([annual, third])
    rows = con.execute(
        "SELECT report_code, period_frontier FROM v_fin_latest(?) WHERE corp_code = 'C5'",
        [_AS_OF]).fetchall()
    assert dict(rows) == {"11011": False, "11014": True}


def test_어느_컷오프에서도_고른_행은_그때까지_공개된_가장_최근_기간이다() -> None:
    con = _connect(_LATE_CORRECTION)
    day = date(2023, 3, 1)
    while day <= _AS_OF:
        assert _pick_at(con, "C1", day) == _latest_period_at(con, "C1", day), day
        day += timedelta(days=1)


def test_frontier_판정은_뷰_as_of_를_늦춰도_바뀌지_않는다() -> None:
    """이미 보이던 행의 판정은 그 뒤에 들어온 행에 기대지 않는다 — look-ahead 가 없다."""
    con = _connect(_LATE_CORRECTION)
    later = _frontier(con, "C1")
    for as_of in (date(2023, 8, 14), date(2023, 9, 1), date(2023, 11, 14)):
        early = _frontier(con, "C1", as_of)
        assert early == {period: later[period] for period in early}, as_of
