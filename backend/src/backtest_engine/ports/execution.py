"""체결 모델 포트: BrokerSim이 슬리피지 계산을 외부 구현에 위임하는 계약.

엔진은 이 프로토콜만 알고, 구체 모델(고정 bp, 거래량 비중 충격 등)은
`backtest_engine.engine.slippage`나 사용자 코드가 제공한다.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from backtest_engine.types.events import OrderEvent
from backtest_engine.types.market import Bar


class SlippageModel(Protocol):
    def slippage_per_share(
        self, order: OrderEvent, bar: Bar, base_price: float, quantity: Decimal
    ) -> float:
        """주당 슬리피지 금액 (항상 ≥ 0). 방향 적용(매수 +, 매도 −)은 브로커가 한다.

        Args:
            order: 체결 대상 주문.
            bar: 체결이 일어나는 세션 bar (거래량 기반 모델용).
            base_price: 슬리피지 적용 전 규칙상 체결 가격.
            quantity: 이번에 체결되는 수량.
        """
        ...
