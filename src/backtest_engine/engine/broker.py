"""BrokerSim: 일봉 OHLC로 주문 종류별 트리거·체결을 결정하는 체결 모델.

체결 규칙 (경로 정보가 없는 일봉에서 결정론적으로 정한다):
- MARKET: 시가.
- LIMIT L: 시가가 이미 L 이내면 시가, 장중 L에 닿으면 L, 아니면 미체결.
- STOP S: 시가가 이미 S를 지났으면 시가, 장중 S에 닿으면 S, 아니면 미발동.
- STOP_LIMIT: STOP 규칙으로 발동한 가격 p가 L 이내면 p에 체결, 아니면 발동만
  기록하고 이후 세션부터 LIMIT으로 평가한다.
낙관적 가정(장중 최유리가 체결)은 쓰지 않는다.

수량 규칙:
- 유동성 캡 = floor(bar.volume × participation). participation은 주문을 만든
  Action의 ExecutionPolicy.max_participation이 있으면 그 값, 없으면 브로커 기본값
  (None = 무제한).
- 매수는 매수 여력(수수료 포함)에도 걸린다. 여력은 호출 측이 준다: MARGIN 없음 = 현금,
  MARGIN = max_gross_leverage × equity − 총노출.
- IOC는 가능한 만큼, FOK는 전량 아니면 체결 없음. 잔량 처리(취소·이월)는 루프 책임.

가격 규칙:
- 체결가 = 규칙 가격 ± 슬리피지(매수 +, 매도 −). LIMIT/STOP_LIMIT은 지정가를
  넘지 않도록 clip하고 기록 슬리피지도 clip 후 값으로 남긴다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from enum import Enum

from backtest_engine.engine.orders import OpenOrder
from backtest_engine.engine.slippage import NoSlippage
from backtest_engine.ports.execution import SlippageModel
from backtest_engine.types.actions import (
    AdjustPosition,
    LiquidatePosition,
    SetPortfolioTarget,
    SetPositionTarget,
)
from backtest_engine.types.events import FillEvent, OrderEvent
from backtest_engine.types.market import Bar
from backtest_engine.types.orders import OrderType, Side, TimeInForce


class ExecutionStatus(Enum):
    FILLED = "filled"
    CASH_LIMITED = "cash_limited"  # 매수 여력(현금/신용 한도)으로 잔량 일부만 체결
    LIQUIDITY_LIMITED = "liquidity_limited"  # 거래량 참여율 캡으로 잔량 일부만 체결
    REJECTED_NO_CASH = "rejected_no_cash"  # 1주도 살 수 없어 주문 전체 거절
    NOT_FILLED = "not_filled"  # 조건 미충족 또는 유동성 0, 주문은 대기 유지
    TRIGGERED_UNFILLED = "triggered_unfilled"  # STOP_LIMIT 발동했지만 지정가 미충족
    FOK_REJECTED = "fok_rejected"  # FOK 전량 체결 불가


@dataclass(frozen=True)
class ExecutionOutcome:
    fill: FillEvent | None
    status: ExecutionStatus
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.fill is not None


@dataclass(frozen=True)
class Quote:
    """체결 전 견적: 이 세션에 이 가격으로 이만큼 체결된다 (quantity 0 = 체결 없음)."""

    order_id: str
    price: float
    quantity: Decimal
    slippage_per_share: float
    status: ExecutionStatus
    detail: str | None


@dataclass(frozen=True)
class PriceDecision:
    price: float | None  # None이면 이 세션에 체결 없음
    triggered: bool  # STOP/STOP_LIMIT이 이 세션에 발동했는가


def execution_price(order: OrderEvent, bar: Bar, already_triggered: bool) -> PriceDecision:
    """주문 종류·방향과 bar OHLC로 체결 가격을 정한다. 순수 함수."""
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


def participation_of(order: OrderEvent) -> float | None:
    """주문을 만든 Action이 지정한 거래량 참여율. 없으면 None."""
    match order.source_action:
        case (
            SetPortfolioTarget(execution=execution)
            | SetPositionTarget(execution=execution)
            | AdjustPosition(execution=execution)
            | LiquidatePosition(execution=execution)
        ):
            return execution.max_participation
        case _:
            return None


class BrokerSim:
    def __init__(
        self,
        fee_bps: float,
        slippage: SlippageModel | None = None,
        max_participation: float | None = None,
    ) -> None:
        if max_participation is not None and not 0.0 < max_participation <= 1.0:
            raise ValueError(
                f"max_participation must be in (0, 1] — max_participation={max_participation}"
            )
        self._fee_rate = fee_bps / 10_000.0
        self._slippage: SlippageModel = slippage if slippage is not None else NoSlippage()
        self._max_participation = max_participation

    def fee_for(self, notional: float) -> float:
        return notional * self._fee_rate

    def execute(
        self,
        open_order: OpenOrder,
        bar: Bar,
        buying_power: float,
        fill_id: str,
    ) -> ExecutionOutcome:
        """대기 주문 하나를 해당 세션 bar로 체결 시도한다 (quote → fill).

        buying_power는 같은 세션에서 앞서 처리된 주문까지 반영한 매수 여력이다
        (MARGIN 없음: 현금, MARGIN: max_gross_leverage × equity − 총노출).
        매도 먼저, 매수 나중 규칙은 호출 측 책임.
        """
        quote = self.quote(open_order, bar, buying_power)
        if quote.quantity <= 0:
            return ExecutionOutcome(fill=None, status=quote.status, detail=quote.detail)
        fill = self.fill(quote, open_order, bar, fill_id)
        return ExecutionOutcome(fill=fill, status=quote.status, detail=quote.detail)

    def quote(
        self,
        open_order: OpenOrder,
        bar: Bar,
        buying_power: float,
        held: Decimal = Decimal(0),
    ) -> Quote:
        """체결 없이 "이 세션에 얼마에 몇 주 체결되는가"만 정한다. quantity 0이면 체결 없음.

        held는 이 세션에서 앞서 처리된 체결까지 반영한 현재 보유 수량(부호 있음). 매도 중
        보유를 넘어 숏을 여는 부분은 매수와 같이 buying_power 캡을 받는다 (스펙 5 결정 1).
        """
        order = open_order.order
        decision = execution_price(order, bar, open_order.triggered)
        if decision.price is None:
            status = (
                ExecutionStatus.TRIGGERED_UNFILLED
                if decision.triggered
                else ExecutionStatus.NOT_FILLED
            )
            return Quote(order.order_id, 0.0, Decimal(0), 0.0, status, None)
        base_price = decision.price

        quantity = open_order.remaining
        status = ExecutionStatus.FILLED
        detail: str | None = None

        liquidity_cap = self._liquidity_cap(order, bar)
        if liquidity_cap is not None and liquidity_cap < quantity:
            if liquidity_cap <= 0:
                return Quote(
                    order.order_id,
                    base_price,
                    Decimal(0),
                    0.0,
                    ExecutionStatus.NOT_FILLED,
                    (
                        f"no liquidity in session — order_id={order.order_id} "
                        f"instrument={order.instrument.symbol} volume={bar.volume} ts={bar.ts}"
                    ),
                )
            quantity = liquidity_cap
            status = ExecutionStatus.LIQUIDITY_LIMITED
            detail = (
                f"fill capped by volume participation — order_id={order.order_id} "
                f"instrument={order.instrument.symbol} remaining={open_order.remaining} "
                f"cap={liquidity_cap} volume={bar.volume}"
            )

        # 슬리피지는 실제 체결 수량에 의존하므로 유동성 캡 이후, 여력 캡 이전에 정한다.
        price, slip = self._slipped_price(order, bar, base_price, quantity)

        short_entry = Decimal(0)
        if order.side is Side.SELL:
            short_entry = quantity - max(held, Decimal(0))
        if order.side is Side.BUY or short_entry > 0:
            if order.side is Side.SELL:
                # 보유분 매도는 무제한이고 그만큼 노출이 줄어 여력이 늘어난다. 숏 진입분만
                # (청산 후 여력)으로 자른다.
                closing = max(held, Decimal(0))
                freed_power = buying_power + float(closing) * price
                affordable = self._affordable_quantity(freed_power, price) + closing
            else:
                affordable = self._affordable_quantity(buying_power, price)
            if affordable <= 0:
                return Quote(
                    order.order_id,
                    price,
                    Decimal(0),
                    slip,
                    ExecutionStatus.REJECTED_NO_CASH,
                    (
                        f"cannot afford a single share — order_id={order.order_id} "
                        f"instrument={order.instrument.symbol} price={price} "
                        f"buying_power={buying_power}"
                    ),
                )
            if affordable < quantity:
                quantity = affordable
                status = ExecutionStatus.CASH_LIMITED
                detail = (
                    f"buy capped by buying_power — order_id={order.order_id} "
                    f"instrument={order.instrument.symbol} remaining={open_order.remaining} "
                    f"filled={quantity} price={price} buying_power={buying_power}"
                )

        if order.time_in_force is TimeInForce.FOK and quantity < open_order.remaining:
            return Quote(
                order.order_id,
                price,
                Decimal(0),
                slip,
                ExecutionStatus.FOK_REJECTED,
                (
                    f"FOK not fillable in full — order_id={order.order_id} "
                    f"instrument={order.instrument.symbol} remaining={open_order.remaining} "
                    f"fillable={quantity} ({detail})"
                ),
            )
        return Quote(order.order_id, price, quantity, slip, status, detail)

    def fill(
        self,
        quote: Quote,
        open_order: OpenOrder,
        bar: Bar,
        fill_id: str,
        quantity: Decimal | None = None,
    ) -> FillEvent:
        """견적을 체결 기록으로 만든다. quantity로 견적보다 적게(바스켓 비례 축소) 체결 가능."""
        order = open_order.order
        filled = quote.quantity if quantity is None else quantity
        if filled <= 0 or filled > quote.quantity:
            raise ValueError(
                f"fill quantity must be within (0, quoted] — order_id={order.order_id} "
                f"requested={filled} quoted={quote.quantity}"
            )
        notional = float(filled) * quote.price
        return FillEvent(
            fill_id=fill_id,
            order_id=order.order_id,
            ts=bar.ts,
            instrument=order.instrument,
            quantity=filled,
            side=order.side,
            price=quote.price,
            fee=self.fee_for(notional),
            slippage_per_share=quote.slippage_per_share,
        )

    def _liquidity_cap(self, order: OrderEvent, bar: Bar) -> Decimal | None:
        participation = participation_of(order)
        if participation is None:
            participation = self._max_participation
        if participation is None:
            return None
        # float 곱셈(90 × 0.7 = 62.999…)이 floor를 한 주 깎지 않도록 Decimal로 정확히 계산한다.
        cap = Decimal(bar.volume) * Decimal(str(participation))
        return cap.quantize(Decimal(1), rounding=ROUND_FLOOR)

    def _slipped_price(
        self, order: OrderEvent, bar: Bar, base_price: float, quantity: Decimal
    ) -> tuple[float, float]:
        slip = self._slippage.slippage_per_share(order, bar, base_price, quantity)
        if slip < 0:
            model = type(self._slippage).__name__
            raise ValueError(
                f"slippage model returned negative slippage — model={model} "
                f"order_id={order.order_id} slip={slip}"
            )
        price = base_price + slip if order.side is Side.BUY else base_price - slip
        if order.limit_price is not None:
            limit = float(order.limit_price)
            price = min(price, limit) if order.side is Side.BUY else max(price, limit)
        return price, abs(price - base_price)

    def _affordable_quantity(self, buying_power: float, price: float) -> Decimal:
        """수수료까지 포함해 여력으로 살 수 있는 최대 정수 수량."""
        if price <= 0 or buying_power <= 0:
            return Decimal(0)
        raw = buying_power / (price * (1.0 + self._fee_rate))
        return Decimal(raw).quantize(Decimal(1), rounding=ROUND_FLOOR)
