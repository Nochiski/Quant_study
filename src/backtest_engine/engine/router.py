"""DecisionRouter: 전략 판단을 검증하고 실행 가능한 주문으로 변환한다.

전략은 원하는 상태만 말한다. 주문 수량 계산, 주문 가능 여부,
체결 가격·수수료 반영은 엔진 책임이다.

구현 범위 (Capability와 일치):
- NoAction, SetPortfolioTarget, SetPositionTarget, AdjustPosition, LiquidatePosition,
  SubmitOrder(MARKET/LIMIT/STOP/STOP_LIMIT, DAY/GTC/IOC/FOK), CancelOrder, ReplaceOrder.
- LIMIT/STOP 종류, IOC/FOK, max_participation은 해당 EngineFeature를 선언한 전략만
  쓸 수 있다 (UndeclaredFeatureUsed).
- 취소·정정은 대기열을 즉시 바꾸고 결과를 OrderUpdateEvent로 돌려준다. 모르는
  order_id는 전략 버그이므로 UnknownOrderId로 run을 중단한다.
- ExecutionPolicy는 MARKET 스타일만, 일봉 엔진이므로 NEXT_OPEN과
  NEXT_AVAILABLE 모두 "다음 세션 시가"를 뜻한다.

수량 규칙:
- 참조 가격은 판단 세션 종가. 금액→수량은 floor_delta_shares 한 곳에서 한다.
- WeightTarget 매도는 비중 반올림 오차 때문에 보유 수량까지 clamp한다.
- Quantity/Notional 타깃과 Delta는 전략이 명시한 수량이므로 clamp하지 않는다.
  결과 포지션이 0 미만이면 SHORT_SELLING 미구현으로 거절한다.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from backtest_engine.engine.orders import OrderManager
from backtest_engine.errors import (
    CapabilityNotImplemented,
    SchemaVersionMismatch,
    UndeclaredActionReturned,
    UndeclaredFeatureUsed,
    UnknownOrderId,
    UnsupportedActionValue,
)
from backtest_engine.sizing import floor_delta_shares
from backtest_engine.types.actions import (
    ActionKind,
    AdjustPosition,
    CancelOrder,
    ExecutionPolicy,
    ExecutionStyle,
    LiquidatePosition,
    NoAction,
    NotionalDelta,
    NotionalTarget,
    PositionTarget,
    QuantityDelta,
    QuantityTarget,
    ReplaceOrder,
    SetPortfolioTarget,
    SetPositionTarget,
    StrategyAction,
    SubmitOrder,
    TargetScope,
    WeightTarget,
    kind_of,
)
from backtest_engine.types.decision import SCHEMA_VERSION, StrategyDecision
from backtest_engine.types.events import OrderEvent, OrderStatus, OrderUpdateEvent
from backtest_engine.types.instruments import InstrumentId, Money
from backtest_engine.types.market import MarketSnapshot
from backtest_engine.types.orders import (
    LimitOrderRequest,
    MarketOrderRequest,
    OrderRequest,
    OrderType,
    Side,
    StopLimitOrderRequest,
    StopOrderRequest,
    TimeInForce,
)
from backtest_engine.types.portfolio import PortfolioSnapshot
from backtest_engine.types.requirements import EngineFeature


@dataclass(frozen=True)
class RoutingResult:
    orders: tuple[OrderEvent, ...]
    updates: tuple[OrderUpdateEvent, ...]  # 취소·정정으로 즉시 바뀐 기존 주문 상태


class DecisionRouter:
    def __init__(
        self,
        declared_actions: frozenset[ActionKind],
        order_manager: OrderManager,
        declared_features: frozenset[EngineFeature] = frozenset(),
    ) -> None:
        self._declared_actions = declared_actions
        self._declared_features = declared_features
        self._order_manager = order_manager
        # 같은 Decision 안에서 이미 라우팅된 매도 수량 (종목별). route()마다 초기화.
        self._routed_sells: dict[InstrumentId, Decimal] = defaultdict(Decimal)
        self._short_allowed = EngineFeature.SHORT_SELLING in declared_features

    def route(
        self,
        decision: StrategyDecision,
        decision_id: str,
        portfolio: PortfolioSnapshot,
        market: MarketSnapshot,
    ) -> RoutingResult:
        if decision.schema_version != SCHEMA_VERSION:
            raise SchemaVersionMismatch(
                f"decision schema version mismatch — expected {SCHEMA_VERSION}, "
                f"got {decision.schema_version} as_of={decision.as_of} reason={decision.reason}"
            )

        orders: list[OrderEvent] = []
        updates: list[OrderUpdateEvent] = []
        self._routed_sells = defaultdict(Decimal)
        for action in decision.actions:
            kind = kind_of(action)
            if kind not in self._declared_actions:
                raise UndeclaredActionReturned(
                    f"action kind was not declared in requirements() — kind={kind.value} "
                    f"declared={sorted(k.value for k in self._declared_actions)} "
                    f"decision_id={decision_id} as_of={decision.as_of}"
                )
            orders.extend(self._route_action(action, decision_id, portfolio, market, updates))
        return RoutingResult(orders=tuple(orders), updates=tuple(updates))

    def _route_action(
        self,
        action: StrategyAction,
        decision_id: str,
        portfolio: PortfolioSnapshot,
        market: MarketSnapshot,
        updates: list[OrderUpdateEvent],
    ) -> list[OrderEvent]:
        match action:
            case NoAction():
                return []
            case SetPortfolioTarget():
                self._check_execution(action.execution, decision_id)
                return self._portfolio_target_orders(action, decision_id, portfolio, market)
            case SetPositionTarget():
                self._check_execution(action.execution, decision_id)
                instrument, delta, clamp = self._target_delta(
                    action.target, decision_id, portfolio, market
                )
                return self._delta_orders(
                    instrument, delta, clamp, action, decision_id, portfolio, market
                )
            case AdjustPosition():
                self._check_execution(action.execution, decision_id)
                delta = self._adjust_delta(action, decision_id, market)
                return self._delta_orders(
                    action.instrument, delta, False, action, decision_id, portfolio, market
                )
            case SubmitOrder():
                return self._order_from_request(
                    action.request, action, decision_id, portfolio, market
                )
            case CancelOrder():
                self._take_open_order(action.order_id, decision_id)
                updates.append(
                    OrderUpdateEvent(
                        ts=market.ts,
                        order_id=action.order_id,
                        status=OrderStatus.CANCELLED,
                        detail=f"cancelled by strategy — decision_id={decision_id}",
                    )
                )
                return []
            case ReplaceOrder():
                self._take_open_order(action.order_id, decision_id)
                orders = self._order_from_request(
                    action.replacement, action, decision_id, portfolio, market
                )
                updates.append(
                    OrderUpdateEvent(
                        ts=market.ts,
                        order_id=action.order_id,
                        status=OrderStatus.REPLACED,
                        detail=(
                            f"replaced by strategy — replaced_by={orders[0].order_id} "
                            f"decision_id={decision_id}"
                        ),
                    )
                )
                return orders
            case LiquidatePosition():
                self._check_execution(action.execution, decision_id)
                if action.cancel_open_orders:
                    for stale in self._order_manager.cancel_for_instrument(action.instrument):
                        updates.append(
                            OrderUpdateEvent(
                                ts=market.ts,
                                order_id=stale.order_id,
                                status=OrderStatus.CANCELLED,
                                detail=(
                                    f"cancelled by LiquidatePosition — "
                                    f"instrument={action.instrument.symbol} "
                                    f"decision_id={decision_id}"
                                ),
                            )
                        )
                return self._liquidation_orders(action, decision_id, portfolio, market)
            case _:
                # prepare_strategy의 capability gate가 거절했어야 하는 경로다.
                raise CapabilityNotImplemented(
                    f"action reached router without an implemented handler — "
                    f"kind={kind_of(action).value} decision_id={decision_id}"
                )

    def _check_execution(self, execution: ExecutionPolicy, decision_id: str) -> None:
        if execution.style is not ExecutionStyle.MARKET:
            raise UnsupportedActionValue(
                f"execution style not implemented in v1 — style={execution.style.value} "
                f"decision_id={decision_id}, only MARKET is supported"
            )
        if execution.max_participation is not None:
            self._require_feature(
                EngineFeature.PARTIAL_FILL,
                f"max_participation={execution.max_participation}",
                decision_id,
            )
            if not 0.0 < execution.max_participation <= 1.0:
                raise UnsupportedActionValue(
                    f"max_participation must be in (0, 1] — "
                    f"max_participation={execution.max_participation} decision_id={decision_id}"
                )

    def _require_feature(self, feature: EngineFeature, what: str, decision_id: str) -> None:
        if feature not in self._declared_features:
            raise UndeclaredFeatureUsed(
                f"{what} requires a feature not declared in requirements() — "
                f"feature={feature.value} "
                f"declared={sorted(f.value for f in self._declared_features)} "
                f"decision_id={decision_id}"
            )

    def _portfolio_target_orders(
        self,
        action: SetPortfolioTarget,
        decision_id: str,
        portfolio: PortfolioSnapshot,
        market: MarketSnapshot,
    ) -> list[OrderEvent]:
        deltas: dict[InstrumentId, tuple[Decimal, bool]] = {}
        for target in action.targets:
            instrument, delta, clamp = self._target_delta(target, decision_id, portfolio, market)
            if instrument in deltas:
                raise ValueError(
                    f"duplicate instrument in portfolio target — "
                    f"instrument={instrument.symbol} decision_id={decision_id}"
                )
            deltas[instrument] = (delta, clamp)

        if action.scope is TargetScope.REPLACE:
            # 목록에 없는 기존 포지션은 0으로 본다.
            for position in portfolio.positions:
                deltas.setdefault(position.instrument, (-position.quantity, True))

        orders: list[OrderEvent] = []
        for instrument, (delta, clamp) in deltas.items():
            orders.extend(
                self._delta_orders(instrument, delta, clamp, action, decision_id, portfolio, market)
            )
        return orders

    def _target_delta(
        self,
        target: PositionTarget,
        decision_id: str,
        portfolio: PortfolioSnapshot,
        market: MarketSnapshot,
    ) -> tuple[InstrumentId, Decimal, bool]:
        """목표 → (종목, 부호 있는 주식 수 변화량, 매도 clamp 허용 여부)."""
        instrument = target.instrument
        match target:
            case WeightTarget():
                if target.weight < 0 and not self._short_allowed:
                    raise UnsupportedActionValue(
                        f"negative target weight requires SHORT_SELLING feature "
                        f"(not implemented) — instrument={instrument.symbol} "
                        f"weight={target.weight} decision_id={decision_id}"
                    )
                target_notional = target.weight * portfolio.equity
                delta = self._notional_to_delta(instrument, target_notional, portfolio, market)
                return instrument, delta, True
            case NotionalTarget():
                self._check_currency(instrument, target.notional, decision_id)
                if target.notional.amount < 0 and not self._short_allowed:
                    raise UnsupportedActionValue(
                        f"negative target notional requires SHORT_SELLING feature "
                        f"(not implemented) — instrument={instrument.symbol} "
                        f"notional={target.notional.amount} decision_id={decision_id}"
                    )
                target_notional = float(target.notional.amount)
                delta = self._notional_to_delta(instrument, target_notional, portfolio, market)
                return instrument, delta, False
            case QuantityTarget():
                self._check_integer(target.quantity, instrument, decision_id)
                if target.quantity < 0 and not self._short_allowed:
                    raise UnsupportedActionValue(
                        f"negative target quantity requires SHORT_SELLING feature "
                        f"(not implemented) — instrument={instrument.symbol} "
                        f"quantity={target.quantity} decision_id={decision_id}"
                    )
                return instrument, target.quantity - portfolio.position_qty(instrument), False

    def _adjust_delta(
        self, action: AdjustPosition, decision_id: str, market: MarketSnapshot
    ) -> Decimal:
        match action.delta:
            case QuantityDelta():
                self._check_integer(action.delta.quantity, action.instrument, decision_id)
                return action.delta.quantity
            case NotionalDelta():
                self._check_currency(action.instrument, action.delta.notional, decision_id)
                notional = float(action.delta.notional.amount)
                shares = floor_delta_shares(notional, market.bar(action.instrument).close)
                return shares if notional >= 0 else -shares

    def _notional_to_delta(
        self,
        instrument: InstrumentId,
        target_notional: float,
        portfolio: PortfolioSnapshot,
        market: MarketSnapshot,
    ) -> Decimal:
        current_position = portfolio.position(instrument)
        current_notional = current_position.market_value if current_position else 0.0
        delta_notional = target_notional - current_notional
        shares = floor_delta_shares(delta_notional, market.bar(instrument).close)
        return shares if delta_notional >= 0 else -shares

    def _delta_orders(
        self,
        instrument: InstrumentId,
        delta: Decimal,
        clamp_sell: bool,
        action: StrategyAction,
        decision_id: str,
        portfolio: PortfolioSnapshot,
        market: MarketSnapshot,
    ) -> list[OrderEvent]:
        if delta == 0:
            return []
        side = Side.BUY if delta > 0 else Side.SELL
        quantity = abs(delta)
        if side is Side.SELL and not self._short_allowed:
            sellable = self._sellable(instrument, portfolio)
            if quantity > sellable:
                if not clamp_sell:
                    self._raise_oversell(instrument, portfolio, quantity, decision_id)
                quantity = sellable
            if quantity <= 0:
                return []
            self._routed_sells[instrument] += quantity
        return [
            OrderEvent(
                order_id=self._order_manager.next_order_id(),
                decision_id=decision_id,
                ts=market.ts,
                instrument=instrument,
                quantity=quantity,
                side=side,
                source_action=action,
            )
        ]

    _FEATURE_FOR_TYPE: dict[OrderType, EngineFeature] = {
        OrderType.LIMIT: EngineFeature.LIMIT_ORDER,
        OrderType.STOP: EngineFeature.STOP_ORDER,
        OrderType.STOP_LIMIT: EngineFeature.STOP_ORDER,
    }

    def _take_open_order(self, order_id: str, decision_id: str) -> None:
        if self._order_manager.get(order_id) is None:
            open_ids = [o.order_id for o in self._order_manager.open_orders()]
            raise UnknownOrderId(
                f"order is not open (unknown, filled, cancelled or expired) — "
                f"order_id={order_id} open={open_ids} decision_id={decision_id}"
            )
        self._order_manager.remove(order_id)

    def _order_from_request(
        self,
        request: OrderRequest,
        action: SubmitOrder | ReplaceOrder,
        decision_id: str,
        portfolio: PortfolioSnapshot,
        market: MarketSnapshot,
    ) -> list[OrderEvent]:
        core = request.core
        order_type, limit_price, stop_price = self._describe_request(request)
        feature = self._FEATURE_FOR_TYPE.get(order_type)
        if feature is not None:
            self._require_feature(feature, f"order_type={order_type.value}", decision_id)
        if core.time_in_force in (TimeInForce.IOC, TimeInForce.FOK):
            self._require_feature(
                EngineFeature.PARTIAL_FILL,
                f"time_in_force={core.time_in_force.value}",
                decision_id,
            )
        self._check_integer(core.quantity, core.instrument, decision_id)
        if core.side is Side.SELL and not self._short_allowed:
            if core.quantity > self._sellable(core.instrument, portfolio):
                self._raise_oversell(core.instrument, portfolio, core.quantity, decision_id)
            self._routed_sells[core.instrument] += core.quantity
        return [
            OrderEvent(
                order_id=self._order_manager.next_order_id(),
                decision_id=decision_id,
                ts=market.ts,
                instrument=core.instrument,
                quantity=core.quantity,
                side=core.side,
                source_action=action,
                order_type=order_type,
                limit_price=limit_price,
                stop_price=stop_price,
                time_in_force=core.time_in_force,
            )
        ]

    @staticmethod
    def _describe_request(
        request: OrderRequest,
    ) -> tuple[OrderType, Decimal | None, Decimal | None]:
        match request:
            case MarketOrderRequest():
                return OrderType.MARKET, None, None
            case LimitOrderRequest():
                return OrderType.LIMIT, request.limit_price, None
            case StopOrderRequest():
                return OrderType.STOP, None, request.stop_price
            case StopLimitOrderRequest():
                return OrderType.STOP_LIMIT, request.limit_price, request.stop_price

    def _open_sell_quantity(self, instrument: InstrumentId) -> Decimal:
        return sum(
            (
                entry.remaining
                for entry in self._order_manager.open_entries()
                if entry.order.instrument == instrument and entry.order.side is Side.SELL
            ),
            Decimal(0),
        )

    def _sellable(self, instrument: InstrumentId, portfolio: PortfolioSnapshot) -> Decimal:
        """더 팔 수 있는 수량 = 보유 − 대기 매도 잔량 − 이 Decision에서 이미 라우팅된 매도."""
        return (
            portfolio.position_qty(instrument)
            - self._open_sell_quantity(instrument)
            - self._routed_sells[instrument]
        )

    def _raise_oversell(
        self,
        instrument: InstrumentId,
        portfolio: PortfolioSnapshot,
        quantity: Decimal,
        decision_id: str,
    ) -> None:
        raise UnsupportedActionValue(
            f"resulting position would be negative — requires SHORT_SELLING feature "
            f"(not implemented) — instrument={instrument.symbol} "
            f"held={portfolio.position_qty(instrument)} sell={quantity} "
            f"already_routed={self._routed_sells[instrument]} "
            f"open_sell={self._open_sell_quantity(instrument)} decision_id={decision_id}"
        )

    @staticmethod
    def _check_integer(quantity: Decimal, instrument: InstrumentId, decision_id: str) -> None:
        if quantity != quantity.to_integral_value():
            raise UnsupportedActionValue(
                f"fractional shares not supported — quantity must be an integer, "
                f"got {quantity} instrument={instrument.symbol} decision_id={decision_id}"
            )

    @staticmethod
    def _check_currency(instrument: InstrumentId, money: Money, decision_id: str) -> None:
        if money.currency != instrument.currency:
            raise UnsupportedActionValue(
                f"notional currency does not match instrument currency — "
                f"notional={money.currency} instrument={instrument.symbol}/"
                f"{instrument.currency} decision_id={decision_id}"
            )

    def _liquidation_orders(
        self,
        action: LiquidatePosition,
        decision_id: str,
        portfolio: PortfolioSnapshot,
        market: MarketSnapshot,
    ) -> list[OrderEvent]:
        held = portfolio.position_qty(action.instrument)
        if held > 0:
            held = self._sellable(action.instrument, portfolio)
            if held <= 0:
                return []
            self._routed_sells[action.instrument] += held
        elif held == 0:
            return []
        return [
            OrderEvent(
                order_id=self._order_manager.next_order_id(),
                decision_id=decision_id,
                ts=market.ts,
                instrument=action.instrument,
                quantity=abs(held),
                side=Side.SELL if held > 0 else Side.BUY,  # 숏 포지션은 매수로 청산
                source_action=action,
            )
        ]
