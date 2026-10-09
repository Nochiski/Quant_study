"""`v_fin_latest` 의 TTM 창 규칙 — 연속 4분기가 다 공개된 날부터 선다 (#212·#238).

워크벤치 `financial.net_income` 등 흐름 계정 필드는 이 뷰의 `ttm_*` 를 낸다. 그래서 TTM 을 세우는
조건(창 4행 · 창 폭 3분기)과 TTM 의 공개일(창 안 분기값 공개일의 max —
`ttm_income_available_date`·`ttm_cf_available_date`)이 곧 필드 값의 PIT 계약이다.
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
    "available_date DATE, available_basis VARCHAR, vintage_kind VARCHAR, "
    "q4_derived_available_date DATE, cf_q_available_date DATE"
)
_N_COLUMNS = 27
_REPORT_BY_MONTH = {3: "11013", 6: "11012", 9: "11014", 12: "11011"}


def _fin(corp: str, period_end: date, available: date, *, report: str | None = None,
         net_income: int | None = None, q4_derived: int | None = None,
         q4_available: date | None = None, cf_ytd: int | None = None,
         cf_q: int | None = None, cf_q_available: date | None = None,
         revenue: int | None = None, revenue_q4: int | None = None,
         revenue_basis: str = "standard",
         fs_div: str = "CFS") -> tuple[object, ...]:
    """fin_std 1행. `report` 를 안 주면 12월 결산으로 보고 분기말 달에서 보고서 종류를 정한다.

    `q4_derived`·`cf_q` 와 두 파생 공개일은 fin_std(S12)가 `bsns_year` 로 만든 파생 블록이다 —
    뷰가 그것을 쓰면 비12월 결산에서 다른 회계연도 분기가, 창 밖 분기의 늦은 정정이 새어 든다.
    """
    report = report or _REPORT_BY_MONTH[period_end.month]
    return (corp, period_end, report, fs_div, str(period_end.year), f"R{corp}{period_end}",
            None, "KRW", revenue, revenue_basis, None, None, net_income, None, None, None,
            cf_ytd, cf_q, revenue_q4, None, None, q4_derived, available, "derived", "api_restated",
            q4_available, cf_q_available)


def _row(corp: str, period_end: date, available: date, net_income: int,
         q4_derived: int | None = None) -> tuple[object, ...]:
    """순이익만 채운 fin_std 1행. 사업보고서(11011)는 4분기 파생값을 `q4_derived` 로 받는다."""
    return _fin(corp, period_end, available, net_income=net_income, q4_derived=q4_derived)


def _connect(rows: list[tuple[object, ...]]) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(f"CREATE TEMP TABLE fin_std ({_FIN_COLUMNS})")
    con.executemany(f"INSERT INTO fin_std VALUES ({', '.join('?' * _N_COLUMNS)})", rows)
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


def _ttm(con: duckdb.DuckDBPyConnection, corp: str, period_end: date,
         column: str = "ttm_net_income") -> Decimal | date | None:
    row = con.execute(
        f"SELECT {column} FROM v_fin_latest(DATE '2023-12-29') "
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
    # 창이 행 공개일에 완성됐으면 TTM 공개일은 행 공개일과 같다
    assert _ttm(con, "C1", date(2023, 3, 31), "ttm_income_available_date") == date(2023, 5, 15)
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


def test_창_안_분기가_이_행보다_늦게_공개되면_TTM_은_그_공개일에_선다() -> None:
    """정정 재제출로 옛 분기가 나중에 접수되면 그 전 행의 TTM 은 그 접수일에 완성된다(#238).

    행 공개일에는 알 수 없었으므로 공개일 열이 정정 접수일을 가리킨다 — 소비자는 그날부터 쓴다.
    다른 기간 값으로 대신하는 것이 아니라 같은 기간 TTM 이 늦게 서는 것이다.
    """
    con = _connect([
        _row("C3", date(2022, 3, 31), date(2022, 5, 16), 10),    # 2022 4분기 = 46 − (10+11+12)
        _row("C3", date(2022, 6, 30), date(2022, 8, 16), 11),
        _row("C3", date(2022, 9, 30), date(2022, 11, 14), 12),
        _row("C3", date(2022, 12, 31), date(2023, 3, 20), 46, q4_derived=13),
        _row("C3", date(2023, 3, 31), date(2023, 9, 1), 14),   # 1분기 정정본이 반기보다 늦다
        _row("C3", date(2023, 6, 30), date(2023, 8, 14), 15),
    ])
    assert _ttm(con, "C3", date(2023, 6, 30)) == 54           # 12 + 13 + 14 + 15
    assert _ttm(con, "C3", date(2023, 6, 30), "ttm_income_available_date") == date(2023, 9, 1)
    assert _ttm(con, "C3", date(2023, 3, 31)) == 50           # 정정 접수일 기준으로는 선다


@pytest.mark.parametrize("span", [273, 274, 275, 276])
def test_창_폭_상한은_정상_연속_4분기를_모두_받는다(span: int) -> None:
    assert views.TTM_SPAN_MIN_DAYS <= span <= views.TTM_SPAN_MAX_DAYS
    assert views.TTM_SPAN_MAX_DAYS < 365       # 분기 하나가 빠진 창(365일 이상)은 받지 않는다


# ── #227 리뷰: 뷰가 fin_std 의 `bsns_year` 파생 블록을 쓰지 않고 회계기간 축으로 4분기를 세운다 ──


def test_비12월_결산은_같은_회계연도_분기로_4분기를_세워_연간_행_TTM_이_연간과_같다() -> None:
    """3월 결산 — fin_std 의 q4 파생·cf 분기값은 `bsns_year` 조인이라 다음 회계연도 분기로 만들어진다.

    리뷰 P1-1(018500): 사업보고서 행 TTM 이 결산월 3·6·8·9월 법인 전건에서 연간과 달랐다. 뷰는
    창 직전 3행(1분기·반기·3분기 보고서)으로 4분기를 직접 만들어야 한다. 파생 블록의 엉뚱한 값
    (q4 1·7, cf 분기 99)은 결과에 나오면 안 된다.
    """
    march = [
        _fin("M3", date(2021, 6, 30), date(2021, 8, 13), report="11013", net_income=9, cf_ytd=5),
        _fin("M3", date(2021, 9, 30), date(2021, 11, 12), report="11012", net_income=10,
             cf_ytd=11, cf_q=99),
        _fin("M3", date(2021, 12, 31), date(2022, 2, 14), report="11014", net_income=11,
             cf_ytd=18, cf_q=99),
        _fin("M3", date(2022, 3, 31), date(2022, 6, 20), report="11011", net_income=40,
             q4_derived=1, q4_available=date(2023, 2, 14), cf_ytd=26, cf_q=99),
        _fin("M3", date(2022, 6, 30), date(2022, 8, 12), report="11013", net_income=12, cf_ytd=6),
        _fin("M3", date(2022, 9, 30), date(2022, 11, 14), report="11012", net_income=13,
             cf_ytd=13, cf_q=99),
        _fin("M3", date(2022, 12, 31), date(2023, 2, 14), report="11014", net_income=14,
             cf_ytd=21, cf_q=99),
        _fin("M3", date(2023, 3, 31), date(2023, 6, 20), report="11011", net_income=50,
             q4_derived=7, q4_available=date(2023, 11, 14), cf_ytd=30, cf_q=99),
    ]
    con = _connect(march)
    assert _ttm(con, "M3", date(2022, 3, 31)) == 40                    # 9 + 10 + 11 + (40 − 30)
    assert _ttm(con, "M3", date(2023, 3, 31)) == 50                    # 연간 행 = 연간
    assert _ttm(con, "M3", date(2022, 6, 30)) == 10 + 11 + 10 + 12     # 창에 든 4분기 = 10
    assert _ttm(con, "M3", date(2022, 3, 31), "ttm_cf_operating") == 26
    assert _ttm(con, "M3", date(2023, 3, 31), "ttm_cf_operating") == 30
    assert _ttm(con, "M3", date(2022, 12, 31), "ttm_cf_operating") == 6 + 8 + 7 + 8


def test_창_밖_분기의_늦은_정정은_4분기_파생을_거쳐_TTM_공개일을_늦춘다() -> None:
    """12월 결산 — 전년 1·2분기 정정본이 당해 반기보다 늦게 접수됐다(리뷰 P1-2, 091970).

    2022 반기 행의 창 [2021 3분기, 2021 사업보고서, 2022 1분기, 2022 반기]는 행 공개일이 다
    2022-08-16 이하다. 그러나 2021 4분기 = 연간 − (1·2·3분기)라 창 밖 1·2분기의 값(2023-11-17
    접수)에 기댄다. 그 창의 TTM 은 반기 공개일에 알 수 없었다 — 값은 서지만 공개일 열이 정정
    접수일을 가리켜 그 전에는 새어 들지 못한다(#238).
    """
    late = date(2023, 11, 17)
    con = _connect([
        _fin("L2", date(2021, 3, 31), late, net_income=10),
        _fin("L2", date(2021, 6, 30), late, net_income=11),
        _fin("L2", date(2021, 9, 30), date(2021, 11, 15), net_income=12),
        _fin("L2", date(2021, 12, 31), date(2022, 3, 21), net_income=46, q4_derived=13,
             q4_available=late),
        _fin("L2", date(2022, 3, 31), date(2022, 5, 16), net_income=14),
        _fin("L2", date(2022, 6, 30), date(2022, 8, 16), net_income=15),
    ])
    assert _ttm(con, "L2", date(2022, 6, 30)) == 12 + 13 + 14 + 15
    assert _ttm(con, "L2", date(2022, 6, 30), "ttm_income_available_date") == late


def test_창_밖_반기의_늦은_정정은_현금흐름_분기_차분을_거쳐_TTM_공개일을_늦춘다() -> None:
    """3분기 영업현금 분기값 = 3분기 누계 − 반기 누계. 반기가 늦게 접수되면 그 분기값도 그때 선다."""
    late = date(2023, 11, 17)
    con = _connect([
        _fin("L3", date(2021, 6, 30), late, cf_ytd=30),
        _fin("L3", date(2021, 9, 30), date(2021, 11, 15), cf_ytd=45, cf_q=15,
             cf_q_available=late),
        _fin("L3", date(2021, 12, 31), date(2022, 3, 21), cf_ytd=60, cf_q=15),
        _fin("L3", date(2022, 3, 31), date(2022, 5, 16), cf_ytd=16, cf_q=16),
        _fin("L3", date(2022, 6, 30), date(2022, 8, 16), cf_ytd=34, cf_q=18),
    ])
    assert _ttm(con, "L3", date(2022, 6, 30), "ttm_cf_operating") == 15 + 15 + 16 + 18
    assert _ttm(con, "L3", date(2022, 6, 30), "ttm_cf_available_date") == late


def test_3분기_행은_창의_가장_오래된_사업보고서_4분기가_늦게_서면_손익_공개일만_늦춘다() -> None:
    """리뷰 #300 P2-1·P3-1 — 3분기 행의 창 [전년 사업보고서, 1분기, 반기, 3분기].

    전년 1분기 정정본(2022-12-01)이 3분기 행보다 늦게 접수됐다. 전년 4분기 = 연간 − 1~3분기라
    그날을 싣는 것은 창의 가장 오래된 행뿐이다 — 공개일 창이 그 행을 빼면 손익 TTM 이 3분기
    공개일부터 새어 든다(실원장 순이익 628행). 영업현금 4분기는 전년 3분기 누계에만 기대므로
    현금 공개일은 행 공개일 그대로다 — 공개일 열이 둘인 이유다.
    """
    late = date(2022, 12, 1)
    con = _connect([
        _fin("P1", date(2021, 3, 31), late, net_income=10, cf_ytd=10),
        _fin("P1", date(2021, 6, 30), date(2021, 8, 16), net_income=11, cf_ytd=21),
        _fin("P1", date(2021, 9, 30), date(2021, 11, 15), net_income=12, cf_ytd=33),
        _fin("P1", date(2021, 12, 31), date(2022, 3, 21), net_income=46, cf_ytd=46),
        _fin("P1", date(2022, 3, 31), date(2022, 5, 16), net_income=14, cf_ytd=14),
        _fin("P1", date(2022, 6, 30), date(2022, 8, 16), net_income=15, cf_ytd=29),
        _fin("P1", date(2022, 9, 30), date(2022, 11, 14), net_income=16, cf_ytd=45),
    ])
    assert _ttm(con, "P1", date(2022, 9, 30)) == 13 + 14 + 15 + 16
    assert _ttm(con, "P1", date(2022, 9, 30), "ttm_income_available_date") == late
    assert _ttm(con, "P1", date(2022, 9, 30), "ttm_cf_operating") == 13 + 14 + 15 + 16
    assert _ttm(con, "P1", date(2022, 9, 30), "ttm_cf_available_date") == date(2022, 11, 14)


def test_1분기와_반기_행은_전년_사업보고서가_늦게_정정되면_현금_공개일도_그날로_늦춘다() -> None:
    """리뷰 #300 P2-1·r2 P2-1 — 전년 사업보고서 정정본(2022-09-01)이 1분기·반기 행보다 늦게 접수됐다.

    1분기 분기값은 누계 그대로라 사업보고서에 기대지 않는다. 그래서 현금 공개일에 그날을 싣는 것은
    1분기 행의 창 [전년 반기, 3분기, 사업보고서, 1분기]에서는 직전 행(r-1), 반기 행의 창 [전년
    3분기, 사업보고서, 1분기, 반기]에서는 r-2 하나뿐이다. 공개일 창이 그 자리를 빼면 영업현금 TTM 이
    행 공개일부터 새어 든다(실원장 1분기 행 2,076행, 반기 행 814행).
    """
    late = date(2022, 9, 1)
    con = _connect([
        _fin("P2", date(2021, 3, 31), date(2021, 5, 17), net_income=10, cf_ytd=10),
        _fin("P2", date(2021, 6, 30), date(2021, 8, 16), net_income=11, cf_ytd=21),
        _fin("P2", date(2021, 9, 30), date(2021, 11, 15), net_income=12, cf_ytd=33),
        _fin("P2", date(2021, 12, 31), late, net_income=46, cf_ytd=46),
        _fin("P2", date(2022, 3, 31), date(2022, 5, 16), net_income=14, cf_ytd=14),
        _fin("P2", date(2022, 6, 30), date(2022, 8, 16), net_income=15, cf_ytd=29),
    ])
    assert _ttm(con, "P2", date(2022, 3, 31), "ttm_cf_operating") == 11 + 12 + 13 + 14
    assert _ttm(con, "P2", date(2022, 3, 31), "ttm_cf_available_date") == late
    assert _ttm(con, "P2", date(2022, 3, 31), "ttm_income_available_date") == late
    assert _ttm(con, "P2", date(2022, 6, 30), "ttm_cf_available_date") == late


def test_창_안에_연결과_별도가_섞이면_TTM_을_세우지_않는다() -> None:
    """리뷰 P2-1 — 연결재무제표를 중간부터 낸 법인의 창 OFS·OFS·OFS·CFS 는 범위가 다른 이익의 합이다."""
    con = _connect([
        _fin("F4", date(2022, 3, 31), date(2022, 5, 16), net_income=10, fs_div="OFS"),
        _fin("F4", date(2022, 6, 30), date(2022, 8, 16), net_income=11, fs_div="OFS"),
        _fin("F4", date(2022, 9, 30), date(2022, 11, 14), net_income=12, fs_div="OFS"),
        _fin("F4", date(2022, 12, 31), date(2023, 3, 20), net_income=46, q4_derived=13,
             fs_div="OFS"),
        _fin("F4", date(2023, 3, 31), date(2023, 5, 15), net_income=14, fs_div="CFS"),
    ])
    assert _ttm(con, "F4", date(2022, 12, 31)) == 46                   # 창이 전부 별도면 선다
    assert _ttm(con, "F4", date(2023, 3, 31)) is None


def test_창_안에_매출_기준이_섞이면_TTM_매출을_세우지_않는다() -> None:
    """리뷰 P3-2 — `standard` 와 `banking_gross` 를 더한 매출은 어느 기준으로도 읽을 수 없다."""
    con = _connect([
        _fin("B5", date(2022, 3, 31), date(2022, 5, 16), revenue=100),
        _fin("B5", date(2022, 6, 30), date(2022, 8, 16), revenue=110),
        _fin("B5", date(2022, 9, 30), date(2022, 11, 14), revenue=120),
        _fin("B5", date(2022, 12, 31), date(2023, 3, 20), revenue=460, revenue_q4=130),
        _fin("B5", date(2023, 3, 31), date(2023, 5, 15), revenue=140,
             revenue_basis="banking_gross"),
    ])
    assert _ttm(con, "B5", date(2022, 12, 31), "ttm_revenue") == 460
    assert _ttm(con, "B5", date(2023, 3, 31), "ttm_revenue") is None


def test_사업보고서_4분기는_앞_3분기가_같은_재무제표_구분일_때만_선다() -> None:
    """재리뷰 P2-R1 ① — 1~3분기는 별도(OFS), 사업보고서부터 연결(CFS)인 법인.

    창 [2021 사업보고서, 2022 1분기·반기·3분기]는 전부 연결이라 창 검사를 통과한다. 그러나 2021
    4분기 = 연결 연간 − 별도 1~3분기라 범위가 다른 이익의 차다. 이 가드가 빠지면 실원장 순이익
    1,014행·매출 1,034행이 그런 값으로 선다.
    """
    con = _connect([
        _fin("N6", date(2021, 3, 31), date(2021, 5, 17), net_income=10, fs_div="OFS"),
        _fin("N6", date(2021, 6, 30), date(2021, 8, 16), net_income=11, fs_div="OFS"),
        _fin("N6", date(2021, 9, 30), date(2021, 11, 15), net_income=12, fs_div="OFS"),
        _fin("N6", date(2021, 12, 31), date(2022, 3, 21), net_income=60),
        _fin("N6", date(2022, 3, 31), date(2022, 5, 16), net_income=14),
        _fin("N6", date(2022, 6, 30), date(2022, 8, 16), net_income=15),
        _fin("N6", date(2022, 9, 30), date(2022, 11, 14), net_income=16),
    ])
    assert _ttm(con, "N6", date(2022, 9, 30)) is None


def test_현금흐름_분기값은_직전_행이_바로_앞_분기_보고서일_때만_선다() -> None:
    """재리뷰 P2-R1 ② — 첫 해 3분기 보고서가 `11013` 으로 실린 법인(01167056 유형).

    사업보고서의 직전 행은 간격이 한 분기(92일)지만 3분기 보고서(`11014`)가 아니다. 누계 차이
    120 − 90 은 분기값으로 쓸 수 없다. 이 가드가 빠지면 실원장 영업현금 759행이 그런 값으로 선다.
    """
    con = _connect([
        _fin("N7", date(2021, 9, 30), date(2021, 11, 15), report="11013", cf_ytd=90),
        _fin("N7", date(2021, 12, 31), date(2022, 3, 21), cf_ytd=120),
        _fin("N7", date(2022, 3, 31), date(2022, 5, 16), cf_ytd=20),
        _fin("N7", date(2022, 6, 30), date(2022, 8, 16), cf_ytd=45),
        _fin("N7", date(2022, 9, 30), date(2022, 11, 14), cf_ytd=75),
    ])
    assert _ttm(con, "N7", date(2022, 9, 30), "ttm_cf_operating") is None
