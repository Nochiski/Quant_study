"""모든 전략이 지키는 함수 모양.

엔진은 전략의 이름이나 내부 공식을 알 필요가 없다. 실행 전에
requirements()를 한 번 부르고, 실행 중에는 on_event()를 반복해서 부른다.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Protocol

from backtest_engine.types.decision import StrategyDecision
from backtest_engine.types.events import OpenOrderSnapshot, StrategyEvent
from backtest_engine.types.instruments import InstrumentId
from backtest_engine.types.market import PriceWindow
from backtest_engine.types.requirements import HistoryRequest, StrategyRequirements


class StrategyContext(Protocol):
    """전략이 판단 재료를 읽는 조회 전용 창구.

    엔진이 매 호출마다 만들어 함수 인자로 명시적으로 건네며,
    전략은 여기서 현재 상태를 읽기만 한다.
    """

    @property
    def now(self) -> datetime:
        """현재 전략 호출의 기준 시각."""
        ...

    def history(self, request: HistoryRequest) -> PriceWindow:
        """선언한 과거 데이터만 읽기 전용 PriceWindow로 받는다."""
        ...

    def current_weight(self, instrument: InstrumentId) -> float: ...

    def position_qty(self, instrument: InstrumentId) -> Decimal: ...

    def cash(self) -> float: ...

    def portfolio_value(self) -> float: ...

    def open_orders(self, instrument: InstrumentId | None = None) -> tuple[OpenOrderSnapshot, ...]:
        """이 호출 시점에 대기 중인 주문과 잔량. Cancel/Replace의 order_id·수량 출처."""
        ...


class Strategy(Protocol):
    def requirements(self) -> StrategyRequirements:
        """실행 전에 필요한 데이터와 호출 일정을 엔진에 알린다."""
        ...

    def on_event(self, ctx: StrategyContext, event: StrategyEvent) -> StrategyDecision:
        """모든 전략은 같은 입력과 출력 타입을 사용한다."""
        ...
