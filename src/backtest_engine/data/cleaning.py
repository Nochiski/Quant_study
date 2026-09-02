"""소스 무관 Bar 정제 로직: OHLC 정책 적용, 거래정지 행 제거, 시간 역행 검사.

어댑터는 파일/DB에서 `RawBar`만 뽑아 오고, Bar 생성과 정제 통계는 여기서 한 번만 한다.
정제 규칙이 두 어댑터에 흩어지면 한쪽만 고쳐지는 silent drift가 생긴다.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from backtest_engine.ports.market_data import LoadResult, LoadStatus, OhlcPolicy
from backtest_engine.types.instruments import InstrumentId
from backtest_engine.types.market import Bar


@dataclass(frozen=True)
class RawBar:
    """소스에서 그대로 읽은 한 행. 아직 검증되지 않은 값이다."""

    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    origin: str  # 진단용 위치 단서 (예: "line=12", "row=3401")


def clean_raw_bars(
    rows: Iterable[RawBar],
    instrument: InstrumentId,
    ohlc_policy: OhlcPolicy,
    source: str,
    *,
    drop_zero_volume: bool = False,
) -> LoadResult:
    """RawBar 시퀀스를 검증된 Bar 튜플로 바꾼다.

    Args:
        rows: 시간 오름차순이어야 하는 원시 행. 역행하면 FORMAT_ERROR (silent reorder 금지).
        instrument: 결과 Bar에 붙일 종목 식별자.
        ohlc_policy: STRICT는 OHLC 위반·비양수 가격을 거절, CLAMP는 보정·제거 후 집계.
        source: 진단 메시지용 소스 라벨 (파일 경로, 테이블명 등).
        drop_zero_volume: True면 거래량 0 행을 거래정지로 보고 제거한다 (KRX 원장은
            정지 중에도 종가를 유지한 행이 매일 존재해 가격만으로는 판별 불가).

    Returns:
        정제된 Bar와 repaired_rows/dropped_rows 통계를 담은 LoadResult.
    """
    bars: list[Bar] = []
    previous_ts: datetime | None = None
    repaired_rows = 0
    dropped_rows = 0

    for row in rows:
        if previous_ts is not None and row.ts <= previous_ts:
            return LoadResult(
                bars=(),
                status=LoadStatus.FORMAT_ERROR,
                detail=(
                    f"timestamps must be strictly increasing — source={source} "
                    f"instrument={instrument.symbol} {row.origin} "
                    f"previous={previous_ts.date()} got={row.ts.date()}"
                ),
            )
        previous_ts = row.ts

        if drop_zero_volume and row.volume == 0:
            dropped_rows += 1
            continue

        open_price, high, low, close = row.open, row.high, row.low, row.close
        if ohlc_policy is OhlcPolicy.CLAMP:
            if min(open_price, high, low, close) <= 0:
                # 거래정지 마커(가격 0) — 세션 자체가 없는 것으로 취급한다.
                dropped_rows += 1
                continue
            clamped_high = max(high, open_price, close)
            clamped_low = min(low, open_price, close)
            if clamped_high != high or clamped_low != low:
                repaired_rows += 1
                high, low = clamped_high, clamped_low

        try:
            bar = Bar(
                ts=row.ts,
                instrument=instrument,
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=row.volume,
            )
        except ValueError as error:
            return LoadResult(
                bars=(),
                status=LoadStatus.FORMAT_ERROR,
                detail=(
                    f"invalid row — source={source} instrument={instrument.symbol} "
                    f"{row.origin} error={error!r}"
                ),
            )
        bars.append(bar)

    if not bars:
        return LoadResult(
            bars=(),
            status=LoadStatus.NO_DATA,
            detail=(
                f"no usable rows — source={source} instrument={instrument.symbol} "
                f"dropped={dropped_rows}"
            ),
        )
    return LoadResult(
        bars=tuple(bars),
        status=LoadStatus.OK,
        repaired_rows=repaired_rows,
        dropped_rows=dropped_rows,
    )


def merge_results(results: Iterable[LoadResult]) -> LoadResult:
    """종목별 LoadResult를 하나로 합친다. 하나라도 실패면 전체 실패 (부분 성공 금지)."""
    bars: list[Bar] = []
    repaired_rows = 0
    dropped_rows = 0
    for result in results:
        if not result.ok:
            return result
        bars.extend(result.bars)
        repaired_rows += result.repaired_rows
        dropped_rows += result.dropped_rows
    return LoadResult(
        bars=tuple(bars),
        status=LoadStatus.OK,
        repaired_rows=repaired_rows,
        dropped_rows=dropped_rows,
    )
