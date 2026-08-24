"""시장 데이터 타입: Bar, MarketSnapshot, PriceWindow."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

import numpy as np
from numpy.typing import NDArray

from backtest_engine.errors import InstrumentNotInSnapshot
from backtest_engine.types.instruments import InstrumentId


class PriceField(Enum):
    OPEN = "open"
    HIGH = "high"
    LOW = "low"
    CLOSE = "close"
    VOLUME = "volume"


@dataclass(frozen=True)
class Bar:
    """한 종목의 한 시점 시장 상태. DataFeed가 만든 원본이므로 수정하지 않는다.

    v1에서 ts는 봉 마감 시각을 뜻한다.
    """

    ts: datetime
    instrument: InstrumentId
    open: float
    high: float
    low: float
    close: float
    volume: int

    def __post_init__(self) -> None:
        body_high = max(self.open, self.close)
        body_low = min(self.open, self.close)
        if self.high < body_high or self.low > body_low:
            raise ValueError(
                "OHLC invariant violated: expected high >= max(open, close) and "
                f"low <= min(open, close) — instrument={self.instrument.symbol} ts={self.ts} "
                f"o={self.open} h={self.high} l={self.low} c={self.close}"
            )
        if self.volume < 0:
            raise ValueError(
                f"volume must be >= 0 — instrument={self.instrument.symbol} "
                f"ts={self.ts} volume={self.volume}"
            )


@dataclass(frozen=True)
class MarketSnapshot:
    """같은 거래 시각에 확정된 여러 종목의 Bar 묶음. 전략 호출 입력."""

    ts: datetime
    bars: tuple[Bar, ...]

    def __post_init__(self) -> None:
        seen: set[InstrumentId] = set()
        for bar in self.bars:
            if bar.ts != self.ts:
                raise ValueError(
                    "all bars in a snapshot must share the snapshot ts — "
                    f"snapshot ts={self.ts} bar instrument={bar.instrument.symbol} bar ts={bar.ts}"
                )
            if bar.instrument in seen:
                raise ValueError(
                    f"duplicate instrument in snapshot — ts={self.ts} "
                    f"instrument={bar.instrument.symbol}"
                )
            seen.add(bar.instrument)

    def bar(self, instrument: InstrumentId) -> Bar:
        for candidate in self.bars:
            if candidate.instrument == instrument:
                return candidate
        raise InstrumentNotInSnapshot(
            f"instrument not in snapshot — ts={self.ts} requested={instrument.symbol} "
            f"available={[b.instrument.symbol for b in self.bars]}"
        )

    def has(self, instrument: InstrumentId) -> bool:
        return any(candidate.instrument == instrument for candidate in self.bars)


@dataclass(frozen=True, eq=False)  # ndarray 필드 → eq=False (원소별 __eq__ 모호성 회피)
class PriceWindow:
    """time × symbol 모양으로 정렬된 읽기 전용 가격 배열.

    values[i, j]는 timestamps[i] 시점 instruments[j]의 값이며,
    해당 시점에 데이터가 없으면 NaN이다.
    """

    timestamps: tuple[datetime, ...]
    instruments: tuple[InstrumentId, ...]
    values: NDArray[np.float64]

    def __post_init__(self) -> None:
        expected = (len(self.timestamps), len(self.instruments))
        if self.values.shape != expected:
            raise ValueError(
                f"values shape mismatch — expected {expected}, got {self.values.shape}"
            )
        self.values.setflags(write=False)

    def column(self, instrument: InstrumentId) -> NDArray[np.float64]:
        try:
            index = self.instruments.index(instrument)
        except ValueError:
            raise KeyError(
                f"instrument not in window — requested={instrument.symbol} "
                f"available={[i.symbol for i in self.instruments]}"
            ) from None
        return self.values[:, index]

    def is_complete(self, instrument: InstrumentId) -> bool:
        """해당 종목 열에 결측(NaN)이 없는지."""
        return not bool(np.isnan(self.column(instrument)).any())
