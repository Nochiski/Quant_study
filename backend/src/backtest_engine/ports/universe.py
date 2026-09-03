"""유니버스 포트: 어느 세션에 어떤 종목이 상장돼 있었는지를 look-ahead 없이 얻는 계약.

결과는 종목별 구간(첫 세션·마지막 세션)이다. 엔진은 `UniverseResult.members(session)`로
그 세션의 구성을 읽고, 호출자는 `instruments_active_between`으로 BarQuery 종목을 고른다.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
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
    # 조회 인덱스 (값 동등성·해시에서 제외). 첫 조회 때 구간을 first_session 순으로 정렬한다.
    _cache: dict[date, frozenset[InstrumentId]] = field(
        default_factory=dict, repr=False, compare=False
    )
    _sorted: list[Membership] = field(default_factory=list, repr=False, compare=False)
    _starts: list[date] = field(default_factory=list, repr=False, compare=False)

    @property
    def ok(self) -> bool:
        return self.status is LoadStatus.OK

    def members(self, session: date) -> frozenset[InstrumentId]:
        """세션 d의 구성. first_session ≤ d 인 구간만 bisect로 잘라 보고, 세션별 결과를 메모한다."""
        cached = self._cache.get(session)
        if cached is not None:
            return cached
        if not self._sorted and self.memberships:
            self._sorted.extend(sorted(self.memberships, key=lambda m: m.first_session))
            self._starts.extend(m.first_session for m in self._sorted)
        end = bisect.bisect_right(self._starts, session)
        result = frozenset(m.instrument for m in self._sorted[:end] if m.last_session >= session)
        self._cache[session] = result
        return result

    def instruments_active_between(self, start: date, end: date) -> tuple[InstrumentId, ...]:
        """기간과 겹치는 구간이 하나라도 있는 종목 (심볼 정렬, 중복 제거)."""
        found = {m.instrument for m in self.memberships if m.overlaps(start, end)}
        return tuple(sorted(found, key=lambda i: (i.venue, i.symbol)))


class UniverseSource(Protocol):
    def load_universe(self, query: UniverseQuery) -> UniverseResult: ...
