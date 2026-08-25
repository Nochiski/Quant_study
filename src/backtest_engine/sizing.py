"""주문 수량 계산의 단일 진실 원천.

DecisionRouter와 외부 대조 하네스(Zipline diff)가 같은 함수를 import해서
수량 산정 규칙이 두 곳에서 어긋나지 않게 한다.
"""

from __future__ import annotations

from decimal import ROUND_FLOOR, Decimal


def floor_delta_shares(delta_notional: float, reference_price: float) -> Decimal:
    """목표 금액 변화량을 정수 주식 수로 변환한다 (절대값 floor).

    반환값은 항상 0 이상이며 방향(매수/매도)은 호출 측이 delta_notional의
    부호로 판단한다.
    """
    if reference_price <= 0:
        raise ValueError(
            f"reference price must be > 0 — reference_price={reference_price} "
            f"delta_notional={delta_notional}"
        )
    return Decimal(abs(delta_notional) / reference_price).quantize(
        Decimal(1), rounding=ROUND_FLOOR
    )
