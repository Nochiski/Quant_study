"""일봉 OHLC 기반 체결 가격 규칙 (순수 함수). Python 코어 구현이며 Rust 코어의 진실 원천.

- MARKET: 시가.
- LIMIT L: 시가가 이미 L 이내면 시가, 장중 L에 닿으면 L, 아니면 미체결.
- STOP S: 시가가 이미 S를 지났으면 시가, 장중 S에 닿으면 S, 아니면 미발동.
- STOP_LIMIT: STOP 규칙으로 발동한 가격 p가 L 이내면 p에 체결, 아니면 발동만 기록하고
  이후 세션부터 LIMIT으로 평가한다.
낙관적 가정(장중 최유리가 체결)은 쓰지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from backtest_engine.types.events import OrderEvent
from backtest_engine.types.market import Bar
from backtest_engine.types.orders import OrderType, Side


@dataclass(frozen=True)
class PriceDecision:
    price: float | None  # None이면 이 세션에 체결 없음
    triggered: bool  # STOP/STOP_LIMIT이 이 세션에 발동했는가


def execution_price(order: OrderEvent, bar: Bar, already_triggered: bool) -> PriceDecision:
    """주문 종류·방향과 bar OHLC로 체결 가격을 정한다."""
    buy = order.side is Side.BUY
    match order.order_type:
        case OrderType.MARKET:
            return PriceDecision(bar.open, False)
        case OrderType.LIMIT:
            return PriceDecision(_limit_price(bar, float(_require(order.limit_price)), buy), False)
        case OrderType.STOP:
            if already_triggered:  # 발동 후 잔량은 시장가로 취급한다 (부분체결 이월)
                return PriceDecision(bar.open, False)
            return PriceDecision(_stop_price(bar, float(_require(order.stop_price)), buy), False)
        case OrderType.STOP_LIMIT:
            limit = float(_require(order.limit_price))
            if already_triggered:
                return PriceDecision(_limit_price(bar, limit, buy), False)
            trigger = _stop_price(bar, float(_require(order.stop_price)), buy)
            if trigger is None:
                return PriceDecision(None, False)
            within_limit = trigger <= limit if buy else trigger >= limit
            return PriceDecision(trigger if within_limit else None, True)


def _limit_price(bar: Bar, limit: float, buy: bool) -> float | None:
    if buy:
        if bar.open <= limit:
            return bar.open
        return limit if bar.low <= limit else None
    if bar.open >= limit:
        return bar.open
    return limit if bar.high >= limit else None


def _stop_price(bar: Bar, stop: float, buy: bool) -> float | None:
    if buy:
        if bar.open >= stop:
            return bar.open
        return stop if bar.high >= stop else None
    if bar.open <= stop:
        return bar.open
    return stop if bar.low <= stop else None


def _require(price: Decimal | None) -> Decimal:
    if price is None:  # OrderEvent.__post_init__가 막으므로 방어용
        raise ValueError("order type requires a price that is missing")
    return price
