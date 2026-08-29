"""포트 값 타입(BarQuery)과 공용 정제 로직(cleaning) 테스트."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from backtest_engine.data.cleaning import RawBar, clean_raw_bars, merge_results
from backtest_engine.ports.market_data import BarQuery, LoadResult, LoadStatus, OhlcPolicy
from tests.conftest import make_instrument

INSTRUMENT = make_instrument()


def raw(day_of_month: int, o: float, h: float, lo: float, c: float, v: int = 100) -> RawBar:
    return RawBar(
        ts=datetime(2026, 8, day_of_month),
        open=o,
        high=h,
        low=lo,
        close=c,
        volume=v,
        origin=f"row={day_of_month}",
    )


class TestBarQuery:
    def test_rejects_empty_instruments(self) -> None:
        with pytest.raises(ValueError, match="at least one instrument"):
            BarQuery(instruments=())

    def test_rejects_reversed_range(self) -> None:
        with pytest.raises(ValueError, match="start must be <= end"):
            BarQuery(instruments=(INSTRUMENT,), start=date(2026, 8, 5), end=date(2026, 8, 1))

    def test_rejects_duplicate_instruments(self) -> None:
        with pytest.raises(ValueError, match="duplicate instrument"):
            BarQuery(instruments=(INSTRUMENT, INSTRUMENT))

    def test_includes_is_inclusive_on_both_ends(self) -> None:
        query = BarQuery(instruments=(INSTRUMENT,), start=date(2026, 8, 3), end=date(2026, 8, 5))
        assert query.includes(date(2026, 8, 3))
        assert query.includes(date(2026, 8, 5))
        assert not query.includes(date(2026, 8, 2))
        assert not query.includes(date(2026, 8, 6))

    def test_open_range_includes_everything(self) -> None:
        assert BarQuery(instruments=(INSTRUMENT,)).includes(date(1999, 1, 1))


class TestCleanRawBars:
    def test_strict_rejects_ohlc_violation_with_origin(self) -> None:
        result = clean_raw_bars([raw(3, 100, 90, 95, 105)], INSTRUMENT, OhlcPolicy.STRICT, "src")
        assert result.status is LoadStatus.FORMAT_ERROR
        assert result.detail is not None and "row=3" in result.detail

    def test_clamp_repairs_and_counts(self) -> None:
        result = clean_raw_bars(
            [raw(3, 100, 99, 95, 105), raw(4, 105, 110, 100, 108)],
            INSTRUMENT,
            OhlcPolicy.CLAMP,
            "src",
        )
        assert result.ok
        assert result.repaired_rows == 1
        assert result.bars[0].high == 105.0
        assert result.bars[1].high == 110.0

    def test_clamp_drops_non_positive_price_rows(self) -> None:
        result = clean_raw_bars(
            [raw(3, 100, 110, 95, 105), raw(4, 0, 0, 0, 105, 0), raw(5, 105, 110, 100, 108)],
            INSTRUMENT,
            OhlcPolicy.CLAMP,
            "src",
        )
        assert result.ok
        assert result.dropped_rows == 1
        assert [bar.ts.day for bar in result.bars] == [3, 5]

    def test_zero_volume_rows_dropped_only_when_requested(self) -> None:
        # KRX 원장: 정지 중에도 직전 종가가 유지된 행이 존재 → 거래량 0이 유일한 신호
        rows = [raw(3, 100, 110, 95, 105), raw(4, 105, 105, 105, 105, 0)]
        kept = clean_raw_bars(rows, INSTRUMENT, OhlcPolicy.STRICT, "src")
        assert kept.ok and len(kept.bars) == 2 and kept.dropped_rows == 0
        dropped = clean_raw_bars(
            rows, INSTRUMENT, OhlcPolicy.STRICT, "src", drop_zero_volume=True
        )
        assert dropped.ok and len(dropped.bars) == 1 and dropped.dropped_rows == 1

    def test_time_reversal_is_format_error_even_for_dropped_rows(self) -> None:
        result = clean_raw_bars(
            [raw(4, 100, 110, 95, 105), raw(3, 0, 0, 0, 0, 0)],
            INSTRUMENT,
            OhlcPolicy.CLAMP,
            "src",
        )
        assert result.status is LoadStatus.FORMAT_ERROR
        assert result.detail is not None and "strictly increasing" in result.detail

    def test_all_rows_dropped_is_no_data(self) -> None:
        result = clean_raw_bars(
            [raw(3, 0, 0, 0, 0, 0)], INSTRUMENT, OhlcPolicy.CLAMP, "src"
        )
        assert result.status is LoadStatus.NO_DATA
        assert result.detail is not None and "dropped=1" in result.detail


class TestMergeResults:
    def test_sums_statistics(self) -> None:
        a = clean_raw_bars([raw(3, 100, 99, 95, 105)], INSTRUMENT, OhlcPolicy.CLAMP, "a")
        b = clean_raw_bars(
            [raw(3, 0, 0, 0, 0, 0), raw(4, 50, 55, 45, 52)],
            make_instrument("000660"),
            OhlcPolicy.CLAMP,
            "b",
        )
        merged = merge_results([a, b])
        assert merged.ok
        assert len(merged.bars) == 2
        assert merged.repaired_rows == 1
        assert merged.dropped_rows == 1

    def test_any_failure_fails_whole_merge(self) -> None:
        ok = clean_raw_bars([raw(3, 100, 110, 95, 105)], INSTRUMENT, OhlcPolicy.STRICT, "a")
        failed = LoadResult(bars=(), status=LoadStatus.NO_DATA, detail="missing 000660")
        merged = merge_results([ok, failed])
        assert merged.status is LoadStatus.NO_DATA
        assert merged.detail == "missing 000660"
