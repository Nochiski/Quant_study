"""유니버스 포트: 어느 세션에 어떤 종목이 상장돼 있었는지를 look-ahead 없이 얻는 계약.

결과는 종목별 구간(첫 세션·마지막 세션)이다. 엔진은 `UniverseResult.members(session)`로
그 세션의 구성을 읽고, 호출자는 `instruments_active_between`으로 BarQuery 종목을 고른다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from backtest_engine.ports.market_data import LoadStatus
from backtest_engine.types.instruments import InstrumentId


@dataclass(frozen=True)
class UniverseQuery:
    """거래소와 기간. 기간은 양끝 포함, None이면 무제한."""

    venue: str
    start: date | None = None
    end: date | None = None

    def __post_init__(self) -> None:
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError(
                f"UniverseQuery start must be <= end — start={self.start} end={self.end}"
            )


@dataclass(frozen=True)
class Membership:
    """종목이 유니버스에 속한 연속 구간 (양끝 포함)."""

    instrument: InstrumentId
    first_session: date
    last_session: date

    def __post_init__(self) -> None:
        if self.first_session > self.last_session:
            raise ValueError(
                f"Membership first_session must be <= last_session — "
                f"instrument={self.instrument.symbol} first={self.first_session} "
                f"last={self.last_session}"
            )

    def active_on(self, session: date) -> bool:
        return self.first_session <= session <= self.last_session

    def overlaps(self, start: date, end: date) -> bool:
        return self.first_session <= end and self.last_session >= start


@dataclass(frozen=True)
class UniverseResult:
    memberships: tuple[Membership, ...]
    status: LoadStatus
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is LoadStatus.OK

    def members(self, session: date) -> frozenset[InstrumentId]:
        return frozenset(m.instrument for m in self.memberships if m.active_on(session))

    def instruments_active_between(self, start: date, end: date) -> tuple[InstrumentId, ...]:
        """기간과 겹치는 구간이 하나라도 있는 종목 (심볼 정렬, 중복 제거)."""
        found = {m.instrument for m in self.memberships if m.overlaps(start, end)}
        return tuple(sorted(found, key=lambda i: (i.venue, i.symbol)))


class UniverseSource(Protocol):
    def load_universe(self, query: UniverseQuery) -> UniverseResult: ...
