"""체결 후 점검(S07 리스크 가드 ③, 실행일 장 마감). 판단에 쓰지 않고 기록만 한다.

- 위반: 리스크가 허용한 수량보다 더 산 매수, 그날 주문 직전 판정이 막은 종목의 매수 체결. 가드가
  제대로 돌면 생기지 않는다 — 생기면 가드 결함이다.
- 기록: 장 마감가로 단일 종목 비중 상한을 넘은 종목(값이 오른 것이라 위반이 아니다).
- 주문별 미체결 비율은 리스크가 줄인 뒤의 수량에 견준다.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from strategy_workbench.domain.risk._limits import BP, RiskLimits


@dataclass(frozen=True)
class ReviewOrder:
    """그날 한 종목·방향. 수량은 모두 주식 수이고 `shortfall_bp`는 기존 체결 차이다."""

    symbol: str
    side: str
    plan_quantity: int  # 주문안(전략)이 정한 수량
    guarded_quantity: int  # 리스크가 줄인 뒤 수량
    filled_quantity: int
    shortfall_bp: int | None


@dataclass(frozen=True)
class ReviewedOrder:
    symbol: str
    side: str
    plan_quantity: int
    guarded_quantity: int
    filled_quantity: int
    reduced_by_risk: int
    unfilled_bp: int | None  # 리스크 뒤 수량이 0이면 None
    shortfall_bp: int | None


@dataclass(frozen=True)
class PostTradeReview:
    orders: tuple[ReviewedOrder, ...]
    violations: tuple[str, ...]
    notes: tuple[str, ...]


def review_day(
    orders: Sequence[ReviewOrder],
    *,
    blocked_buys: Collection[str],
    holdings: Sequence[tuple[str, int, int]],
    equity_krw: int,
    limits: RiskLimits,
) -> PostTradeReview:
    """`holdings`는 (종목, 주식 수, 장 마감가)다."""
    reviewed: list[ReviewedOrder] = []
    violations: list[str] = []
    for order in orders:
        unfilled = max(order.guarded_quantity - order.filled_quantity, 0)
        reviewed.append(
            ReviewedOrder(
                order.symbol,
                order.side,
                order.plan_quantity,
                order.guarded_quantity,
                order.filled_quantity,
                max(order.plan_quantity - order.guarded_quantity, 0),
                unfilled * BP // order.guarded_quantity if order.guarded_quantity else None,
                order.shortfall_bp,
            )
        )
        if order.side != "buy":
            continue
        if order.filled_quantity > order.guarded_quantity:
            violations.append(
                f"리스크가 허용한 수량보다 더 샀다 — symbol={order.symbol} "
                f"filled={order.filled_quantity} allowed={order.guarded_quantity}"
            )
        if order.symbol in blocked_buys and order.filled_quantity > 0:
            violations.append(
                f"주문 직전 판정이 막은 종목의 매수가 체결됐다 — symbol={order.symbol} "
                f"filled={order.filled_quantity}"
            )
    notes = tuple(
        f"장 마감가로 단일 종목 비중 상한을 넘었다 — symbol={symbol} "
        f"weight_bp={shares * close * BP // equity_krw} cap_bp={limits.max_weight_bp}"
        for symbol, shares, close in holdings
        if equity_krw > 0 and shares * close * BP > equity_krw * limits.max_weight_bp
    )
    return PostTradeReview(tuple(reviewed), tuple(violations), notes)
