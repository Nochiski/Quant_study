"""`v_fin_latest` 의 TTM 창 규칙 — 연속 4분기가 공개일 기준으로 다 보일 때만 선다 (#212).

워크벤치 `financial.net_income` 등 흐름 계정 필드는 이 뷰의 `ttm_*` 를 그대로 낸다. 그래서 TTM 을
세우는 조건(창 4행 · 창 폭 3분기 · 창 안 모든 공개일 ≤ 이 행 공개일)이 곧 필드 값의 PIT 계약이다.
여기서는 절단본 체인 없이 손으로 만든 `fin_std` 몇 행으로 그 조건만 잰다.

창 폭: 연속 4분기의 첫·끝 `period_end` 간격은 273~276일이다(분기 말일 차이). 분기 하나가 빠지면
4행 창이 5분기에 걸쳐 365·366일이 된다. 실원장(2026-09-18 카탈로그)의 4행 창 폭은 240~280일
84,155행과 365일 이상 923행으로 갈리고 그 사이 값은 없다 — 상한 400일은 누락 창 289행을 TTM 으로
통과시켰다.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import duckdb
import pytest
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


def _row(corp: str, period_end: date, available: date, net_income: int,
         q4_derived: int | None = None) -> tuple[object, ...]:
    """순이익만 채운 fin_std 1행. 사업보고서(11011)는 4분기 파생값을 `q4_derived` 로 받는다."""
    report = _REPORT_BY_MONTH[period_end.month]
    return (corp, period_end, report, "CFS", str(period_end.year), f"R{corp}{period_end}",
            None, "KRW", None, "standard", None, None, net_income, None, None, None, None, None,
            None, None, None, q4_derived, available, "derived", "api_restated")


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


def _ttm(con: duckdb.DuckDBPyConnection, corp: str, period_end: date) -> Decimal | None:
    row = con.execute(
        "SELECT ttm_net_income FROM v_fin_latest(DATE '2023-12-29') "
        "WHERE corp_code = ? AND period_end = ?", [corp, period_end]).fetchone()
    assert row is not None, f"v_fin_latest 에 행이 없다 — corp={corp} period_end={period_end}"
    return row[0]


def test_연속_4분기가_다_보이면_TTM_은_분기_합이고_연간_행은_연간과_같다() -> None:
    con = _connect([
        _row("C1", date(2022, 3, 31), date(2022, 5, 16), 10),
        _row("C1", date(2022, 6, 30), date(2022, 8, 16), 11),
        _row("C1", date(2022, 9, 30), date(2022, 11, 14), 12),
        _row("C1", date(2022, 12, 31), date(2023, 3, 20), 46, q4_derived=13),
        _row("C1", date(2023, 3, 31), date(2023, 5, 15), 14),
    ])
    assert _ttm(con, "C1", date(2022, 12, 31)) == 46          # 10 + 11 + 12 + 13 = 연간
    assert _ttm(con, "C1", date(2023, 3, 31)) == 50           # 11 + 12 + 13 + 14
    assert _ttm(con, "C1", date(2022, 9, 30)) is None         # 앞 분기가 셋뿐 — 부분합 금지


def test_분기_하나가_빠진_창은_TTM_을_세우지_않는다() -> None:
    """2023 1분기가 없다 — 2023 반기 행의 4행 창이 2022 반기~2023 반기(365일)에 걸친다."""
    con = _connect([
        _row("C2", date(2022, 6, 30), date(2022, 8, 16), 11),
        _row("C2", date(2022, 9, 30), date(2022, 11, 14), 12),
        _row("C2", date(2022, 12, 31), date(2023, 3, 20), 46, q4_derived=13),
        _row("C2", date(2023, 6, 30), date(2023, 8, 14), 15),
    ])
    assert _ttm(con, "C2", date(2023, 6, 30)) is None


def test_창_안_분기가_이_행보다_늦게_공개되면_TTM_을_세우지_않는다() -> None:
    """정정 재제출로 옛 분기가 나중에 접수되면 그 전 행의 TTM 은 PIT 로 설 수 없다."""
    con = _connect([
        _row("C3", date(2022, 6, 30), date(2022, 8, 16), 11),
        _row("C3", date(2022, 9, 30), date(2022, 11, 14), 12),
        _row("C3", date(2022, 12, 31), date(2023, 3, 20), 46, q4_derived=13),
        _row("C3", date(2023, 3, 31), date(2023, 9, 1), 14),   # 1분기 정정본이 반기보다 늦다
        _row("C3", date(2023, 6, 30), date(2023, 8, 14), 15),
    ])
    assert _ttm(con, "C3", date(2023, 6, 30)) is None
    assert _ttm(con, "C3", date(2023, 3, 31)) == 50           # 정정 접수일 기준으로는 선다


@pytest.mark.parametrize("span", [273, 274, 275, 276])
def test_창_폭_상한은_정상_연속_4분기를_모두_받는다(span: int) -> None:
    assert views.TTM_SPAN_MIN_DAYS <= span <= views.TTM_SPAN_MAX_DAYS
    assert views.TTM_SPAN_MAX_DAYS < 365       # 분기 하나가 빠진 창(365일 이상)은 받지 않는다
