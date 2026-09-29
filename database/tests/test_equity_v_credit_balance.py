"""`v_credit_balance` — 무상증자 척도 창의 신용잔고를 결측으로 가린다 (#249).

무상증자에서 원천(KIS) 신용잔고 주식수는 권리락일부터 옛 단위(권리락 전 융자)와 새 단위(권리락 뒤
융자)가 섞이고, 상장주식수는 신주 상장일에야 바뀐다. 그래서 잔고 ÷ 상장주식수가 그 사이 부풀었다가
상장일에 꺾인다. 척도를 되돌릴 계수가 없으므로 뷰가 권리락일부터 `BONUS_SCALE_WINDOW_SESSIONS`
세션의 잔고를 결측으로 낸다. 가림 판단은 공시 접수일 **다음** 행부터만 쓴다(PIT) — 공시 전에는
가릴 근거가 없다.
"""
from __future__ import annotations

from datetime import date, timedelta

import duckdb
from equity import views

_K = views.BONUS_SCALE_WINDOW_SESSIONS
_SESSIONS = tuple(
    d for d in (date(2022, 1, 3) + timedelta(days=n) for n in range(120)) if d.weekday() < 5)
_EX = 10  # 권리락일의 세션 index
_LAST = _SESSIONS[-1]


def _connect(events: list[tuple[str, str, date, date]],
             cells: dict[tuple[str, date], tuple[int | None, str]]) -> duckdb.DuckDBPyConnection:
    """`events` 는 (ticker, event_type, 권리락일, 공시일). 잔고는 전 세션 measured 1,000 + index
    이고 `cells` 가 (ticker, date) 별로 (값, fill_kind.kind) 를 덮는다."""
    con = duckdb.connect()
    con.execute("CREATE TEMP TABLE trading_calendar (date DATE)")
    con.executemany("INSERT INTO trading_calendar VALUES (?)", [(d,) for d in _SESSIONS])
    con.execute("CREATE TEMP TABLE corp_event (ticker VARCHAR, event_type VARCHAR, "
                "effective_date DATE, available_date DATE)")
    con.executemany("INSERT INTO corp_event VALUES (?, ?, ?, ?)", events)
    con.execute("CREATE TEMP TABLE credit_daily (date DATE, ticker VARCHAR, "
                "whol_loan_rmnd_stcn_shr DECIMAL(10,0), "
                "fill_kind STRUCT(kind VARCHAR, evidence VARCHAR), available_date DATE, "
                "available_basis VARCHAR)")
    rows = []
    for ticker in sorted({e[0] for e in events} | {key[0] for key in cells}):
        for i, d in enumerate(_SESSIONS):
            value, kind = cells.get((ticker, d), (1_000 + i, "measured"))
            rows.append((d, ticker, value, kind, d))
    con.executemany("INSERT INTO credit_daily SELECT ?, ?, ?, "
                    "struct_pack(kind := ?, evidence := 'unit_ok'), ?, 'default'", rows)
    made = views.install_temp_macros(
        con, {"credit_daily": "credit_daily", "corp_event": "corp_event",
              "trading_calendar": "trading_calendar"})
    assert "v_credit_balance" in made
    return con


def _rows(con: duckdb.DuckDBPyConnection, ticker: str, as_of: date = _LAST
          ) -> dict[date, tuple[object, ...]]:
    """date → (잔고, fill_kind.kind, fill_kind.evidence, bonus_window)."""
    return {r[0]: r[1:] for r in con.execute(
        "SELECT date, whol_loan_rmnd_stcn_shr, fill_kind.kind, fill_kind.evidence, bonus_window "
        "FROM v_credit_balance(?) WHERE ticker = ? ORDER BY date", [as_of, ticker]).fetchall()}


def _masked(rows: dict[date, tuple[object, ...]]) -> list[date]:
    return [d for d, (_, _, _, window) in rows.items() if window]


def test_권리락일부터_창_길이만큼_잔고를_가리고_창_밖은_값을_둔다() -> None:
    con = _connect([("A", "bonus", _SESSIONS[_EX], _SESSIONS[0])], {})
    rows = _rows(con, "A")
    window = _SESSIONS[_EX:_EX + _K]
    assert _masked(rows) == list(window)  # 거래일로 센다 — 주말은 창 길이에 들지 않는다
    for d in window:
        # 값만 지우고 수집 로그 사실(evidence)은 둔다 — 잔고 이상 격리 셀과 같은 어휘다
        assert rows[d][:3] == (None, "empty_response", "unit_ok"), d
    for i in (_EX - 1, _EX + _K):
        assert rows[_SESSIONS[i]] == (1_000 + i, "measured", "unit_ok", False), i


def test_공시_전_세션은_가리지_않는다() -> None:
    """권리락일 뒤에 공시된 사건(정기보고서 회고·정정본)은 공시 다음 행부터만 가린다."""
    filed = _EX + 3
    con = _connect([("B", "bonus", _SESSIONS[_EX], _SESSIONS[filed])], {})
    rows = _rows(con, "B")
    assert _masked(rows) == list(_SESSIONS[filed + 1:_EX + _K])
    for i in range(_EX, filed + 1):  # 공시일 당일도 아직 쓸 수 없다(접수 시각 미제공)
        assert rows[_SESSIONS[i]][0] == 1_000 + i


def test_무상증자가_아닌_사건과_원래_값이_없던_셀은_그대로다() -> None:
    inside = _SESSIONS[_EX + 1]
    con = _connect([("C", "split", _SESSIONS[_EX], _SESSIONS[0]),
                    ("D", "bonus", _SESSIONS[_EX], _SESSIONS[0])],
                   {("D", inside): (None, "not_collected")})
    assert _masked(_rows(con, "C")) == []
    # 값이 원래 없던 셀은 결측 사유를 바꾸지 않는다 — '안 물어봤다' 를 '물었는데 없다' 로 덮지
    # 않는다
    assert _rows(con, "D")[inside] == (None, "not_collected", "unit_ok", True)


def test_as_of_는_행만_자르고_가림은_바꾸지_않는다() -> None:
    con = _connect([("A", "bonus", _SESSIONS[_EX], _SESSIONS[0])], {})
    cut = _SESSIONS[_EX + 5]
    early = _rows(con, "A", cut)
    assert max(early) == cut
    assert early == {d: v for d, v in _rows(con, "A").items() if d <= cut}
