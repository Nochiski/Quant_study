"""정제된 Bar를 시간순 MarketSnapshot 이터레이터로 바꾸는 DataFeed."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime

from backtest_engine.errors import TimeReversalError
from backtest_engine.types.instruments import InstrumentId
from backtest_engine.types.market import Bar, MarketSnapshot, validate_bar_values


@dataclass(frozen=True)
class FeedColumns:
    """세션 경계로 나뉜 bar 행의 열(column) 묶음.

    행 `i`는 종목 `instruments[instrument_ids[i]]`의 bar이고, 세션 `s`의 행 구간은
    `offsets[s] : offsets[s + 1]`이다. `offsets`의 길이는 세션 수 + 1이다.

    열은 읽기 전용으로 다룬다 — persistent Rust 경로가 이 열을 그대로 FFI로 넘기므로
    복사본을 만들지 않는다. 받은 쪽이 원소를 바꾸면 feed가 조용히 달라진다.
    """

    instruments: tuple[InstrumentId, ...]
    offsets: Sequence[int]
    instrument_ids: Sequence[int]
    opens: Sequence[float]
    highs: Sequence[float]
    lows: Sequence[float]
    closes: Sequence[float]
    volumes: Sequence[int]


class DataFeed:
    """동일 timestamp의 Bar를 모아 MarketSnapshot을 시간순으로 제공한다.

    내부 저장은 두 표현 중 하나로 시작한다. `DataFeed(bars)`는 세션 스냅샷을 바로 갖고
    `columns()`가 처음 불릴 때 열을 만든다. `DataFeed.from_columns(...)`는 열을 갖고
    `MarketSnapshot`을 세션 단위로 요청 시에만 만들어 캐시한다. persistent Rust 경로는
    열만 읽고 스냅샷 객체를 쓰지 않으므로, 열로 적재한 feed는 객체를 한 개도 만들지 않는다.
    """

    _sessions: tuple[datetime, ...]
    _columns: FeedColumns | None
    _snapshots: list[MarketSnapshot | None]

    def __init__(self, bars: Iterable[Bar]) -> None:
        grouped: dict[datetime, list[Bar]] = {}
        per_instrument_last: dict[object, datetime] = {}
        for bar in bars:
            last_ts = per_instrument_last.get(bar.instrument)
            if last_ts is not None and bar.ts <= last_ts:
                raise TimeReversalError(
                    f"bar timestamps must be strictly increasing per instrument — "
                    f"instrument={bar.instrument.symbol} previous={last_ts} got={bar.ts}"
                )
            per_instrument_last[bar.instrument] = bar.ts
            grouped.setdefault(bar.ts, []).append(bar)
        sessions = sorted(grouped)
        self._sessions = tuple(sessions)
        # 열은 persistent 경로에서만 쓰인다 — Python 경로가 치르지 않도록 요청 시 만든다.
        self._columns = None
        self._snapshots = [
            MarketSnapshot(ts=ts, bars=tuple(grouped[ts])) for ts in sessions
        ]

    @classmethod
    def from_columns(
        cls,
        *,
        sessions: Sequence[datetime],
        instruments: Sequence[InstrumentId],
        offsets: Sequence[int],
        instrument_ids: Sequence[int],
        opens: Sequence[float],
        highs: Sequence[float],
        lows: Sequence[float],
        closes: Sequence[float],
        volumes: Sequence[int],
    ) -> DataFeed:
        """이미 세션순으로 묶인 열에서 feed를 만든다 (`Bar` 객체 생성 없음).

        `DataFeed(bars)`와 같은 불변식을 같은 메시지로 검사한다 — 세션은 strictly
        increasing, 한 세션 안에 같은 종목이 두 번 오지 않으며(그래서 종목별 세션도
        strictly increasing), 각 행은 `validate_bar_values`를 통과한다.

        Args:
            sessions: 봉 마감 시각. strictly increasing이어야 한다.
            instruments: 등록 순서의 종목. `instrument_ids`가 이 순서를 가리킨다.
            offsets: 세션별 행 구간 경계. 길이는 `len(sessions) + 1`, 첫 값은 0.
            instrument_ids: 행별 `instruments` 인덱스.
            opens/highs/lows/closes/volumes: 행별 시가·고가·저가·종가·거래량.

        Returns:
            열을 그대로 보관하고 스냅샷을 요청 시 만드는 DataFeed.

        Raises:
            TimeReversalError: 세션이 strictly increasing이 아닐 때.
            ValueError: 열 길이·`offsets`·종목 인덱스가 어긋나거나, 한 세션에 같은 종목이
                두 번 오거나, 행이 가격·거래량 불변식을 어길 때.
        """
        rows = len(instrument_ids)
        lengths = (len(opens), len(highs), len(lows), len(closes), len(volumes))
        if any(length != rows for length in lengths):
            raise ValueError(
                "feed column lengths must match — "
                f"instrument_ids={rows} opens={lengths[0]} highs={lengths[1]} "
                f"lows={lengths[2]} closes={lengths[3]} volumes={lengths[4]}"
            )
        if len(offsets) != len(sessions) + 1:
            raise ValueError(
                "feed offsets must hold one boundary per session plus a final end — "
                f"expected {len(sessions) + 1}, got {len(offsets)} (sessions={len(sessions)})"
            )
        if offsets and (offsets[0] != 0 or offsets[-1] != rows):
            raise ValueError(
                "feed offsets must start at 0 and end at the row count — "
                f"first={offsets[0]} last={offsets[-1]} rows={rows}"
            )
        instrument_count = len(instruments)
        previous_ts: datetime | None = None
        for session_index, ts in enumerate(sessions):
            if previous_ts is not None and ts <= previous_ts:
                raise TimeReversalError(
                    "feed sessions must be strictly increasing — "
                    f"index={session_index} previous={previous_ts} got={ts}"
                )
            previous_ts = ts
            start = offsets[session_index]
            end = offsets[session_index + 1]
            if end < start:
                raise ValueError(
                    "feed offsets must be non-decreasing — "
                    f"session_index={session_index} ts={ts} start={start} end={end}"
                )
            seen: set[int] = set()
            for row in range(start, end):
                instrument_id = instrument_ids[row]
                if not 0 <= instrument_id < instrument_count:
                    raise ValueError(
                        "feed instrument id out of range — "
                        f"row={row} ts={ts} instrument_id={instrument_id} "
                        f"registry={instrument_count}"
                    )
                if instrument_id in seen:
                    raise ValueError(
                        f"duplicate instrument in snapshot — ts={ts} "
                        f"instrument={instruments[instrument_id].symbol}"
                    )
                seen.add(instrument_id)
                validate_bar_values(
                    instruments[instrument_id].symbol,
                    ts,
                    opens[row],
                    highs[row],
                    lows[row],
                    closes[row],
                    volumes[row],
                )

        feed = cls.__new__(cls)
        # `__init__`은 Bar 목록이라는 다른 표현을 받는다 — 여기서는 검증을 마친 열을 그대로
        # 심는다 (대안 생성자 패턴).
        feed._sessions = tuple(sessions)
        feed._columns = FeedColumns(
            instruments=tuple(instruments),
            offsets=offsets,
            instrument_ids=instrument_ids,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            volumes=volumes,
        )
        feed._snapshots = [None] * len(sessions)
        return feed

    @property
    def sessions(self) -> tuple[datetime, ...]:
        return self._sessions

    def columns(self) -> FeedColumns:
        """persistent 경로가 그대로 넘길 열. Bar로 만든 feed는 첫 호출에서 열을 만든다."""
        columns = self._columns
        if columns is None:
            columns = self._columns_from_snapshots()
            self._columns = columns
        return columns

    def snapshot_at(self, index: int) -> MarketSnapshot:
        """세션 index의 스냅샷. 열로 만든 feed는 이때 Bar 객체를 만들어 캐시한다."""
        cached = self._snapshots[index]
        if cached is not None:
            return cached
        built = self._build_snapshot(index)
        self._snapshots[index] = built
        return built

    def snapshots(self) -> Iterator[MarketSnapshot]:
        return (self.snapshot_at(index) for index in range(len(self._sessions)))

    def __len__(self) -> int:
        return len(self._sessions)

    def _build_snapshot(self, index: int) -> MarketSnapshot:
        columns = self._columns
        if columns is None:
            raise RuntimeError(
                "feed has neither a cached snapshot nor columns to build it from — "
                f"session_index={index} sessions={len(self._sessions)}"
            )
        ts = self._sessions[index]
        instruments = columns.instruments
        instrument_ids = columns.instrument_ids
        opens = columns.opens
        highs = columns.highs
        lows = columns.lows
        closes = columns.closes
        volumes = columns.volumes
        return MarketSnapshot(
            ts=ts,
            bars=tuple(
                Bar(
                    ts=ts,
                    instrument=instruments[instrument_ids[row]],
                    open=opens[row],
                    high=highs[row],
                    low=lows[row],
                    close=closes[row],
                    volume=volumes[row],
                )
                for row in range(columns.offsets[index], columns.offsets[index + 1])
            ),
        )

    def _columns_from_snapshots(self) -> FeedColumns:
        """Bar로 만든 feed를 열로 편다. 종목 순서는 세션순으로 훑은 첫 등장 순서다."""
        registry: dict[InstrumentId, int] = {}
        offsets = [0]
        instrument_ids: list[int] = []
        opens: list[float] = []
        highs: list[float] = []
        lows: list[float] = []
        closes: list[float] = []
        volumes: list[int] = []
        for index in range(len(self._sessions)):
            for bar in self.snapshot_at(index).bars:
                instrument_id = registry.get(bar.instrument)
                if instrument_id is None:
                    instrument_id = len(registry)
                    registry[bar.instrument] = instrument_id
                instrument_ids.append(instrument_id)
                opens.append(bar.open)
                highs.append(bar.high)
                lows.append(bar.low)
                closes.append(bar.close)
                volumes.append(bar.volume)
            offsets.append(len(instrument_ids))
        return FeedColumns(
            instruments=tuple(registry),
            offsets=offsets,
            instrument_ids=instrument_ids,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            volumes=volumes,
        )
