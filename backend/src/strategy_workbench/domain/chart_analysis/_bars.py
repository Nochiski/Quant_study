"""캔들 묶기(일·주·월)와 표시 배율 규칙의 owner.

어댑터는 일봉 행(`PriceBar`)만 나르고, 주봉·월봉 묶기·진행 중 봉 판정·전방 조정 공간과 화면 가격
사이의 배율은 여기 하나가 정한다(설계 문서 3장).
"""

from __future__ import annotations

import calendar
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum


class Timeframe(Enum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


@dataclass(frozen=True)
class PriceBar:
    """원장 일봉 한 행(종목은 전방 조정 OHLC, 지수는 지수 OHLC)."""

    session: date
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class Candle:
    """묶은 봉. `start`는 기간의 첫 거래일, `end`는 마지막 거래일이다.

    `in_progress`는 기준일이 기간 중간이라 아직 끝나지 않은 봉이다. 표시만 하고 판정·확인 봉에는
    쓰지 않는다.
    """

    start: date
    end: date
    open: float
    high: float
    low: float
    close: float
    in_progress: bool = False


def _period_key(session: date, timeframe: Timeframe) -> tuple[int, int, int]:
    if timeframe is Timeframe.DAY:
        return (session.year, session.month, session.day)
    if timeframe is Timeframe.WEEK:
        iso = session.isocalendar()
        return (iso.year, iso.week, 0)
    return (session.year, session.month, 0)


def _last_weekday(session: date, timeframe: Timeframe) -> date:
    """`session`이 든 기간의 마지막 평일(달력 기준). 이날 전이 기준일이면 그 봉은 진행 중이다."""
    if timeframe is Timeframe.DAY:
        return session
    if timeframe is Timeframe.WEEK:
        return session + timedelta(days=4 - session.weekday())
    last = date(session.year, session.month, calendar.monthrange(session.year, session.month)[1])
    return last - timedelta(days=max(0, last.weekday() - 4))


def aggregate_candles(
    bars: Sequence[PriceBar], timeframe: Timeframe, as_of: date
) -> tuple[Candle, ...]:
    """일봉을 같은 날·같은 주(월~금)·같은 달로 묶는다.

    시가 = 첫 일봉 시가, 고가 = 최대, 저가 = 최소, 종가 = 마지막 종가. 마지막 묶음만 진행 중일 수
    있고, 기준일 `as_of`가 그 기간의 마지막 평일보다 앞이면 진행 중이다(그 평일이 휴장이면 다음
    기간 봉이 생길 때까지 진행 중으로 남는 보수 판정).

    Raises:
        ValueError: 일봉 날짜가 오름차순·중복 없음이 아니거나 기준일 뒤의 일봉이 있을 때(룩어헤드).
    """
    for previous, current in zip(bars, bars[1:], strict=False):
        if current.session <= previous.session:
            raise ValueError(
                "daily bars must be strictly increasing — "
                f"previous={previous.session} current={current.session} rows={len(bars)}"
            )
    if bars and bars[-1].session > as_of:
        raise ValueError(f"daily bar after as_of — last_session={bars[-1].session} as_of={as_of}")
    groups: list[list[PriceBar]] = []
    for bar in bars:
        if groups and _period_key(groups[-1][0].session, timeframe) == _period_key(
            bar.session, timeframe
        ):
            groups[-1].append(bar)
        else:
            groups.append([bar])
    candles = [
        Candle(
            start=group[0].session,
            end=group[-1].session,
            open=group[0].open,
            high=max(bar.high for bar in group),
            low=min(bar.low for bar in group),
            close=group[-1].close,
        )
        for group in groups
    ]
    if candles and as_of < _last_weekday(candles[-1].start, timeframe):
        last = candles[-1]
        candles[-1] = Candle(last.start, last.end, last.open, last.high, last.low, last.close, True)
    return tuple(candles)


@dataclass(frozen=True)
class DisplayScale:
    """저장 좌표(전방 조정 공간) × `factor` = 화면 가격. 지수는 `DisplayScale(1.0)`."""

    factor: float

    def __post_init__(self) -> None:
        if not (math.isfinite(self.factor) and self.factor > 0):
            raise ValueError(f"display scale must be finite and positive — factor={self.factor}")

    @classmethod
    def from_last_close(cls, actual_close: float, adjusted_close: float) -> DisplayScale:
        """`k = 마지막 실제 종가 ÷ 마지막 전방 조정 종가`."""
        if not (adjusted_close > 0 and actual_close > 0):
            raise ValueError(
                "closes must be positive — "
                f"actual_close={actual_close} adjusted_close={adjusted_close}"
            )
        return cls(actual_close / adjusted_close)

    def to_display(self, stored_price: float) -> float:
        return stored_price * self.factor

    def to_stored(self, display_price: float) -> float:
        """사용자가 화면 가격으로 입력한 값을 저장 좌표로 바꾼다(÷k)."""
        return display_price / self.factor
