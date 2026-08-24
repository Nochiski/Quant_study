"""date,open,high,low,close,volume 형식 CSV를 Bar 튜플로 변환하는 로더.

예상된 도메인 실패(파일 없음, 빈 파일, 형식 오류)는 예외가 아니라
LoadResult 상태 값으로 보고한다 (errors-as-values).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from backtest_engine.types.instruments import InstrumentId
from backtest_engine.types.market import Bar

EXPECTED_HEADER = ("date", "open", "high", "low", "close", "volume")


class LoadStatus(Enum):
    OK = "ok"
    NO_DATA = "no_data"
    FORMAT_ERROR = "format_error"


class OhlcPolicy(Enum):
    """OHLC 불변조건(high ≥ open/close, low ≤ open/close) 위반 행 처리 정책.

    실제 벤더 데이터(예: PyKRX 수정주가)에는 close가 high를 넘는 행이
    존재한다. STRICT는 그런 행을 FORMAT_ERROR로 거절하고, CLAMP는
    high/low를 몸통을 포함하도록 넓힌 뒤 보정 행 수를 결과에 남긴다.
    """

    STRICT = "strict"
    CLAMP = "clamp"


@dataclass(frozen=True)
class LoadResult:
    bars: tuple[Bar, ...]
    status: LoadStatus
    detail: str | None = None
    repaired_rows: int = 0  # CLAMP 정책으로 high/low를 보정한 행 수

    @property
    def ok(self) -> bool:
        return self.status is LoadStatus.OK


def load_bars_csv(
    path: Path,
    instrument: InstrumentId,
    ohlc_policy: OhlcPolicy = OhlcPolicy.STRICT,
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

        bars: list[Bar] = []
        previous_ts: datetime | None = None
        repaired_rows = 0
        for line_number, row in enumerate(reader, start=2):
            try:
                ts = datetime.strptime(row[0], "%Y-%m-%d")
                open_price, high, low, close = (
                    float(row[1]),
                    float(row[2]),
                    float(row[3]),
                    float(row[4]),
                )
                if ohlc_policy is OhlcPolicy.CLAMP:
                    clamped_high = max(high, open_price, close)
                    clamped_low = min(low, open_price, close)
                    if clamped_high != high or clamped_low != low:
                        repaired_rows += 1
                        high, low = clamped_high, clamped_low
                bar = Bar(
                    ts=ts,
                    instrument=instrument,
                    open=open_price,
                    high=high,
                    low=low,
                    close=close,
                    volume=int(row[5]),
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
            if previous_ts is not None and bar.ts <= previous_ts:
                return LoadResult(
                    bars=(),
                    status=LoadStatus.FORMAT_ERROR,
                    detail=(
                        f"timestamps must be strictly increasing — path={path} "
                        f"line={line_number} previous={previous_ts.date()} got={bar.ts.date()}"
                    ),
                )
            previous_ts = bar.ts
            bars.append(bar)

    if not bars:
        return LoadResult(
            bars=(),
            status=LoadStatus.NO_DATA,
            detail=f"no data rows — path={path} instrument={instrument.symbol}",
        )
    return LoadResult(bars=tuple(bars), status=LoadStatus.OK, repaired_rows=repaired_rows)
