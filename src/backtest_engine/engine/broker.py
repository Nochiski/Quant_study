"""BrokerSim: 일봉 OHLC로 주문 종류별 트리거·체결을 결정하는 체결 모델.

체결 가격 규칙은 `engine/pricing.py`(Python) 또는 Rust 코어가 제공한다 (`pricing` 인자).

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
from typing import Protocol

from backtest_engine.engine.orders import OpenOrder
from backtest_engine.engine.pricing import PriceDecision, execution_price
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
from backtest_engine.types.orders import Side, TimeInForce


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


class ExecutionPricing(Protocol):
    """체결 가격 규칙 제공자 (Python 기본 구현 또는 Rust 코어 어댑터)."""

    def execution_price(
        self, order: OrderEvent, bar: Bar, already_triggered: bool
    ) -> PriceDecision: ...


class _DefaultPricing:
    def execution_price(
        self, order: OrderEvent, bar: Bar, already_triggered: bool
    ) -> PriceDecision:
        return execution_price(order, bar, already_triggered)


@dataclass(frozen=True)
class QuoteNumbers:
    """견적의 수치 결과 (진단 문자열은 BrokerSim이 붙인다)."""

    price: float
    quantity: Decimal
    slippage_per_share: float
    status: ExecutionStatus


class QuoteCore(Protocol):
    """유동성 캡 → 슬리피지·지정가 clip → 여력 캡 → FOK 판정의 수치 부분 (Python 또는 Rust)."""

    def quote_numbers(
        self,
        side: Side,
        base_price: float,
        remaining: Decimal,
        volume: int,
        participation: float | None,
        slip: float,
        limit_price: Decimal | None,
        buying_power: float,
        held: Decimal,
        fee_rate: float,
        fok: bool,
    ) -> QuoteNumbers: ...


class PythonQuoteCore:
    def quote_numbers(
        self,
        side: Side,
        base_price: float,
        remaining: Decimal,
        volume: int,
        participation: float | None,
        slip: float,
        limit_price: Decimal | None,
        buying_power: float,
        held: Decimal,
        fee_rate: float,
        fok: bool,
    ) -> QuoteNumbers:
        if slip < 0:
            raise ValueError(f"slippage must be >= 0 — slip={slip}")
        quantity = remaining
        status = ExecutionStatus.FILLED
        if participation is not None:
            # float 곱셈(90 × 0.7 = 62.999…)이 floor를 한 주 깎지 않도록 Decimal로 정확히 계산한다.
            cap = (Decimal(volume) * Decimal(str(participation))).quantize(
                Decimal(1), rounding=ROUND_FLOOR
            )
            if cap < quantity:
                if cap <= 0:
                    return QuoteNumbers(base_price, Decimal(0), 0.0, ExecutionStatus.NOT_FILLED)
                quantity = cap
                status = ExecutionStatus.LIQUIDITY_LIMITED

        buy = side is Side.BUY
        price = base_price + slip if buy else base_price - slip
        if limit_price is not None:
            limit = float(limit_price)
            price = min(price, limit) if buy else max(price, limit)
        applied = abs(price - base_price)

        short_entry = Decimal(0) if buy else quantity - max(held, Decimal(0))
        if buy or short_entry > 0:
            if buy:
                affordable = _affordable_quantity(buying_power, price, fee_rate)
            else:
                # 보유분 매도는 무제한이고 그만큼 노출이 줄어 여력이 늘어난다. 숏 진입분만
                # (청산 후 여력)으로 자른다.
                closing = max(held, Decimal(0))
                freed_power = buying_power + float(closing) * price
                affordable = _affordable_quantity(freed_power, price, fee_rate) + closing
            if affordable <= 0:
                return QuoteNumbers(price, Decimal(0), applied, ExecutionStatus.REJECTED_NO_CASH)
            if affordable < quantity:
                quantity = affordable
                status = ExecutionStatus.CASH_LIMITED

        if fok and quantity < remaining:
            return QuoteNumbers(price, Decimal(0), applied, ExecutionStatus.FOK_REJECTED)
        return QuoteNumbers(price, quantity, applied, status)


def _affordable_quantity(buying_power: float, price: float, fee_rate: float) -> Decimal:
    """수수료까지 포함해 여력으로 살 수 있는 최대 정수 수량."""
    if price <= 0 or buying_power <= 0:
        return Decimal(0)
    raw = buying_power / (price * (1.0 + fee_rate))
    return Decimal(raw).quantize(Decimal(1), rounding=ROUND_FLOOR)


class BrokerSim:
    def __init__(
        self,
        fee_bps: float,
        slippage: SlippageModel | None = None,
        max_participation: float | None = None,
        pricing: ExecutionPricing | None = None,
        quote_core: QuoteCore | None = None,
    ) -> None:
        if max_participation is not None and not 0.0 < max_participation <= 1.0:
            raise ValueError(
                f"max_participation must be in (0, 1] — max_participation={max_participation}"
            )
        self._fee_rate = fee_bps / 10_000.0
        self._slippage: SlippageModel = slippage if slippage is not None else NoSlippage()
        self._max_participation = max_participation
        self._pricing: ExecutionPricing = pricing if pricing is not None else _DefaultPricing()
        self._quote_core: QuoteCore = quote_core if quote_core is not None else PythonQuoteCore()

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
        decision = self._pricing.execution_price(order, bar, open_order.triggered)
        if decision.price is None:
            status = (
                ExecutionStatus.TRIGGERED_UNFILLED
                if decision.triggered
                else ExecutionStatus.NOT_FILLED
            )
            return Quote(order.order_id, 0.0, Decimal(0), 0.0, status, None)
        base_price = decision.price

        participation = participation_of(order)
        if participation is None:
            participation = self._max_participation
        # 슬리피지는 유동성 캡 이후 수량에 의존하지만 모델 인터페이스가 수량을 받으므로 캡을
        # 먼저 한 번 계산해 넘긴다 (Python·Rust 코어 모두 같은 값을 다시 계산한다).
        capped = open_order.remaining
        if participation is not None:
            cap = (Decimal(bar.volume) * Decimal(str(participation))).quantize(
                Decimal(1), rounding=ROUND_FLOOR
            )
            capped = min(capped, max(cap, Decimal(0)))
        slip = self._slippage.slippage_per_share(order, bar, base_price, capped)
        if slip < 0:
            model = type(self._slippage).__name__
            raise ValueError(
                f"slippage model returned negative slippage — model={model} "
                f"order_id={order.order_id} slip={slip}"
            )
        numbers = self._quote_core.quote_numbers(
            order.side,
            base_price,
            open_order.remaining,
            bar.volume,
            participation,
            slip,
            order.limit_price,
            buying_power,
            held,
            self._fee_rate,
            order.time_in_force is TimeInForce.FOK,
        )
        detail = self._detail(numbers, order, bar, open_order.remaining, buying_power)
        return Quote(
            order.order_id,
            numbers.price,
            numbers.quantity,
            numbers.slippage_per_share,
            numbers.status,
            detail,
        )

    @staticmethod
    def _detail(
        numbers: QuoteNumbers, order: OrderEvent, bar: Bar, remaining: Decimal, buying_power: float
    ) -> str | None:
        symbol = order.instrument.symbol
        match numbers.status:
            case ExecutionStatus.NOT_FILLED:
                return (
                    f"no liquidity in session — order_id={order.order_id} instrument={symbol} "
                    f"volume={bar.volume} ts={bar.ts}"
                )
            case ExecutionStatus.LIQUIDITY_LIMITED:
                return (
                    f"fill capped by volume participation — order_id={order.order_id} "
                    f"instrument={symbol} remaining={remaining} cap={numbers.quantity} "
                    f"volume={bar.volume}"
                )
            case ExecutionStatus.REJECTED_NO_CASH:
                return (
                    f"cannot afford a single share — order_id={order.order_id} "
                    f"instrument={symbol} price={numbers.price} buying_power={buying_power}"
                )
            case ExecutionStatus.CASH_LIMITED:
                return (
                    f"buy capped by buying_power — order_id={order.order_id} instrument={symbol} "
                    f"remaining={remaining} filled={numbers.quantity} price={numbers.price} "
                    f"buying_power={buying_power}"
                )
            case ExecutionStatus.FOK_REJECTED:
                return (
                    f"FOK not fillable in full — order_id={order.order_id} instrument={symbol} "
                    f"remaining={remaining} price={numbers.price} buying_power={buying_power}"
                )
            case _:
                return None

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
