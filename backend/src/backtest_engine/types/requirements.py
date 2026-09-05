"""전략이 실행 전에 선언하는 요구사항.

전략마다 새로운 요구사항 클래스를 만들지 않고 StrategyRequirements에
필요한 값만 채운다. 엔진은 이 값을 보고 데이터를 준비하고,
현재 구현으로 실행할 수 있는 전략인지 미리 확인한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from backtest_engine.types.actions import ActionKind
from backtest_engine.types.instruments import InstrumentId
from backtest_engine.types.market import PriceField


@dataclass(frozen=True)
class HistoryRequest:
    """전략이 사용할 과거 데이터 창 하나."""

    instruments: tuple[InstrumentId, ...]
    field: PriceField
    lookback: int

    def __post_init__(self) -> None:
        if self.lookback <= 0:
            raise ValueError(
                f"lookback must be > 0 — got lookback={self.lookback} "
                f"instruments={[i.symbol for i in self.instruments]}"
            )
        if not self.instruments:
            raise ValueError(f"instruments must not be empty — field={self.field.value}")


@dataclass(frozen=True)
class EverySession:
    """매 거래일(세션)마다 전략을 호출한다."""


@dataclass(frozen=True)
class MonthEndSession:
    """매월 마지막 거래일에만 전략을 호출한다. (스키마만 정의, v1 미구현)"""


Schedule = EverySession | MonthEndSession


class EventKind(Enum):
    MARKET = "market"
    TIMER = "timer"
    FILL = "fill"
    ORDER_UPDATE = "order_update"
    CORPORATE_ACTION = "corporate_action"


class EngineFeature(Enum):
    SHORT_SELLING = "short_selling"
    MARGIN = "margin"
    PARTIAL_FILL = "partial_fill"
    LIMIT_ORDER = "limit_order"
    STOP_ORDER = "stop_order"
    PROPORTIONAL_BASKET = "proportional_basket"


@dataclass(frozen=True)
class StrategyRequirements:
    """모든 전략이 같은 타입을 반환하고 필드 값만 다르게 채운다."""

    histories: tuple[HistoryRequest, ...]
    schedule: Schedule
    events: frozenset[EventKind]
    actions: frozenset[ActionKind]
    features: frozenset[EngineFeature]
