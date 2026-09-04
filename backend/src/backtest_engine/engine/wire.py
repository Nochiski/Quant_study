"""Versioned primitive wire contract for persistent Rust decision routing."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, TypeAlias

from backtest_engine.engine.compact import (
    CompactOrder,
    CompactOrderUpdate,
    CompactRoutingResult,
)
from backtest_engine.engine.core import instrument_key
from backtest_engine.engine.orders import BasketGroup
from backtest_engine.engine.router import RoutingResult
from backtest_engine.errors import (
    InstrumentNotInSnapshot,
    SchemaVersionMismatch,
    UndeclaredActionReturned,
    UndeclaredFeatureUsed,
    UnknownOrderId,
    UnsupportedActionValue,
)
from backtest_engine.types.actions import (
    AdjustPosition,
    BasketAction,
    CancelOrder,
    GroupPolicy,
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
    SubmitOrder,
    WeightTarget,
    kind_of,
)
from backtest_engine.types.decision import StrategyDecision
from backtest_engine.types.events import OrderEvent, OrderStatus, OrderUpdateEvent
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

ExecutionWire: TypeAlias = tuple[str, str, str, float | None]
TargetWire: TypeAlias = tuple[
    str,
    str,
    str,
    str,
    str | None,
    float | None,
    str | None,
]
RequestWire: TypeAlias = tuple[
    str,
    TargetWire,
    str,
    str,
    str,
    str | None,
    str | None,
]
BasketLegWire: TypeAlias = tuple[
    str,
    TargetWire | None,
    ExecutionWire | None,
    RequestWire | None,
    str | None,
]
ActionWire: TypeAlias = tuple[
    str,
    list[TargetWire],
    str | None,
    ExecutionWire | None,
    RequestWire | None,
    list[BasketLegWire],
]
DecisionWire: TypeAlias = tuple[int, str, str | None, list[ActionWire]]


@dataclass(frozen=True)
class PersistentRouteEnvelope:
    decision_id: str
    routing: RoutingResult | CompactRoutingResult
    error: Exception | None


def supports_basic_decision(decision: StrategyDecision) -> bool:
    """M2 first slice: common target actions route entirely inside Rust."""
    return all(
        isinstance(
            action,
            (
                NoAction,
                SetPortfolioTarget,
                SetPositionTarget,
                AdjustPosition,
                LiquidatePosition,
                SubmitOrder,
                CancelOrder,
                ReplaceOrder,
                BasketAction,
            ),
        )
        for action in decision.actions
    )


def _execution_wire(
    action: SetPortfolioTarget | SetPositionTarget | AdjustPosition | LiquidatePosition,
) -> ExecutionWire:
    execution = action.execution
    return (
        execution.style.value,
        execution.timing.value,
        execution.time_in_force.value,
        execution.max_participation,
    )


def _target_wire(target: PositionTarget) -> TargetWire:
    key = instrument_key(target.instrument)
    common = (key, target.instrument.symbol, target.instrument.currency)
    if isinstance(target, WeightTarget):
        return ("weight", *common, None, target.weight, None)
    if isinstance(target, QuantityTarget):
        return ("quantity", *common, None, None, str(target.quantity))
    if isinstance(target, NotionalTarget):
        return (
            "notional",
            *common,
            target.notional.currency,
            None,
            str(target.notional.amount),
        )
    raise TypeError(f"unsupported position target wire — got {type(target).__name__}")


def _instrument_wire(
    kind: str,
    action: AdjustPosition | LiquidatePosition,
    *,
    money_currency: str | None = None,
    value: str | None = None,
) -> TargetWire:
    instrument = action.instrument
    return (
        kind,
        instrument_key(instrument),
        instrument.symbol,
        instrument.currency,
        money_currency,
        None,
        value,
    )


def _request_wire(request: OrderRequest) -> RequestWire:
    core = request.core
    instrument = (
        "instrument",
        instrument_key(core.instrument),
        core.instrument.symbol,
        core.instrument.currency,
        None,
        None,
        None,
    )
    if isinstance(request, MarketOrderRequest):
        order_type, limit_price, stop_price = "market", None, None
    elif isinstance(request, LimitOrderRequest):
        order_type, limit_price, stop_price = "limit", str(request.limit_price), None
    elif isinstance(request, StopOrderRequest):
        order_type, limit_price, stop_price = "stop", None, str(request.stop_price)
    elif isinstance(request, StopLimitOrderRequest):
        order_type = "stop_limit"
        limit_price, stop_price = str(request.limit_price), str(request.stop_price)
    else:
        raise TypeError(f"unsupported order request wire — got {type(request).__name__}")
    return (
        order_type,
        instrument,
        core.side.value,
        str(core.quantity),
        core.time_in_force.value,
        limit_price,
        stop_price,
    )


def _basket_leg_wire(
    leg: SetPositionTarget | AdjustPosition | LiquidatePosition | SubmitOrder,
) -> BasketLegWire:
    kind = kind_of(leg).value
    if isinstance(leg, SetPositionTarget):
        return kind, _target_wire(leg.target), _execution_wire(leg), None, None
    if isinstance(leg, AdjustPosition):
        if isinstance(leg.delta, QuantityDelta):
            target = _instrument_wire("quantity_delta", leg, value=str(leg.delta.quantity))
        else:
            target = _instrument_wire(
                "notional_delta",
                leg,
                money_currency=leg.delta.notional.currency,
                value=str(leg.delta.notional.amount),
            )
        return kind, target, _execution_wire(leg), None, None
    if isinstance(leg, LiquidatePosition):
        modifier = f"{str(leg.cancel_open_orders).lower()}|{leg.persistence.value}"
        return kind, _instrument_wire("liquidate", leg), _execution_wire(leg), None, modifier
    if isinstance(leg, SubmitOrder):
        return kind, None, None, _request_wire(leg.request), None
    raise TypeError(f"unsupported basket leg wire — got {type(leg).__name__}")


def decision_to_wire(decision: StrategyDecision) -> DecisionWire:
    actions: list[ActionWire] = []
    for action in decision.actions:
        kind = kind_of(action).value
        if isinstance(action, NoAction):
            actions.append((kind, [], None, None, None, []))
        elif isinstance(action, SetPortfolioTarget):
            actions.append(
                (
                    kind,
                    [_target_wire(target) for target in action.targets],
                    action.scope.value,
                    _execution_wire(action),
                    None,
                    [],
                )
            )
        elif isinstance(action, SetPositionTarget):
            actions.append(
                (kind, [_target_wire(action.target)], None, _execution_wire(action), None, [])
            )
        elif isinstance(action, AdjustPosition):
            if isinstance(action.delta, QuantityDelta):
                target = _instrument_wire(
                    "quantity_delta", action, value=str(action.delta.quantity)
                )
            elif isinstance(action.delta, NotionalDelta):
                target = _instrument_wire(
                    "notional_delta",
                    action,
                    money_currency=action.delta.notional.currency,
                    value=str(action.delta.notional.amount),
                )
            else:
                raise TypeError(f"unsupported delta wire — got {type(action.delta).__name__}")
            actions.append((kind, [target], None, _execution_wire(action), None, []))
        elif isinstance(action, LiquidatePosition):
            target = _instrument_wire("liquidate", action)
            modifier = f"{str(action.cancel_open_orders).lower()}|{action.persistence.value}"
            actions.append((kind, [target], modifier, _execution_wire(action), None, []))
        elif isinstance(action, SubmitOrder):
            actions.append((kind, [], None, None, _request_wire(action.request), []))
        elif isinstance(action, CancelOrder):
            actions.append((kind, [], action.order_id, None, None, []))
        elif isinstance(action, ReplaceOrder):
            actions.append((kind, [], action.order_id, None, _request_wire(action.replacement), []))
        elif isinstance(action, BasketAction):
            actions.append(
                (
                    kind,
                    [],
                    action.group_policy.value,
                    None,
                    None,
                    [_basket_leg_wire(leg) for leg in action.legs],
                )
            )
        else:
            raise TypeError(f"unsupported basic action wire — got {type(action).__name__}")
    return decision.schema_version, str(decision.as_of), decision.reason, actions


def _route_error(error: tuple[str, str] | None) -> Exception | None:
    if error is None:
        return None
    code, message = error
    error_types: dict[str, type[Exception]] = {
        "schema_version": SchemaVersionMismatch,
        "undeclared_action": UndeclaredActionReturned,
        "undeclared_feature": UndeclaredFeatureUsed,
        "unsupported_action_value": UnsupportedActionValue,
        "instrument_not_snapshot": InstrumentNotInSnapshot,
        "unknown_order_id": UnknownOrderId,
        "value_error": ValueError,
    }
    error_type = error_types.get(code, RuntimeError)
    return error_type(message)


def route_basic_decision(
    runtime: Any,
    decision: StrategyDecision,
    portfolio: PortfolioSnapshot,
    market: MarketSnapshot,
) -> PersistentRouteEnvelope:
    decision_id, order_wires, update_wires, group_wires, error_wire = runtime.route_basic_decision(
        decision_to_wire(decision)
    )
    return _route_response_to_envelope(
        decision_id,
        order_wires,
        update_wires,
        group_wires,
        error_wire,
        decision,
        portfolio,
        market,
    )


def _route_response_to_envelope(
    decision_id: str,
    order_wires: list[tuple[Any, ...]],
    update_wires: list[tuple[str, str, str]],
    group_wires: list[tuple[str, str, list[str]]],
    error_wire: tuple[str, str] | None,
    decision: StrategyDecision,
    portfolio: PortfolioSnapshot,
    market: MarketSnapshot,
) -> PersistentRouteEnvelope:
    if order_wires:
        instruments = {instrument_key(bar.instrument): bar.instrument for bar in market.bars}
        instruments.update(
            {
                instrument_key(position.instrument): position.instrument
                for position in portfolio.positions
            }
        )
        built_orders: list[OrderEvent] = []
        for (
            order_id,
            key,
            quantity,
            side,
            order_type,
            limit_price,
            stop_price,
            time_in_force,
            group_id,
            action_index,
            leg_index,
        ) in order_wires:
            action = decision.actions[action_index]
            source_action = (
                action.legs[leg_index]
                if isinstance(action, BasketAction) and leg_index is not None
                else action
            )
            built_orders.append(
                OrderEvent(
                    order_id=order_id,
                    decision_id=decision_id,
                    ts=market.ts,
                    instrument=instruments[key],
                    quantity=Decimal(quantity),
                    side=Side(side),
                    source_action=source_action,
                    order_type=OrderType(order_type),
                    limit_price=None if limit_price is None else Decimal(limit_price),
                    stop_price=None if stop_price is None else Decimal(stop_price),
                    time_in_force=TimeInForce(time_in_force),
                    group_id=group_id,
                )
            )
        orders = tuple(built_orders)
    else:
        orders = ()
    updates = tuple(
        OrderUpdateEvent(
            ts=market.ts,
            order_id=order_id,
            status=OrderStatus(status),
            detail=detail,
        )
        for order_id, status, detail in update_wires
    )
    groups = tuple(
        BasketGroup(
            group_id=group_id,
            policy=GroupPolicy(policy),
            order_ids=tuple(order_ids),
        )
        for group_id, policy, order_ids in group_wires
    )
    return PersistentRouteEnvelope(
        decision_id=decision_id,
        routing=RoutingResult(orders=orders, updates=updates, groups=groups),
        error=_route_error(error_wire),
    )


def submit_basic_decision(
    runtime: Any,
    token: int,
    decision: StrategyDecision,
    portfolio: PortfolioSnapshot,
    market: MarketSnapshot,
) -> PersistentRouteEnvelope:
    decision_id, order_wires, update_wires, group_wires, error_wire = runtime.submit_decision(
        token, decision_to_wire(decision)
    )
    instruments = {instrument_key(bar.instrument): bar.instrument for bar in market.bars}
    instruments.update(
        {
            instrument_key(position.instrument): position.instrument
            for position in portfolio.positions
        }
    )
    orders = tuple(
        CompactOrder(
            order_id=order_id,
            decision_id=decision_id,
            ts=market.ts,
            instrument=instruments[key],
            quantity=quantity,
            side=Side(side),
            source_action=(
                decision.actions[action_index].legs[leg_index]
                if isinstance(decision.actions[action_index], BasketAction)
                and leg_index is not None
                else decision.actions[action_index]
            ),
            order_type=OrderType(order_type),
            limit_price=limit_price,
            stop_price=stop_price,
            time_in_force=TimeInForce(time_in_force),
            group_id=group_id,
        )
        for (
            order_id,
            key,
            quantity,
            side,
            order_type,
            limit_price,
            stop_price,
            time_in_force,
            group_id,
            action_index,
            leg_index,
        ) in order_wires
    )
    updates = tuple(
        CompactOrderUpdate(
            ts=market.ts,
            order_id=order_id,
            status=OrderStatus(status),
            detail=detail,
        )
        for order_id, status, detail in update_wires
    )
    groups = tuple(
        BasketGroup(
            group_id=group_id,
            policy=GroupPolicy(policy),
            order_ids=tuple(order_ids),
        )
        for group_id, policy, order_ids in group_wires
    )
    return PersistentRouteEnvelope(
        decision_id=decision_id,
        routing=CompactRoutingResult(orders=orders, updates=updates, groups=groups),
        error=_route_error(error_wire),
    )
