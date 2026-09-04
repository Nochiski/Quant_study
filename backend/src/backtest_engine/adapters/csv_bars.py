"""CSV 어댑터: `date,open,high,low,close,volume` 파일을 BarSource 포트에 맞춘다.

파일 하나 = 종목 하나. 디렉토리 안에서 `{symbol}.csv`로 종목을 찾는다.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from backtest_engine.data.cleaning import RawBar, clean_raw_bars, merge_results
from backtest_engine.ports.market_data import BarQuery, LoadResult, LoadStatus, OhlcPolicy
from backtest_engine.types.instruments import InstrumentId

EXPECTED_HEADER = ("date", "open", "high", "low", "close", "volume")


def load_bars_csv(
    path: Path,
    instrument: InstrumentId,
    ohlc_policy: OhlcPolicy = OhlcPolicy.STRICT,
    start: datetime | None = None,
    end: datetime | None = None,
) -> LoadResult:
    """CSV 파일 하나를 시간순 Bar 튜플로 읽는다.

    v1에서 Bar.ts는 세션 마감을 나타내는 마커로, 날짜 자정(naive datetime)을 쓴다.
    행이 시간 역행하면 FORMAT_ERROR로 거절한다 (silent reorder 금지).
    """
    if not path.exists():
        return LoadResult(
            bars=(),
            status=LoadStatus.NO_DATA,
            detail=f"file not found — path={path} instrument={instrument.symbol}",
        )

    with path.open(newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = tuple(next(reader))
        except StopIteration:
            return LoadResult(
                bars=(),
                status=LoadStatus.NO_DATA,
                detail=f"empty file — path={path} instrument={instrument.symbol}",
            )
        if header != EXPECTED_HEADER:
            return LoadResult(
                bars=(),
                status=LoadStatus.FORMAT_ERROR,
                detail=(
                    f"unexpected header — path={path} expected={EXPECTED_HEADER} got={header}"
                ),
            )

        rows: list[RawBar] = []
        for line_number, row in enumerate(reader, start=2):
            try:
                raw = RawBar(
                    ts=datetime.strptime(row[0], "%Y-%m-%d"),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=int(row[5]),
                    origin=f"line={line_number}",
                )
            except (IndexError, ValueError) as error:
                return LoadResult(
                    bars=(),
                    status=LoadStatus.FORMAT_ERROR,
                    detail=(
                        f"invalid row — path={path} line={line_number} row={row!r} "
                        f"error={error!r}"
                    ),
                )
            if (start is not None and raw.ts < start) or (end is not None and raw.ts > end):
                continue
            rows.append(raw)

    if not rows:
        return LoadResult(
            bars=(),
            status=LoadStatus.NO_DATA,
            detail=(
                f"no data rows — path={path} instrument={instrument.symbol} "
                f"start={start} end={end}"
            ),
        )
    return clean_raw_bars(rows, instrument, ohlc_policy, source=str(path))


class CsvBarSource:
    """디렉토리의 `{symbol}.csv` 파일들을 BarSource로 노출한다."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def path_for(self, instrument: InstrumentId) -> Path:
        return self._root / f"{instrument.symbol}.csv"

    def load_bars(self, query: BarQuery) -> LoadResult:
        start = datetime.combine(query.start, datetime.min.time()) if query.start else None
        end = datetime.combine(query.end, datetime.min.time()) if query.end else None
        return merge_results(
            load_bars_csv(
                self.path_for(instrument),
                instrument,
                ohlc_policy=query.ohlc_policy,
                start=start,
                end=end,
            )
            for instrument in query.instruments
        )
