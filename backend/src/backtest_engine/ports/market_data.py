"""시장 데이터 포트: 엔진이 Bar를 얻기 위해 외부 소스에 요구하는 계약.

엔진/전략 쪽은 `BarSource` 프로토콜과 `BarQuery`/`LoadResult` 값 타입만 알면 된다.
CSV·parquet·외부 DB 등 소스별 차이는 어댑터가 흡수한다.

예상된 도메인 실패(파일 없음, 형식 오류, 요청 종목 없음)는 예외가 아니라
`LoadResult.status`로 보고한다 (errors-as-values).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Protocol

from backtest_engine.types.instruments import InstrumentId
from backtest_engine.types.market import Bar


class LoadStatus(Enum):
    OK = "ok"
    NO_DATA = "no_data"
    FORMAT_ERROR = "format_error"


class OhlcPolicy(Enum):
    """OHLC 불변조건(high ≥ open/close, low ≤ open/close) 위반 행 처리 정책.

    실제 벤더 데이터(PyKRX 수정주가, KRX 원장)에는 close가 high를 넘는 행과
    가격이 0인 거래정지 행이 존재한다. STRICT는 그런 행을 FORMAT_ERROR로
    거절한다. CLAMP는 high/low를 몸통을 포함하도록 넓히고(보정 행 수 기록),
    가격이 0 이하인 행은 거래 불가 마커로 보고 drop한다(제거 행 수 기록).
    """

    STRICT = "strict"
    CLAMP = "clamp"


@dataclass(frozen=True)
class LoadResult:
    """소스에서 읽은 Bar와 정제 통계. silent 보정 금지 — 손댄 행 수는 항상 보고한다."""

    bars: tuple[Bar, ...]
    status: LoadStatus
    detail: str | None = None
    repaired_rows: int = 0  # CLAMP 정책으로 high/low를 보정한 행 수
    dropped_rows: int = 0  # 거래 불가 행(비양수 가격·거래량 0 등)으로 제거한 행 수

    @property
    def ok(self) -> bool:
        return self.status is LoadStatus.OK


@dataclass(frozen=True)
class BarQuery:
    """어떤 종목의 어느 기간 Bar가 필요한지. 기간 경계는 양끝 포함, None이면 무제한."""

    instruments: tuple[InstrumentId, ...]
    start: date | None = None
    end: date | None = None
    ohlc_policy: OhlcPolicy = OhlcPolicy.STRICT

    def __post_init__(self) -> None:
        if not self.instruments:
            raise ValueError("BarQuery requires at least one instrument — got empty tuple")
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError(
                f"BarQuery start must be <= end — start={self.start} end={self.end}"
            )
        seen = set()
        for instrument in self.instruments:
            if instrument in seen:
                raise ValueError(
                    f"duplicate instrument in BarQuery — symbol={instrument.symbol} "
                    f"venue={instrument.venue}"
                )
            seen.add(instrument)

    def includes(self, session: date) -> bool:
        if self.start is not None and session < self.start:
            return False
        if self.end is not None and session > self.end:
            return False
        return True


class BarSource(Protocol):
    """Bar 공급 포트. 어댑터는 이 메서드 하나만 구현하면 된다.

    계약:
    - 반환 bars는 종목별로 시간 오름차순이며 timestamp가 역행하지 않는다.
    - 요청한 종목 중 하나라도 데이터가 전혀 없으면 NO_DATA (부분 성공 금지 —
      한 종목이 조용히 빠진 채 백테스트가 돌면 결과가 왜곡된다).
    - 정제로 손댄 행 수는 repaired_rows / dropped_rows로 보고한다.
    """

    def load_bars(self, query: BarQuery) -> LoadResult: ...
