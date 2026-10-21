"""주간 리밸런싱 주문의 가격·수량·체결 품질(S07). 호가와 금액만 다루는 순수 계산이다.

- 지정가는 매수면 매도 호가, 매도면 매수 호가의 `level`번째 값이다(2026-10-21 승인: 2호가).
- 체결 차이는 신호 기준가 대비 bp이고 불리한 쪽이 양수다. 미체결 기회비용은 못 산(판) 수량에
  기준가에서 장 마감가까지의 움직임을 곱한 값이고, 놓친 이익이 양수다.
"""

from __future__ import annotations

from dataclasses import dataclass

from strategy_workbench.domain.rebalance._model import BP

SIDES = frozenset({"buy", "sell"})


@dataclass(frozen=True)
class Quote:
    """호가표. 1호가부터이고 가격이 없는 칸은 뺀다."""

    symbol: str
    asks: tuple[int, ...]
    bids: tuple[int, ...]


@dataclass(frozen=True)
class ExecutionPolicy:
    """비용 상한은 주문 금액의 bp(올림)다. 모의 비용은 매수 0.35%, 매도 0.35% + 거래세 0.2%다."""

    limit_level: int = 2
    buy_fee_budget_bp: int = 40
    sell_fee_budget_bp: int = 70
    # 주문안 하나에 낼 주문(다시 낸 주문 포함) 상한. 10종목 × 오전 추적 16회에 여유를 둔 값이다.
    max_orders_per_day: int = 200

    def __post_init__(self) -> None:
        for name in (
            "limit_level",
            "buy_fee_budget_bp",
            "sell_fee_budget_bp",
            "max_orders_per_day",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"execution policy field must be positive — {name}={value!r}")

    def fee_budget(self, side: str, quantity: int, limit_price_krw: int) -> int:
        _side(side)
        budget_bp = self.buy_fee_budget_bp if side == "buy" else self.sell_fee_budget_bp
        return -(-quantity * limit_price_krw * budget_bp // BP)


def limit_price(side: str, quote: Quote, level: int) -> int | None:
    _side(side)
    book = quote.asks if side == "buy" else quote.bids
    if not book:
        return None
    return book[min(level, len(book)) - 1]


def affordable_shares(cash_krw: int, limit_price_krw: int, fee_rate_bp: int) -> int:
    if cash_krw <= 0 or limit_price_krw <= 0:
        return 0
    return cash_krw * BP // (limit_price_krw * (BP + fee_rate_bp))


def shortfall_bp(side: str, filled_quantity: int, filled_value_krw: int, reference_krw: int) -> int:
    """평균 체결가가 신호 기준가보다 불리한 정도(bp, 내림). 매수는 비쌀수록, 매도는 쌀수록
    양수다."""
    _side(side)
    if filled_quantity <= 0 or reference_krw <= 0:
        raise ValueError(
            f"shortfall needs fills and a reference — filled={filled_quantity} "
            f"reference={reference_krw}"
        )
    gap = filled_value_krw - filled_quantity * reference_krw
    if side == "sell":
        gap = -gap
    return gap * BP // (filled_quantity * reference_krw)


def missed_krw(side: str, unfilled_quantity: int, reference_krw: int, close_krw: int) -> int:
    """못 산(판) 수량의 기회비용(원). 기준가에서 장 마감가로 유리하게 움직였으면 양수다."""
    _side(side)
    move = close_krw - reference_krw
    return unfilled_quantity * (move if side == "buy" else -move)


def _side(side: str) -> None:
    if side not in SIDES:
        raise ValueError(f"side must be buy or sell — side={side!r}")
