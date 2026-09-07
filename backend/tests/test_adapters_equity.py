"""equity 층 parquet 어댑터(`equity_duckdb`, S07) — 커널 3포트 테스트.

동작 검증은 테스트가 직접 만든 손 픽스처 equity_root(`tests/equity_fixture.py`)로 한다.
산출물 parquet·MANIFEST 에 의존하지 않는다(`.claude/rules/testing.md`).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

pytest.importorskip("pyarrow")

from backtest_engine.adapters.equity_duckdb import (  # noqa: E402  # reason: importorskip 이후 import
    EquityBarSource,
    EquityCorporateActionSource,
    EquityUniverseSource,
    resolve_table,
)
from backtest_engine.ports.corporate_actions import (  # noqa: E402  # reason: importorskip 이후 import
    CorporateActionQuery,
)
from backtest_engine.ports.market_data import (  # noqa: E402  # reason: importorskip 이후 import
    BarQuery,
    LoadStatus,
    OhlcPolicy,
)
from backtest_engine.ports.universe import UniverseQuery  # noqa: E402  # reason: importorskip 이후
from backtest_engine.types.events import (  # noqa: E402  # reason: importorskip 이후 import
    CorporateActionType,
)
from tests.conftest import make_instrument  # noqa: E402  # reason: importorskip 이후 import
from tests.equity_fixture import (  # noqa: E402  # reason: importorskip 이후 import
    FactorRow,
    PriceRow,
    SpanRow,
    calendar_table,
    factor_table,
    price_table,
    security_table,
    span_table,
    write_equity_table,
    write_manifest,
)

SAMSUNG = make_instrument("005930")
HYNIX = make_instrument("000660")
BACKFILL_END = date(2026, 8, 20)


def d(day: int, month: int = 5, year: int = 2018) -> date:
    return date(year, month, day)


def trade(ticker: str, session: date, close: float, volume: int = 1_000) -> PriceRow:
    return (ticker, session, close, close + 10, close - 10, close, volume)


def reference(ticker: str, session: date, close: float) -> PriceRow:
    """기준가·정지일 행 — S04 규약: O/H/L NULL, 거래량 0, 종가 보존."""
    return (ticker, session, None, None, None, close, 0)


def sessions(*days: date) -> list[date]:
    return sorted(set(days) | {BACKFILL_END})


def write_spans(root: Path, rows: list[SpanRow], calendar: list[date] | None = None) -> None:
    write_equity_table(root, "security_span", span_table(rows))
    write_equity_table(root, "trading_calendar", calendar_table(calendar or [BACKFILL_END]))


# --- MANIFEST 해석 -------------------------------------------------------------


class TestResolveTable:
    def test_missing_table_is_no_data_with_root_and_table(self, tmp_path: Path) -> None:
        build = resolve_table(tmp_path, "price_daily")
        assert build.status is LoadStatus.NO_DATA
        assert build.detail is not None
        assert str(tmp_path) in build.detail and "table=price_daily" in build.detail

    def test_no_current_build_is_no_data(self, tmp_path: Path) -> None:
        write_manifest(tmp_path / "price_daily", None, [])
        build = resolve_table(tmp_path, "price_daily")
        assert build.status is LoadStatus.NO_DATA
        assert "no current_build" in (build.detail or "")

    def test_current_build_not_in_builds_is_format_error(self, tmp_path: Path) -> None:
        write_manifest(tmp_path / "price_daily", "b_missing", [], builds=[])
        build = resolve_table(tmp_path, "price_daily")
        assert build.status is LoadStatus.FORMAT_ERROR
        assert "build=b_missing" in (build.detail or "")

    def test_partition_directory_missing_is_format_error(self, tmp_path: Path) -> None:
        write_manifest(tmp_path / "price_daily", "b_1", [{"path": "v=b_1/year=2018"}])
        build = resolve_table(tmp_path, "price_daily")
        assert build.status is LoadStatus.FORMAT_ERROR
        assert "partition directory missing" in (build.detail or "")

    def test_only_manifest_partitions_are_read_not_stale_versions(self, tmp_path: Path) -> None:
        """keep=3 GC 로 구버전 `v=` 가 공존해도 current_build 파티션만 읽는다(glob 금지)."""
        write_equity_table(
            tmp_path, "price_daily", price_table([trade("005930", d(2), 100)]), build_id="b_old",
            year_column="date",
        )
        write_equity_table(
            tmp_path, "price_daily", price_table([trade("005930", d(3), 200)]), build_id="b_new",
            year_column="date",
        )
        assert (tmp_path / "price_daily" / "v=b_old").exists()
        result = EquityBarSource(tmp_path).load_bars(BarQuery(instruments=(SAMSUNG,)))
        assert result.ok, result.detail
        assert [(b.ts.day, b.close) for b in result.bars] == [(3, 200.0)]


# --- BarSource ---------------------------------------------------------------


class TestEquityBarSource:
    def test_missing_price_table_is_no_data(self, tmp_path: Path) -> None:
        result = EquityBarSource(tmp_path).load_bars(BarQuery(instruments=(SAMSUNG,)))
        assert result.status is LoadStatus.NO_DATA
        assert "table=price_daily" in (result.detail or "")

    def test_raw_prices_and_session_midnight_ts(self, tmp_path: Path) -> None:
        write_equity_table(
            tmp_path, "price_daily",
            price_table([("005930", d(2), 2_650_000, 2_660_000, 2_640_000, 2_655_000, 123)]),
            year_column="date",
        )
        result = EquityBarSource(tmp_path).load_bars(BarQuery(instruments=(SAMSUNG,)))
        assert result.ok, result.detail
        (bar,) = result.bars
        assert bar.ts == datetime(2018, 5, 2)
        assert (bar.open, bar.high, bar.low, bar.close, bar.volume) == (
            2_650_000.0, 2_660_000.0, 2_640_000.0, 2_655_000.0, 123,
        )
        assert bar.instrument == SAMSUNG

    def test_reference_rows_are_dropped_and_counted(self, tmp_path: Path) -> None:
        """005930 2018-04-30~05-03 분할 정지: O/H/L NULL·거래량 0·종가 보존 → 방출하지 않는다."""
        write_equity_table(
            tmp_path, "price_daily",
            price_table([
                trade("005930", d(27, 4), 2_650_000),
                reference("005930", d(30, 4), 2_650_000),
                reference("005930", d(2), 2_650_000),
                reference("005930", d(3), 2_650_000),
                trade("005930", d(4), 51_900),
            ]),
            year_column="date",
        )
        result = EquityBarSource(tmp_path).load_bars(BarQuery(instruments=(SAMSUNG,)))
        assert result.ok, result.detail
        assert [b.ts.date() for b in result.bars] == [d(27, 4), d(4)]
        assert result.dropped_rows == 3 and result.repaired_rows == 0

    def test_only_reference_rows_is_no_data_with_count(self, tmp_path: Path) -> None:
        write_equity_table(
            tmp_path, "price_daily", price_table([reference("005930", d(2), 100)]),
            year_column="date",
        )
        result = EquityBarSource(tmp_path).load_bars(BarQuery(instruments=(SAMSUNG,)))
        assert result.status is LoadStatus.NO_DATA
        assert "reference_rows=1" in (result.detail or "")

    def test_open_null_with_volume_follows_ohlc_policy(self, tmp_path: Path) -> None:
        """GAP-14 (`open IS NULL ∧ volume > 0`, 서버 127행): STRICT 거절, CLAMP 제거+집계."""
        rows: list[PriceRow] = [
            trade("005930", d(2), 100),
            ("005930", d(3), None, 110, 90, 105, 500),
            trade("005930", d(4), 110),
        ]
        write_equity_table(tmp_path, "price_daily", price_table(rows), year_column="date")
        strict = EquityBarSource(tmp_path).load_bars(
            BarQuery(instruments=(SAMSUNG,), ohlc_policy=OhlcPolicy.STRICT)
        )
        assert strict.status is LoadStatus.FORMAT_ERROR
        assert "session=2018-05-03" in (strict.detail or "")
        clamp = EquityBarSource(tmp_path).load_bars(
            BarQuery(instruments=(SAMSUNG,), ohlc_policy=OhlcPolicy.CLAMP)
        )
        assert clamp.ok, clamp.detail
        assert [b.ts.day for b in clamp.bars] == [2, 4] and clamp.dropped_rows == 1

    def test_date_range_is_inclusive_across_year_partitions(self, tmp_path: Path) -> None:
        rows = [trade("005930", date(y, 6, 1), 100 + y) for y in (2017, 2018, 2019, 2020)]
        write_equity_table(tmp_path, "price_daily", price_table(rows), year_column="date")
        result = EquityBarSource(tmp_path).load_bars(
            BarQuery(instruments=(SAMSUNG,), start=date(2018, 6, 1), end=date(2019, 6, 1))
        )
        assert result.ok, result.detail
        assert [b.ts.year for b in result.bars] == [2018, 2019]

    def test_missing_instrument_fails_whole_query(self, tmp_path: Path) -> None:
        write_equity_table(
            tmp_path, "price_daily", price_table([trade("005930", d(2), 100)]), year_column="date"
        )
        result = EquityBarSource(tmp_path).load_bars(BarQuery(instruments=(SAMSUNG, HYNIX)))
        assert result.status is LoadStatus.NO_DATA
        assert "symbol=000660" in (result.detail or "")

    def test_duplicate_session_is_format_error(self, tmp_path: Path) -> None:
        write_equity_table(
            tmp_path, "price_daily",
            price_table([trade("005930", d(2), 100), trade("005930", d(2), 101)]),
            year_column="date",
        )
        result = EquityBarSource(tmp_path).load_bars(BarQuery(instruments=(SAMSUNG,)))
        assert result.status is LoadStatus.FORMAT_ERROR
        assert "duplicate session" in (result.detail or "")

    def test_missing_required_column_is_format_error(self, tmp_path: Path) -> None:
        import pyarrow as pa

        broken = price_table([trade("005930", d(2), 100)]).drop_columns(["price_kind"])
        assert isinstance(broken, pa.Table)
        write_equity_table(tmp_path, "price_daily", broken, year_column="date")
        result = EquityBarSource(tmp_path).load_bars(BarQuery(instruments=(SAMSUNG,)))
        assert result.status is LoadStatus.FORMAT_ERROR
        assert "price_kind" in (result.detail or "")


class TestEquityBarSourceSpans:
    """`security_id = ticker:span_seq` — 재상장 종목은 구간별로 다른 종목이다."""

    @pytest.fixture
    def root(self, tmp_path: Path) -> Path:
        write_equity_table(
            tmp_path, "price_daily",
            price_table([
                trade("036220", date(2016, 5, 3), 100),
                trade("036220", date(2016, 5, 4), 101),
                trade("036220", date(2024, 3, 13), 200),
                trade("036220", date(2024, 3, 14), 201),
            ]),
            year_column="date",
        )
        write_spans(tmp_path, [
            ("036220", 1, date(2010, 1, 4), date(2016, 5, 4), "delisted"),
            ("036220", 2, date(2024, 3, 13), BACKFILL_END, "coverage_gap"),
        ])
        return tmp_path

    def test_span_suffix_restricts_to_that_span(self, root: Path) -> None:
        first = EquityBarSource(root).load_bars(
            BarQuery(instruments=(make_instrument("036220:1"),))
        )
        second = EquityBarSource(root).load_bars(
            BarQuery(instruments=(make_instrument("036220:2"),))
        )
        assert first.ok and [b.ts.year for b in first.bars] == [2016, 2016]
        assert second.ok and [b.ts.year for b in second.bars] == [2024, 2024]
        assert all(b.instrument.symbol == "036220:2" for b in second.bars)

    def test_plain_ticker_returns_all_spans(self, root: Path) -> None:
        result = EquityBarSource(root).load_bars(BarQuery(instruments=(make_instrument("036220"),)))
        assert result.ok and [b.ts.year for b in result.bars] == [2016, 2016, 2024, 2024]

    def test_query_range_outside_span_is_no_data(self, root: Path) -> None:
        result = EquityBarSource(root).load_bars(
            BarQuery(instruments=(make_instrument("036220:1"),), start=date(2020, 1, 1))
        )
        assert result.status is LoadStatus.NO_DATA
        assert "outside span" in (result.detail or "")

    @pytest.mark.parametrize("symbol", ["036220:3", "036220:x", ":1"])
    def test_unknown_or_malformed_security_id_is_no_data(self, root: Path, symbol: str) -> None:
        result = EquityBarSource(root).load_bars(BarQuery(instruments=(make_instrument(symbol),)))
        assert result.status is LoadStatus.NO_DATA
        assert result.detail is not None

    def test_span_suffix_without_span_table_is_no_data(self, tmp_path: Path) -> None:
        write_equity_table(
            tmp_path, "price_daily", price_table([trade("036220", d(2), 100)]), year_column="date"
        )
        result = EquityBarSource(tmp_path).load_bars(
            BarQuery(instruments=(make_instrument("036220:1"),))
        )
        assert result.status is LoadStatus.NO_DATA
        assert "table=security_span" in (result.detail or "")


# --- UniverseSource ----------------------------------------------------------

SPANS: list[SpanRow] = [
    ("005930", 1, date(2010, 1, 4), BACKFILL_END, "coverage_gap"),
    ("036220", 1, date(2010, 1, 4), date(2016, 5, 4), "delisted"),
    ("036220", 2, date(2024, 3, 13), BACKFILL_END, "coverage_gap"),
    ("069500", 1, date(2010, 1, 4), BACKFILL_END, "coverage_gap"),
]


class TestEquityUniverseSource:
    def test_relisted_ticker_has_two_memberships_and_coverage_gap_keeps_span(
        self, tmp_path: Path
    ) -> None:
        write_spans(tmp_path, SPANS)
        result = EquityUniverseSource(tmp_path).load_universe(UniverseQuery(venue="XKRX"))
        assert result.ok, result.detail
        by_symbol = {
            m.instrument.symbol: (m.first_session, m.last_session) for m in result.memberships
        }
        assert by_symbol["036220:1"] == (date(2010, 1, 4), date(2016, 5, 4))
        assert by_symbol["036220:2"] == (date(2024, 3, 13), BACKFILL_END)
        assert by_symbol["005930:1"] == (date(2010, 1, 4), BACKFILL_END)
        assert all(m.instrument.venue == "XKRX" for m in result.memberships)
        assert make_instrument("036220:1") in result.members(date(2016, 5, 4))
        assert not {i for i in result.members(date(2020, 1, 2)) if i.symbol.startswith("036220")}
        assert make_instrument("036220:2") in result.members(BACKFILL_END)

    def test_end_beyond_backfill_end_is_rejected(self, tmp_path: Path) -> None:
        write_spans(tmp_path, SPANS)
        source = EquityUniverseSource(tmp_path)
        rejected = source.load_universe(UniverseQuery(venue="XKRX", end=date(2026, 8, 21)))
        assert rejected.status is LoadStatus.NO_DATA
        assert "backfill_end=2026-08-20" in (rejected.detail or "")
        assert source.load_universe(UniverseQuery(venue="XKRX", end=BACKFILL_END)).ok

    def test_period_filter_clips_and_drops_spans_outside(self, tmp_path: Path) -> None:
        write_spans(tmp_path, SPANS)
        result = EquityUniverseSource(tmp_path).load_universe(
            UniverseQuery(venue="XKRX", start=date(2016, 5, 4), end=date(2016, 5, 10))
        )
        assert result.ok, result.detail
        got = {m.instrument.symbol: (m.first_session, m.last_session) for m in result.memberships}
        assert got == {
            "005930:1": (date(2016, 5, 4), date(2016, 5, 10)),
            "036220:1": (date(2016, 5, 4), date(2016, 5, 4)),
            "069500:1": (date(2016, 5, 4), date(2016, 5, 10)),
        }

    def test_no_overlap_is_no_data(self, tmp_path: Path) -> None:
        write_spans(tmp_path, [SPANS[1]])
        result = EquityUniverseSource(tmp_path).load_universe(
            UniverseQuery(venue="XKRX", start=date(2020, 1, 1), end=date(2020, 1, 31))
        )
        assert result.status is LoadStatus.NO_DATA

    def test_sec_type_filter_reads_security_table(self, tmp_path: Path) -> None:
        write_spans(tmp_path, SPANS)
        write_equity_table(
            tmp_path, "security",
            security_table([("005930", "common"), ("036220", "common"), ("069500", "etf")]),
        )
        result = EquityUniverseSource(tmp_path, sec_types=frozenset({"common"})).load_universe(
            UniverseQuery(venue="XKRX")
        )
        assert result.ok, result.detail
        assert sorted(m.instrument.symbol for m in result.memberships) == [
            "005930:1", "036220:1", "036220:2",
        ]

    def test_missing_calendar_or_span_table_is_no_data(self, tmp_path: Path) -> None:
        result = EquityUniverseSource(tmp_path).load_universe(UniverseQuery(venue="XKRX"))
        assert result.status is LoadStatus.NO_DATA
        assert "table=trading_calendar" in (result.detail or "")


# --- CorporateActionSource ---------------------------------------------------

FACTORS: list[FactorRow] = [
    ("005930", date(2018, 5, 4), "005930:split:2018-05-04", "split", 50.0, True),
    ("247540", date(2022, 6, 27), "247540:bonus:2022-06-27", "bonus", 4.0, True),
    ("101970", date(2018, 10, 12), "101970:capred:2018-10-12", "capred", 0.0999999934, True),
    ("101970", date(2018, 10, 13), "101970:capred:2018-10-13", "capred", 0.1, False),
    ("101970", date(2018, 2, 23), "101970:capred:2018-02-23", "capred", 1.0, False),
    ("000660", date(2015, 1, 5), "000660:reverse_split:2015-01-05", "reverse_split", 0.2, True),
]
FACTOR_SPANS: list[SpanRow] = [
    ("005930", 1, date(2010, 1, 4), BACKFILL_END, "coverage_gap"),
    ("247540", 1, date(2019, 3, 5), BACKFILL_END, "coverage_gap"),
    ("101970", 1, date(2012, 7, 26), date(2015, 3, 16), "delisted"),
    ("101970", 2, date(2018, 1, 2), BACKFILL_END, "coverage_gap"),
    ("000660", 1, date(2010, 1, 4), BACKFILL_END, "coverage_gap"),
]


def write_factors(root: Path, rows: list[FactorRow], apply_dates: list[date | None] | None = None,
                  spans: list[SpanRow] | None = None) -> None:
    write_equity_table(
        root, "adj_factor", factor_table(rows, apply_dates), year_column="effective_date"
    )
    write_spans(root, spans or FACTOR_SPANS)


Action = tuple[str, CorporateActionType, Decimal, date, str]


def actions_of(root: Path, *symbols: str, **query: date) -> list[Action]:
    result = EquityCorporateActionSource(root).load_actions(
        CorporateActionQuery(instruments=tuple(make_instrument(s) for s in symbols), **query)
    )
    assert result.ok, result.detail
    return [(e.instrument.symbol, e.action_type, e.ratio, e.ts.date(), e.detail)
            for e in result.actions]


class TestEquityCorporateActionSource:
    def test_ok_rows_only_with_enum_mapping_and_ratio_equal_to_share_factor(
        self, tmp_path: Path
    ) -> None:
        write_factors(tmp_path, FACTORS)
        got = actions_of(tmp_path, "005930", "247540", "101970", "000660")
        assert got == [
            ("000660", CorporateActionType.REVERSE_SPLIT, Decimal("0.2"), date(2015, 1, 5),
             "000660:reverse_split:2015-01-05"),
            ("005930", CorporateActionType.SPLIT, Decimal("50.0"), date(2018, 5, 4),
             "005930:split:2018-05-04"),
            ("101970", CorporateActionType.REVERSE_SPLIT, Decimal("0.0999999934"),
             date(2018, 10, 12), "101970:capred:2018-10-12"),
            ("247540", CorporateActionType.SPLIT, Decimal("4.0"), date(2022, 6, 27),
             "247540:bonus:2022-06-27"),
        ]
        assert all(float(ratio) == next(r[4] for r in FACTORS if r[2] == eid)
                   for _, _, ratio, _, eid in got)

    def test_ts_is_effective_date_without_apply_date_column(self, tmp_path: Path) -> None:
        write_factors(tmp_path, FACTORS[:1])
        assert actions_of(tmp_path, "005930")[0][3] == date(2018, 5, 4)

    def test_ts_is_apply_date_when_column_present(self, tmp_path: Path) -> None:
        """S06 후속: 감자는 기준일이 아니라 거래재개일에 가격이 조정된다 → 컬럼이 있으면 그 날."""
        write_factors(tmp_path, FACTORS[2:3], apply_dates=[date(2018, 10, 29)])
        assert actions_of(tmp_path, "101970")[0][3] == date(2018, 10, 29)

    def test_null_apply_date_on_ok_row_is_format_error(self, tmp_path: Path) -> None:
        write_factors(tmp_path, FACTORS[:1], apply_dates=[None])
        result = EquityCorporateActionSource(tmp_path).load_actions(
            CorporateActionQuery(instruments=(SAMSUNG,))
        )
        assert result.status is LoadStatus.FORMAT_ERROR
        assert "apply_date is NULL" in (result.detail or "")

    def test_event_type_outside_vocabulary_is_format_error(self, tmp_path: Path) -> None:
        write_factors(
            tmp_path,
            [("005930", date(2018, 5, 4), "005930:spinoff:2018-05-04", "spinoff", 2.0, True)],
        )
        result = EquityCorporateActionSource(tmp_path).load_actions(
            CorporateActionQuery(instruments=(SAMSUNG,))
        )
        assert result.status is LoadStatus.FORMAT_ERROR
        assert "event_type='spinoff'" in (result.detail or "")

    def test_direction_contradiction_is_format_error(self, tmp_path: Path) -> None:
        write_factors(
            tmp_path, [("005930", date(2018, 5, 4), "005930:split:2018-05-04", "split", 0.5, True)]
        )
        result = EquityCorporateActionSource(tmp_path).load_actions(
            CorporateActionQuery(instruments=(SAMSUNG,))
        )
        assert result.status is LoadStatus.FORMAT_ERROR
        assert "direction contradicts" in (result.detail or "")

    def test_unknown_krx_is_directed_by_share_factor(self, tmp_path: Path) -> None:
        """S06-2 KRX 기준가 원천 행(`unknown_krx`, corp_event 에 없는 사건)은 share_factor 방향으로
        SPLIT / REVERSE_SPLIT — DART 공백기 액면분할(×10)·감자(×0.1)."""
        write_factors(
            tmp_path,
            [
                ("005930", date(2012, 3, 5), "005930:krx_base:2012-03-05", "unknown_krx", 10.0,
                 True),
                ("000660", date(2013, 7, 1), "000660:krx_base:2013-07-01", "unknown_krx", 0.1,
                 True),
            ],
        )
        assert actions_of(tmp_path, "005930", "000660") == [
            ("005930", CorporateActionType.SPLIT, Decimal("10.0"), date(2012, 3, 5),
             "005930:krx_base:2012-03-05"),
            ("000660", CorporateActionType.REVERSE_SPLIT, Decimal("0.1"), date(2013, 7, 1),
             "000660:krx_base:2013-07-01"),
        ]

    def test_unknown_price_only_ok_row_is_format_error(self, tmp_path: Path) -> None:
        """`unknown_price_only` 는 시총 불변이 아니라 항상 factor_ok=false 로 실려야 한다 —
        ok 로 오면 어휘 밖(조용한 분할 적용 금지). ok=false 행은 방출되지 않는다."""
        rows: list[FactorRow] = [
            ("005930", date(2012, 3, 5), "005930:krx_base:2012-03-05", "unknown_price_only", 1.0,
             False),
        ]
        write_factors(tmp_path, rows)
        assert actions_of(tmp_path, "005930") == []
        write_factors(
            tmp_path,
            [("005930", date(2012, 3, 5), "005930:krx_base:2012-03-05", "unknown_price_only",
              0.98, True)],
        )
        result = EquityCorporateActionSource(tmp_path).load_actions(
            CorporateActionQuery(instruments=(SAMSUNG,))
        )
        assert result.status is LoadStatus.FORMAT_ERROR
        assert "event_type='unknown_price_only'" in (result.detail or "")

    def test_unknown_krx_with_share_factor_one_is_format_error(self, tmp_path: Path) -> None:
        write_factors(
            tmp_path,
            [("005930", date(2012, 3, 5), "005930:krx_base:2012-03-05", "unknown_krx", 1.0,
              True)],
        )
        result = EquityCorporateActionSource(tmp_path).load_actions(
            CorporateActionQuery(instruments=(SAMSUNG,))
        )
        assert result.status is LoadStatus.FORMAT_ERROR
        assert "cannot direct" in (result.detail or "")

    def test_instrument_without_events_is_ok_but_unknown_ticker_is_no_data(
        self, tmp_path: Path
    ) -> None:
        write_factors(tmp_path, FACTORS[:1])
        assert actions_of(tmp_path, "000660") == []
        result = EquityCorporateActionSource(tmp_path).load_actions(
            CorporateActionQuery(instruments=(SAMSUNG, make_instrument("999999")))
        )
        assert result.status is LoadStatus.NO_DATA
        assert "ticker=999999" in (result.detail or "")

    def test_span_suffix_restricts_events_to_that_span(self, tmp_path: Path) -> None:
        write_factors(tmp_path, FACTORS)
        assert actions_of(tmp_path, "101970:1") == []
        assert [a[4] for a in actions_of(tmp_path, "101970:2")] == ["101970:capred:2018-10-12"]

    def test_period_filter_on_event_session(self, tmp_path: Path) -> None:
        write_factors(tmp_path, FACTORS)
        assert actions_of(tmp_path, "005930", start=date(2018, 5, 5)) == []
        assert len(actions_of(tmp_path, "005930", end=date(2018, 5, 4))) == 1

    def test_missing_factor_table_is_no_data(self, tmp_path: Path) -> None:
        result = EquityCorporateActionSource(tmp_path).load_actions(
            CorporateActionQuery(instruments=(SAMSUNG,))
        )
        assert result.status is LoadStatus.NO_DATA
        assert "table=adj_factor" in (result.detail or "")
