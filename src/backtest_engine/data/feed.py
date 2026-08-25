"""정제된 Bar를 시간순 MarketSnapshot 이터레이터로 바꾸는 DataFeed."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import datetime

from backtest_engine.errors import TimeReversalError
from backtest_engine.types.market import Bar, MarketSnapshot


class DataFeed:
    """동일 timestamp의 Bar를 모아 MarketSnapshot을 시간순으로 제공한다."""

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
        self._snapshots: tuple[MarketSnapshot, ...] = tuple(
            MarketSnapshot(ts=ts, bars=tuple(grouped[ts])) for ts in sorted(grouped)
        )

    @property
    def sessions(self) -> tuple[datetime, ...]:
        return tuple(snapshot.ts for snapshot in self._snapshots)

    def snapshots(self) -> Iterator[MarketSnapshot]:
        return iter(self._snapshots)

    def __len__(self) -> int:
        return len(self._snapshots)
