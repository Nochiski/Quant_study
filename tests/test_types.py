"""프로토콜 타입 불변조건 테스트."""

from __future__ import annotations

import numpy as np
import pytest

from backtest_engine.errors import InstrumentNotInSnapshot
from backtest_engine.types.market import Bar, MarketSnapshot, PriceWindow
from tests.conftest import day, make_bar, make_instrument, make_snapshot


class TestBar:
    def test_high_below_body_rejected(self) -> None:
        with pytest.raises(ValueError, match="OHLC invariant"):
            Bar(
                ts=day(1),
                instrument=make_instrument(),
                open=100.0,
                high=90.0,
                low=80.0,
                close=95.0,
                volume=10,
            )

    def test_zero_price_rejected(self) -> None:
        # 거래정지 행(open=0)이 0원 체결로 이어지는 것을 Bar 단계에서 차단
        with pytest.raises(ValueError, match="prices must be > 0"):
            Bar(
                ts=day(1),
                instrument=make_instrument(),
                open=0.0,
                high=0.0,
                low=0.0,
                close=53_000.0,
                volume=0,
            )

    def test_negative_volume_rejected(self) -> None:
        with pytest.raises(ValueError, match="volume"):
            Bar(
                ts=day(1),
                instrument=make_instrument(),
                open=100.0,
                high=100.0,
                low=100.0,
                close=100.0,
                volume=-1,
            )


class TestMarketSnapshot:
    def test_mixed_timestamps_rejected(self) -> None:
        instrument = make_instrument()
        with pytest.raises(ValueError, match="share the snapshot ts"):
            MarketSnapshot(
                ts=day(1), bars=(make_bar(day(2), instrument, 100.0, 100.0),)
            )

    def test_duplicate_instrument_rejected(self) -> None:
        instrument = make_instrument()
        bar = make_bar(day(1), instrument, 100.0, 100.0)
        with pytest.raises(ValueError, match="duplicate instrument"):
            MarketSnapshot(ts=day(1), bars=(bar, bar))

    def test_missing_instrument_lookup_fails_with_context(self) -> None:
        snapshot = make_snapshot(day(1), make_bar(day(1), make_instrument("A"), 100.0, 100.0))
        with pytest.raises(InstrumentNotInSnapshot, match="requested=B"):
            snapshot.bar(make_instrument("B"))


class TestPriceWindow:
    def _window(self) -> PriceWindow:
        instrument = make_instrument()
        return PriceWindow(
            timestamps=(day(1), day(2)),
            instruments=(instrument,),
            values=np.array([[100.0], [110.0]]),
        )

    def test_values_are_read_only(self) -> None:
        window = self._window()
        with pytest.raises(ValueError):
            window.values[0, 0] = 999.0

    def test_shape_mismatch_rejected(self) -> None:
        with pytest.raises(ValueError, match="shape mismatch"):
            PriceWindow(
                timestamps=(day(1),),
                instruments=(make_instrument(),),
                values=np.zeros((2, 2)),
            )

    def test_is_complete_detects_nan(self) -> None:
        instrument = make_instrument()
        window = PriceWindow(
            timestamps=(day(1), day(2)),
            instruments=(instrument,),
            values=np.array([[np.nan], [110.0]]),
        )
        assert not window.is_complete(instrument)
        assert self._window().is_complete(make_instrument())

    def test_unknown_column_fails_with_context(self) -> None:
        window = self._window()
        with pytest.raises(KeyError, match="not in window"):
            window.column(make_instrument("XXXX"))
