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
        # 같은 InstrumentId가 두 index를 차지하면 세션 안 중복 검사(index 기준)를 통과한
        # 채로 같은 종목이 한 스냅샷에 두 번 담긴다. 그러면 `MarketSnapshot`이 실행 도중
        # `snapshot_at`에서야 거부하고, Rust 적재는 등록부 key 하나에 심볼 둘로 어긋난다.
        first_index_of: dict[InstrumentId, int] = {}
        for index, instrument in enumerate(instruments):
            first_index = first_index_of.setdefault(instrument, index)
            if first_index != index:
                raise ValueError(
                    "duplicate instrument in feed registry — "
                    f"instrument={instrument.symbol} indices=[{first_index}, {index}] "
                    f"registry={instrument_count}"
                )
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
        """persistent 경로가 그대로 넘길 열.

        `from_columns`로 만든 feed는 보관 중인 열을 그대로 돌려준다. `DataFeed(bars)`로
        만든 feed는 호출마다 스냅샷에서 새로 판다 — 캐시하면 파생 열(포인터 리스트 6개,
        123,100행 기준 약 5.8MiB)이 실행 내내 상주하는데, 부르는 쪽은 완주 실행의 적재
        1회(`_load_persistent_feed`)와 중단된 실행의 종목 조회표(`_covered_feed`)뿐이라
        그 상주를 정당화하지 못한다 (DEFECT-1002).
        """
        columns = self._columns
        if columns is None:
            return self._columns_from_snapshots()
        return columns

    def snapshot_at(self, index: int) -> MarketSnapshot:
        """세션 index의 스냅샷. 열로 만든 feed는 이때 Bar 객체를 만들어 캐시한다.

        Raises:
            IndexError: index가 세션 범위 밖일 때. 음수를 막는 이유는 Python 리스트 규칙으로
                뒤에서 세어 버리면 세션 경계가 뒤집혀(`offsets[index] > offsets[index + 1]`)
                빈 스냅샷을 조용히 돌려주기 때문이다 — 세션 index는 Rust가 보내는 0 기반
                위치다.
        """
        if index < 0:
            raise IndexError(
                f"feed session index must be >= 0 — got {index} sessions={len(self._sessions)}"
            )
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
