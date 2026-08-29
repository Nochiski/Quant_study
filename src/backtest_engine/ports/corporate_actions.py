"""자본변동 포트: 엔진이 액면분할·병합 같은 주식 수 변동 사건을 얻기 위한 계약.

엔진은 `CorporateActionSource`와 `CorporateActionQuery`/`CorporateActionResult`만 알고,
원장 컬럼·검출 휴리스틱은 어댑터와 `data.corporate_actions`가 맡는다.
부분 성공 금지: 요청 종목 중 하나라도 데이터가 없으면 전체 NO_DATA.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from backtest_engine.ports.market_data import LoadStatus
from backtest_engine.types.events import CorporateActionEvent
from backtest_engine.types.instruments import InstrumentId


@dataclass(frozen=True)
class CorporateActionQuery:
    """어떤 종목의 어느 기간 사건이 필요한지. 기간은 사건 세션 기준 양끝 포함."""

    instruments: tuple[InstrumentId, ...]
    start: date | None = None
    end: date | None = None

    def __post_init__(self) -> None:
        if not self.instruments:
            raise ValueError(
                "CorporateActionQuery requires at least one instrument — got empty tuple"
            )
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError(
                f"CorporateActionQuery start must be <= end — start={self.start} end={self.end}"
            )

    def includes(self, session: date) -> bool:
        if self.start is not None and session < self.start:
            return False
        return self.end is None or session <= self.end


@dataclass(frozen=True)
class CorporateActionResult:
    actions: tuple[CorporateActionEvent, ...]
    status: LoadStatus
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is LoadStatus.OK


class CorporateActionSource(Protocol):
    def load_actions(self, query: CorporateActionQuery) -> CorporateActionResult: ...
