"""프로토콜 타입의 JSON 직렬화·역직렬화.

엔진 내부는 dataclass/enum/tuple로 고정하고, YAML/API/JSON 경계에서만
dict로 바꾼다. 합 타입은 "type" 태그로 구분하고, Decimal은 문자열,
datetime은 ISO 8601, Enum은 value로 표현한다.

여기 함수들이 Python/Rust 경계의 직렬화 계약이며,
StrategyDecision.schema_version이 이 계약의 버전을 식별한다.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from backtest_engine.types.actions import (
    AdjustPosition,
    BasketAction,
    BasketLeg,
    CancelOrder,
    ExecutionPolicy,
    ExecutionStyle,
    ExecutionTiming,
    GroupPolicy,
    LiquidatePosition,
    LiquidationPersistence,
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
)
from backtest_engine.types.decision import StrategyDecision
from backtest_engine.types.events import FillEvent, OrderEvent
from backtest_engine.types.instruments import AssetClass, InstrumentId, Money
from backtest_engine.types.market import PriceField
from backtest_engine.types.orders import (
    LimitOrderRequest,
    MarketOrderRequest,
    OrderCore,
    OrderRequest,
    Side,
    StopLimitOrderRequest,
    StopOrderRequest,
    TimeInForce,
)
from backtest_engine.types.requirements import (
    EngineFeature,
    EventKind,
    EverySession,
    HistoryRequest,
    MonthEndSession,
    Schedule,
    StrategyRequirements,
)

Json = dict[str, object]


class SerdeError(ValueError):
    """직렬화 계약 위반 (알 수 없는 태그, 필드 누락 등)."""


def _expect_dict(value: object, where: str) -> Json:
    if not isinstance(value, dict):
        raise SerdeError(f"expected object at {where} — got {type(value).__name__}: {value!r}")
    return value


def _expect_str(value: object, where: str) -> str:
    if not isinstance(value, str):
        raise SerdeError(f"expected string at {where} — got {type(value).__name__}: {value!r}")
    return value


def _expect_list(value: object, where: str) -> list[object]:
    if not isinstance(value, list):
        raise SerdeError(f"expected array at {where} — got {type(value).__name__}: {value!r}")
    return value


def _expect_float(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SerdeError(f"expected number at {where} — got {type(value).__name__}: {value!r}")
    return float(value)


def _expect_int(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SerdeError(f"expected integer at {where} — got {type(value).__name__}: {value!r}")
    return value


def _expect_bool(value: object, where: str) -> bool:
    if not isinstance(value, bool):
        raise SerdeError(f"expected boolean at {where} — got {type(value).__name__}: {value!r}")
    return value


def _opt_str(value: object, where: str) -> str | None:
    return None if value is None else _expect_str(value, where)


# --- 기본 값 타입 -------------------------------------------------------------


def instrument_to_dict(instrument: InstrumentId) -> Json:
    return {
        "venue": instrument.venue,
        "symbol": instrument.symbol,
        "asset_class": instrument.asset_class.value,
        "currency": instrument.currency,
    }


def instrument_from_dict(data: object) -> InstrumentId:
    obj = _expect_dict(data, "instrument")
    return InstrumentId(
        venue=_expect_str(obj["venue"], "instrument.venue"),
        symbol=_expect_str(obj["symbol"], "instrument.symbol"),
        asset_class=AssetClass(obj["asset_class"]),
        currency=_expect_str(obj["currency"], "instrument.currency"),
    )


def _money_to_dict(money: Money) -> Json:
    return {"amount": str(money.amount), "currency": money.currency}


def _money_from_dict(data: object) -> Money:
    obj = _expect_dict(data, "money")
    return Money(
        amount=Decimal(_expect_str(obj["amount"], "money.amount")),
        currency=_expect_str(obj["currency"], "money.currency"),
    )


def _execution_to_dict(execution: ExecutionPolicy) -> Json:
    return {
        "style": execution.style.value,
        "timing": execution.timing.value,
        "time_in_force": execution.time_in_force.value,
        "max_participation": execution.max_participation,
    }


def _execution_from_dict(data: object) -> ExecutionPolicy:
    obj = _expect_dict(data, "execution")
    raw_participation = obj.get("max_participation")
    return ExecutionPolicy(
        style=ExecutionStyle(obj["style"]),
        timing=ExecutionTiming(obj["timing"]),
        time_in_force=TimeInForce(obj["time_in_force"]),
        max_participation=(
            None
            if raw_participation is None
            else _expect_float(raw_participation, "execution.max_participation")
        ),
    )


# --- 목표값 ------------------------------------------------------------------


def _target_to_dict(target: PositionTarget) -> Json:
    match target:
        case WeightTarget():
            return {
                "type": "weight",
                "instrument": instrument_to_dict(target.instrument),
                "weight": target.weight,
            }
        case QuantityTarget():
            return {
                "type": "quantity",
                "instrument": instrument_to_dict(target.instrument),
                "quantity": str(target.quantity),
            }
        case NotionalTarget():
            return {
                "type": "notional",
                "instrument": instrument_to_dict(target.instrument),
                "notional": _money_to_dict(target.notional),
            }


def _target_from_dict(data: object) -> PositionTarget:
    obj = _expect_dict(data, "target")
    tag = _expect_str(obj["type"], "target.type")
    instrument = instrument_from_dict(obj["instrument"])
    if tag == "weight":
        return WeightTarget(instrument, _expect_float(obj["weight"], "target.weight"))
    if tag == "quantity":
        return QuantityTarget(instrument, Decimal(_expect_str(obj["quantity"], "target.quantity")))
    if tag == "notional":
        return NotionalTarget(instrument, _money_from_dict(obj["notional"]))
    raise SerdeError(f"unknown target type — got {tag!r}, expected weight|quantity|notional")


# --- 주문 요청 ----------------------------------------------------------------


def _order_core_to_dict(core: OrderCore) -> Json:
    return {
        "instrument": instrument_to_dict(core.instrument),
        "side": core.side.value,
        "quantity": str(core.quantity),
        "time_in_force": core.time_in_force.value,
    }


def _order_core_from_dict(data: object) -> OrderCore:
    obj = _expect_dict(data, "order.core")
    return OrderCore(
        instrument=instrument_from_dict(obj["instrument"]),
        side=Side(obj["side"]),
        quantity=Decimal(_expect_str(obj["quantity"], "order.core.quantity")),
        time_in_force=TimeInForce(obj["time_in_force"]),
    )


def _order_request_to_dict(request: OrderRequest) -> Json:
    match request:
        case MarketOrderRequest():
            return {"type": "market", "core": _order_core_to_dict(request.core)}
        case LimitOrderRequest():
            return {
                "type": "limit",
                "core": _order_core_to_dict(request.core),
                "limit_price": str(request.limit_price),
            }
        case StopOrderRequest():
            return {
                "type": "stop",
                "core": _order_core_to_dict(request.core),
                "stop_price": str(request.stop_price),
            }
        case StopLimitOrderRequest():
            return {
                "type": "stop_limit",
                "core": _order_core_to_dict(request.core),
                "stop_price": str(request.stop_price),
                "limit_price": str(request.limit_price),
            }


def _order_request_from_dict(data: object) -> OrderRequest:
    obj = _expect_dict(data, "order_request")
    tag = _expect_str(obj["type"], "order_request.type")
    core = _order_core_from_dict(obj["core"])
    if tag == "market":
        return MarketOrderRequest(core)
    if tag == "limit":
        return LimitOrderRequest(core, Decimal(_expect_str(obj["limit_price"], "limit_price")))
    if tag == "stop":
        return StopOrderRequest(core, Decimal(_expect_str(obj["stop_price"], "stop_price")))
    if tag == "stop_limit":
        return StopLimitOrderRequest(
            core,
            Decimal(_expect_str(obj["stop_price"], "stop_price")),
            Decimal(_expect_str(obj["limit_price"], "limit_price")),
        )
    raise SerdeError(
        f"unknown order request type — got {tag!r}, expected market|limit|stop|stop_limit"
    )


# --- Action ------------------------------------------------------------------


def action_to_dict(action: StrategyAction) -> Json:
    match action:
        case NoAction():
            return {"type": "no_action", "reason": action.reason}
        case SetPortfolioTarget():
            return {
                "type": "set_portfolio_target",
                "targets": [_target_to_dict(target) for target in action.targets],
                "scope": action.scope.value,
                "execution": _execution_to_dict(action.execution),
            }
        case SetPositionTarget():
            return {
                "type": "set_position_target",
                "target": _target_to_dict(action.target),
                "execution": _execution_to_dict(action.execution),
            }
        case AdjustPosition():
            delta: Json
            if isinstance(action.delta, QuantityDelta):
                delta = {"type": "quantity", "quantity": str(action.delta.quantity)}
            else:
                delta = {"type": "notional", "notional": _money_to_dict(action.delta.notional)}
            return {
                "type": "adjust_position",
                "instrument": instrument_to_dict(action.instrument),
                "delta": delta,
                "execution": _execution_to_dict(action.execution),
            }
        case LiquidatePosition():
            return {
                "type": "liquidate_position",
                "instrument": instrument_to_dict(action.instrument),
                "execution": _execution_to_dict(action.execution),
                "cancel_open_orders": action.cancel_open_orders,
                "persistence": action.persistence.value,
            }
        case SubmitOrder():
            return {"type": "submit_order", "request": _order_request_to_dict(action.request)}
        case CancelOrder():
            return {"type": "cancel_order", "order_id": action.order_id}
        case ReplaceOrder():
            return {
                "type": "replace_order",
                "order_id": action.order_id,
                "replacement": _order_request_to_dict(action.replacement),
            }
        case BasketAction():
            return {
                "type": "basket",
                "legs": [action_to_dict(leg) for leg in action.legs],
                "group_policy": action.group_policy.value,
            }


def action_from_dict(data: object) -> StrategyAction:
    obj = _expect_dict(data, "action")
    tag = _expect_str(obj["type"], "action.type")
    if tag == "no_action":
        return NoAction(reason=_opt_str(obj.get("reason"), "no_action.reason"))
    if tag == "set_portfolio_target":
        return SetPortfolioTarget(
            targets=tuple(
                _target_from_dict(item) for item in _expect_list(obj["targets"], "targets")
            ),
            scope=TargetScope(obj["scope"]),
            execution=_execution_from_dict(obj["execution"]),
        )
    if tag == "set_position_target":
        return SetPositionTarget(
            target=_target_from_dict(obj["target"]),
            execution=_execution_from_dict(obj["execution"]),
        )
    if tag == "adjust_position":
        delta_obj = _expect_dict(obj["delta"], "adjust_position.delta")
        delta_tag = _expect_str(delta_obj["type"], "delta.type")
        delta: QuantityDelta | NotionalDelta
        if delta_tag == "quantity":
            delta = QuantityDelta(Decimal(_expect_str(delta_obj["quantity"], "delta.quantity")))
        elif delta_tag == "notional":
            delta = NotionalDelta(_money_from_dict(delta_obj["notional"]))
        else:
            raise SerdeError(f"unknown delta type — got {delta_tag!r}, expected quantity|notional")
        return AdjustPosition(
            instrument=instrument_from_dict(obj["instrument"]),
            delta=delta,
            execution=_execution_from_dict(obj["execution"]),
        )
    if tag == "liquidate_position":
        return LiquidatePosition(
            instrument=instrument_from_dict(obj["instrument"]),
            execution=_execution_from_dict(obj["execution"]),
            cancel_open_orders=_expect_bool(obj["cancel_open_orders"], "cancel_open_orders"),
            persistence=LiquidationPersistence(obj["persistence"]),
        )
    if tag == "submit_order":
        return SubmitOrder(request=_order_request_from_dict(obj["request"]))
    if tag == "cancel_order":
        return CancelOrder(order_id=_expect_str(obj["order_id"], "cancel_order.order_id"))
    if tag == "replace_order":
        return ReplaceOrder(
            order_id=_expect_str(obj["order_id"], "replace_order.order_id"),
            replacement=_order_request_from_dict(obj["replacement"]),
        )
    if tag == "basket":
        legs: list[BasketLeg] = []
        for index, item in enumerate(_expect_list(obj["legs"], "basket.legs")):
            leg = action_from_dict(item)
            leg_types = (SetPositionTarget, AdjustPosition, LiquidatePosition, SubmitOrder)
            if not isinstance(leg, leg_types):
                raise SerdeError(
                    f"invalid basket leg at index {index} — got {type(leg).__name__}, "
                    "expected SetPositionTarget|AdjustPosition|LiquidatePosition|SubmitOrder"
                )
            legs.append(leg)
        return BasketAction(legs=tuple(legs), group_policy=GroupPolicy(obj["group_policy"]))
    raise SerdeError(f"unknown action type — got {tag!r}")


# --- Decision ----------------------------------------------------------------


def decision_to_dict(decision: StrategyDecision) -> Json:
    return {
        "schema_version": decision.schema_version,
        "as_of": decision.as_of.isoformat(),
        "actions": [action_to_dict(action) for action in decision.actions],
        "reason": decision.reason,
    }


def decision_from_dict(data: object) -> StrategyDecision:
    obj = _expect_dict(data, "decision")
    return StrategyDecision(
        schema_version=_expect_int(obj["schema_version"], "decision.schema_version"),
        as_of=datetime.fromisoformat(_expect_str(obj["as_of"], "decision.as_of")),
        actions=tuple(
            action_from_dict(item) for item in _expect_list(obj["actions"], "decision.actions")
        ),
        reason=_opt_str(obj.get("reason"), "decision.reason"),
    )


# --- Requirements ------------------------------------------------------------


def _schedule_to_dict(schedule: Schedule) -> Json:
    match schedule:
        case EverySession():
            return {"type": "every_session"}
        case MonthEndSession():
            return {"type": "month_end_session"}


def _schedule_from_dict(data: object) -> Schedule:
    obj = _expect_dict(data, "schedule")
    tag = _expect_str(obj["type"], "schedule.type")
    if tag == "every_session":
        return EverySession()
    if tag == "month_end_session":
        return MonthEndSession()
    raise SerdeError(
        f"unknown schedule type — got {tag!r}, expected every_session|month_end_session"
    )


def requirements_to_dict(requirements: StrategyRequirements) -> Json:
    return {
        "histories": [
            {
                "instruments": [instrument_to_dict(i) for i in request.instruments],
                "field": request.field.value,
                "lookback": request.lookback,
            }
            for request in requirements.histories
        ],
        "schedule": _schedule_to_dict(requirements.schedule),
        "events": sorted(kind.value for kind in requirements.events),
        "actions": sorted(kind.value for kind in requirements.actions),
        "features": sorted(feature.value for feature in requirements.features),
    }


def requirements_from_dict(data: object) -> StrategyRequirements:
    from backtest_engine.types.actions import ActionKind

    obj = _expect_dict(data, "requirements")
    histories = []
    for index, item in enumerate(_expect_list(obj["histories"], "requirements.histories")):
        request = _expect_dict(item, f"histories[{index}]")
        histories.append(
            HistoryRequest(
                instruments=tuple(
                    instrument_from_dict(i)
                    for i in _expect_list(request["instruments"], f"histories[{index}].instruments")
                ),
                field=PriceField(request["field"]),
                lookback=_expect_int(request["lookback"], f"histories[{index}].lookback"),
            )
        )
    return StrategyRequirements(
        histories=tuple(histories),
        schedule=_schedule_from_dict(obj["schedule"]),
        events=frozenset(
            EventKind(item) for item in _expect_list(obj["events"], "requirements.events")
        ),
        actions=frozenset(
            ActionKind(item) for item in _expect_list(obj["actions"], "requirements.actions")
        ),
        features=frozenset(
            EngineFeature(item) for item in _expect_list(obj["features"], "requirements.features")
        ),
    )


# --- Order / Fill ------------------------------------------------------------


def order_event_to_dict(order: OrderEvent) -> Json:
    return {
        "order_id": order.order_id,
        "decision_id": order.decision_id,
        "ts": order.ts.isoformat(),
        "instrument": instrument_to_dict(order.instrument),
        "quantity": str(order.quantity),
        "side": order.side.value,
        "source_action": action_to_dict(order.source_action),
    }


def order_event_from_dict(data: object) -> OrderEvent:
    obj = _expect_dict(data, "order_event")
    return OrderEvent(
        order_id=_expect_str(obj["order_id"], "order_event.order_id"),
        decision_id=_expect_str(obj["decision_id"], "order_event.decision_id"),
        ts=datetime.fromisoformat(_expect_str(obj["ts"], "order_event.ts")),
        instrument=instrument_from_dict(obj["instrument"]),
        quantity=Decimal(_expect_str(obj["quantity"], "order_event.quantity")),
        side=Side(obj["side"]),
        source_action=action_from_dict(obj["source_action"]),
    )


def fill_event_to_dict(fill: FillEvent) -> Json:
    return {
        "fill_id": fill.fill_id,
        "order_id": fill.order_id,
        "ts": fill.ts.isoformat(),
        "instrument": instrument_to_dict(fill.instrument),
        "quantity": str(fill.quantity),
        "side": fill.side.value,
        "price": fill.price,
        "fee": fill.fee,
        "slippage_per_share": fill.slippage_per_share,
    }


def fill_event_from_dict(data: object) -> FillEvent:
    obj = _expect_dict(data, "fill_event")
    return FillEvent(
        fill_id=_expect_str(obj["fill_id"], "fill_event.fill_id"),
        order_id=_expect_str(obj["order_id"], "fill_event.order_id"),
        ts=datetime.fromisoformat(_expect_str(obj["ts"], "fill_event.ts")),
        instrument=instrument_from_dict(obj["instrument"]),
        quantity=Decimal(_expect_str(obj["quantity"], "fill_event.quantity")),
        side=Side(obj["side"]),
        price=_expect_float(obj["price"], "fill_event.price"),
        fee=_expect_float(obj["fee"], "fill_event.fee"),
        slippage_per_share=_expect_float(
            obj["slippage_per_share"], "fill_event.slippage_per_share"
        ),
    )
