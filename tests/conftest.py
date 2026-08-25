"""테스트 공용 헬퍼: 손으로 만든 소형 픽스처 생성기."""

from __future__ import annotations

from datetime import datetime

from backtest_engine.types.instruments import AssetClass, InstrumentId
from backtest_engine.types.market import Bar, MarketSnapshot


def make_instrument(symbol: str = "005930") -> InstrumentId:
    return InstrumentId(
        venue="XKRX", symbol=symbol, asset_class=AssetClass.EQUITY, currency="KRW"
    )


def make_bar(
    ts: datetime,
    instrument: InstrumentId,
    open_price: float,
    close_price: float,
    volume: int = 1_000,
) -> Bar:
    return Bar(
        ts=ts,
        instrument=instrument,
        open=open_price,
        high=max(open_price, close_price),
        low=min(open_price, close_price),
        close=close_price,
        volume=volume,
    )


def make_snapshot(ts: datetime, *bars: Bar) -> MarketSnapshot:
    return MarketSnapshot(ts=ts, bars=bars)


def day(day_of_month: int) -> datetime:
    return datetime(2026, 8, day_of_month)
