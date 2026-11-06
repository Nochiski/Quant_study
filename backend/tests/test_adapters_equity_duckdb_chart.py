"""`ChartPricePort`(개별종목차트): duckdb 어댑터가 원장 일봉을 그대로 나르는가.

손 원장(2024-01-02 ~ 01-12 평일 9개):
- 01-12(금)는 모든 종목이 저녁 잠정 행뿐이라 확정 기준일은 01-11(목)이다.
- 005930: 조정가 = 원주가 × 2. 01-05는 정지일(거래량 0 → 기준가 행)이라 빠진다.
- 000020: 같은 티커를 쓰던 옛 종목이 01-02~01-04에 거래했고 지금 종목은 01-08에
  재상장했다(구간 2).
- 000030: 01-09에 다른 시장으로 이전상장했다 — `security.list_date`는 01-09지만 상장 구간은
  01-02부터 이어진다.
- 지수: 코스피(01-03 시가 0, 01-04 고가 비어 빠짐)와 코스피·코스닥에 하나씩 있는 `건설`.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("duckdb", reason="backend optional extra `equity` (uv sync --extra equity)")
import pyarrow as pa

from strategy_workbench.adapters.outbound.equity_duckdb.facade.provider import EquityDuckdbAdapter
from strategy_workbench.application.stock_chart.facade.ports import (
    ChartInstrument,
    InstrumentKind,
    InstrumentRef,
)
from strategy_workbench.domain.chart_analysis.facade.bars import PriceBar
from tests.equity_fixture import (
    PriceRow,
    calendar_table,
    policy_table,
    price_table,
    security_table,
    span_table,
    universe_table,
    write_equity_table,
)

SESSIONS = [date(2024, 1, d) for d in (2, 3, 4, 5, 8, 9, 10, 11, 12)]
HALT, EVENING, RELISTED = date(2024, 1, 5), date(2024, 1, 12), date(2024, 1, 8)
SAMSUNG = InstrumentRef(InstrumentKind.STOCK, "005930")
REUSED = InstrumentRef(InstrumentKind.STOCK, "000020")
MOVED = InstrumentRef(InstrumentKind.STOCK, "000030")
CONFIRMED, MOVED_LISTING = date(2024, 1, 11), date(2024, 1, 9)
KOSPI = InstrumentRef(InstrumentKind.INDEX, "KOSPI:코스피")


def _raw(day: date) -> float:
    return 1_000.0 + 10 * SESSIONS.index(day)


def _price_rows() -> tuple[list[PriceRow], list[str]]:
    rows: list[PriceRow] = []
    basis: list[str] = []
    for day in SESSIONS:
        close = _raw(day)
        if day == HALT:
            rows.append(("005930", day, close, close, close, close, 0))
        else:
            rows.append(("005930", day, close - 5, close + 20, close - 20, close, 100))
        rows.append(("000020", day, 500.0, 510.0, 490.0, 505.0, 100))
        rows.append(("000030", day, 300.0, 310.0, 290.0, 305.0, 100))
        basis += ["evening" if day == EVENING else "krx"] * 3
    return rows, basis


def _adjusted(rows: list[PriceRow], basis: list[str]) -> pa.Table:
    """`price_adj_daily` 중 어댑터가 읽는 칸. 조정 계수 2를 원주가에 곱한다."""

    def adj(value: float | None) -> float | None:
        return None if value is None else value * 2

    return pa.table(
        {
            "ticker": pa.array([r[0] for r in rows]),
            "date": pa.array([r[1] for r in rows], type=pa.date32()),
            "adj_open": pa.array([adj(r[2]) for r in rows], type=pa.float64()),
            "adj_high": pa.array([adj(r[3]) for r in rows], type=pa.float64()),
            "adj_low": pa.array([adj(r[4]) for r in rows], type=pa.float64()),
            "adj_close": pa.array([adj(r[5]) for r in rows], type=pa.float64()),
            "basis": pa.array(basis),
        }
    )


def _index() -> pa.Table:
    rows: list[tuple[str, str, date, float, float | None, float, float]] = []
    for i, day in enumerate(SESSIONS):
        close = 2_000.0 + i
        rows.append(
            (
                "KOSPI",
                "코스피",
                day,
                0.0 if i == 1 else close - 1,
                None if i == 2 else close + 3,
                close - 3,
                close,
            )
        )
        rows.append(("KOSPI", "건설", day, 90.0, 91.0, 89.0, 90.5))
        rows.append(("KOSDAQ", "건설", day, 50.0, 51.0, 49.0, 50.5))
    return pa.table(
        {
            "index_class": pa.array([r[0] for r in rows]),
            "index_name": pa.array([r[1] for r in rows]),
            "date": pa.array([r[2] for r in rows], type=pa.date32()),
            "open_idx": pa.array([r[3] for r in rows], type=pa.float64()),
            "high_idx": pa.array([r[4] for r in rows], type=pa.float64()),
            "low_idx": pa.array([r[5] for r in rows], type=pa.float64()),
            "close_idx": pa.array([r[6] for r in rows], type=pa.float64()),
        }
    )


def build_chart_root(root: Path) -> Path:
    """모듈 설명의 손 원장을 `root`에 쓴다(HTTP 통합 테스트도 쓴다)."""
    rows, basis = _price_rows()
    write_equity_table(root, "trading_calendar", calendar_table(SESSIONS))
    write_equity_table(
        root,
        "security_span",
        span_table(
            [
                ("005930", 1, SESSIONS[0], SESSIONS[-1], "active"),
                ("000020", 1, SESSIONS[0], date(2024, 1, 4), "delisted"),
                ("000020", 2, RELISTED, SESSIONS[-1], "active"),
                ("000030", 1, SESSIONS[0], SESSIONS[-1], "active"),
            ]
        ),
    )
    write_equity_table(
        root,
        "universe_daily",
        universe_table([(r[0], r[1], "common", "listed") for r in rows]),
        year_column="date",
    )
    write_equity_table(root, "universe_policy", policy_table([("krx.all", "all", 1, "TRUE")]))
    write_equity_table(root, "price_daily", price_table(rows, basis=basis), year_column="date")
    security = security_table(
        [("005930", "common"), ("000020", "common"), ("000030", "common")],
        names={"005930": "삼성전자보통주", "000020": "새회사", "000030": "옮긴회사"},
    )
    security = security.append_column(
        "name_abbrv_current", pa.array(["삼성전자", None, None])
    ).append_column(
        "list_date",
        pa.array([date(1975, 6, 11), RELISTED, MOVED_LISTING], type=pa.date32()),
    )
    write_equity_table(root, "security", security)
    write_equity_table(root, "price_adj_daily", _adjusted(rows, basis), year_column="date")
    write_equity_table(root, "index_daily", _index(), year_column="date")
    return root


@pytest.fixture(scope="module")
def adapter(tmp_path_factory: pytest.TempPathFactory) -> EquityDuckdbAdapter:
    return EquityDuckdbAdapter(build_chart_root(tmp_path_factory.mktemp("chart") / "root"))


def test_instruments_list_stocks_with_short_names_and_indices_per_market(
    adapter: EquityDuckdbAdapter,
) -> None:
    items = {item.ref: item for item in adapter.chart_instruments()}
    assert items[SAMSUNG] == ChartInstrument(SAMSUNG, "삼성전자", "common", ("삼성전자보통주",))
    assert items[REUSED] == ChartInstrument(REUSED, "새회사", "common")
    # 같은 이름의 업종 지수는 시장 구분으로 갈린다
    indices = sorted(ref.code for ref in items if ref.kind is InstrumentKind.INDEX)
    assert indices == ["KOSDAQ:건설", "KOSPI:건설", "KOSPI:코스피"]
    assert items[KOSPI].group == "KOSPI"


def test_stock_history_is_adjusted_and_skips_reference_and_evening_rows(
    adapter: EquityDuckdbAdapter,
) -> None:
    # 달력의 마지막 날(01-12)은 잠정 행뿐이라 기준일이 아니다
    assert (adapter.latest_session(), adapter.confirmed_session()) == (EVENING, CONFIRMED)
    history = adapter.chart_history(SAMSUNG, EVENING)
    assert history is not None
    assert [bar.session for bar in history.bars] == [
        d for d in SESSIONS if d not in (HALT, EVENING)
    ]
    last = date(2024, 1, 11)
    assert history.bars[-1] == PriceBar(
        last, (_raw(last) - 5) * 2, (_raw(last) + 20) * 2, (_raw(last) - 20) * 2, _raw(last) * 2
    )
    # 배율의 근거는 마지막 일봉 날의 원주가 종가다
    assert history.last_actual_close == _raw(last)

    earlier = adapter.chart_history(SAMSUNG, date(2024, 1, 9))
    assert earlier is not None
    assert (earlier.bars[-1].session, earlier.last_actual_close) == (
        date(2024, 1, 9),
        _raw(date(2024, 1, 9)),
    )


def test_the_lower_bound_is_the_last_listing_span_not_the_listing_date(
    adapter: EquityDuckdbAdapter,
) -> None:
    # 재상장(티커 재사용): 옛 종목(구간 1)의 가격을 섞지 않는다
    reused = adapter.chart_history(REUSED, CONFIRMED)
    assert reused is not None
    assert [bar.session for bar in reused.bars] == [
        d for d in SESSIONS if RELISTED <= d <= CONFIRMED
    ]
    # 이전상장: 구간이 이어지므로 새 시장 상장일(01-09) 앞의 이력도 그 종목이다
    moved = adapter.chart_history(MOVED, CONFIRMED)
    assert moved is not None
    assert moved.bars[0].session == SESSIONS[0] < MOVED_LISTING
    assert len(moved.bars) == SESSIONS.index(CONFIRMED) + 1


def test_index_history_reads_index_ohlc_without_missing_or_zero_prices(
    adapter: EquityDuckdbAdapter,
) -> None:
    history = adapter.chart_history(KOSPI, EVENING)
    assert history is not None
    assert history.last_actual_close is None
    # 01-03 시가 0, 01-04 고가 비어 있음 → 그날은 봉이 없다
    assert [bar.session for bar in history.bars] == [SESSIONS[0], *SESSIONS[3:]]
    assert history.bars[0] == PriceBar(SESSIONS[0], 1_999.0, 2_003.0, 1_997.0, 2_000.0)
    kosdaq = adapter.chart_history(InstrumentRef(InstrumentKind.INDEX, "KOSDAQ:건설"), EVENING)
    assert kosdaq is not None and {bar.close for bar in kosdaq.bars} == {50.5}


def test_unknown_instruments_have_no_history(adapter: EquityDuckdbAdapter) -> None:
    for ref in (
        InstrumentRef(InstrumentKind.STOCK, "999999"),
        InstrumentRef(InstrumentKind.INDEX, "KOSPI:없는지수"),
        InstrumentRef(InstrumentKind.INDEX, "코스피"),
    ):
        assert adapter.chart_history(ref, EVENING) is None
