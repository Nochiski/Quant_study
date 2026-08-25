"""직렬화 계약 round-trip 테스트 (로드맵 1단계: 스키마 고정)."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from backtest_engine.types.actions import (
    ActionKind,
    AdjustPosition,
    BasketAction,
    CancelOrder,
    ExecutionPolicy,
    GroupPolicy,
    LiquidatePosition,
    LiquidationPersistence,
    NoAction,
    NotionalTarget,
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
from backtest_engine.types.decision import SCHEMA_VERSION, StrategyDecision
from backtest_engine.types.events import FillEvent, OrderEvent
from backtest_engine.types.instruments import Money
from backtest_engine.types.market import PriceField
from backtest_engine.types.orders import (
    LimitOrderRequest,
    MarketOrderRequest,
    OrderCore,
    Side,
    StopLimitOrderRequest,
    TimeInForce,
)
from backtest_engine.types.requirements import (
    EngineFeature,
    EventKind,
    EverySession,
    HistoryRequest,
    StrategyRequirements,
)
from backtest_engine.types.serde import (
    SerdeError,
    action_from_dict,
    action_to_dict,
    decision_from_dict,
    decision_to_dict,
    fill_event_from_dict,
    fill_event_to_dict,
    order_event_from_dict,
    order_event_to_dict,
    requirements_from_dict,
    requirements_to_dict,
)
from tests.conftest import day, make_instrument

INSTRUMENT = make_instrument()
EXECUTION = ExecutionPolicy.market_next_open()
ORDER_CORE = OrderCore(
    instrument=INSTRUMENT, side=Side.BUY, quantity=Decimal(10), time_in_force=TimeInForce.DAY
)

ALL_ACTIONS: tuple[StrategyAction, ...] = (
    NoAction(reason="idle"),
    SetPortfolioTarget(
        targets=(
            WeightTarget(INSTRUMENT, 0.7),
            QuantityTarget(make_instrument("000660"), Decimal(5)),
        ),
        scope=TargetScope.REPLACE,
        execution=EXECUTION,
    ),
    SetPositionTarget(
        target=NotionalTarget(INSTRUMENT, Money(Decimal("1000000"), "KRW")), execution=EXECUTION
    ),
    AdjustPosition(instrument=INSTRUMENT, delta=QuantityDelta(Decimal(-3)), execution=EXECUTION),
    LiquidatePosition(
        instrument=INSTRUMENT,
        execution=ExecutionPolicy.market_next_available(),
        cancel_open_orders=True,
        persistence=LiquidationPersistence.UNTIL_FLAT,
    ),
    SubmitOrder(request=LimitOrderRequest(core=ORDER_CORE, limit_price=Decimal(70000))),
    CancelOrder(order_id="O-000001"),
    ReplaceOrder(
        order_id="O-000001",
        replacement=StopLimitOrderRequest(
            core=ORDER_CORE, stop_price=Decimal(69000), limit_price=Decimal(68000)
        ),
    ),
    BasketAction(
        legs=(
            SetPositionTarget(target=WeightTarget(INSTRUMENT, 0.5), execution=EXECUTION),
            SubmitOrder(request=MarketOrderRequest(core=ORDER_CORE)),
        ),
        group_policy=GroupPolicy.PROPORTIONAL,
    ),
)


@pytest.mark.parametrize("action", ALL_ACTIONS, ids=lambda action: type(action).__name__)
def test_action_round_trip(action: StrategyAction) -> None:
    encoded = json.dumps(action_to_dict(action))  # JSON 호환성까지 확인
    assert action_from_dict(json.loads(encoded)) == action


def test_decision_round_trip_preserves_schema_version() -> None:
    decision = StrategyDecision(
        schema_version=SCHEMA_VERSION,
        as_of=day(17),
        actions=(NoAction("wait"), ALL_ACTIONS[1]),
        reason="test",
    )
    restored = decision_from_dict(decision_to_dict(decision))
    assert restored == decision
    assert restored.schema_version == SCHEMA_VERSION


def test_requirements_round_trip() -> None:
    requirements = StrategyRequirements(
        histories=(
            HistoryRequest(instruments=(INSTRUMENT,), field=PriceField.CLOSE, lookback=60),
        ),
        schedule=EverySession(),
        events=frozenset({EventKind.MARKET, EventKind.FILL}),
        actions=frozenset({ActionKind.NO_ACTION, ActionKind.BASKET}),
        features=frozenset({EngineFeature.SHORT_SELLING}),
    )
    assert requirements_from_dict(requirements_to_dict(requirements)) == requirements


def test_order_and_fill_round_trip() -> None:
    order = OrderEvent(
        order_id="O-000001",
        decision_id="D-000001",
        ts=day(17),
        instrument=INSTRUMENT,
        quantity=Decimal(10),
        side=Side.BUY,
        source_action=ALL_ACTIONS[1],
    )
    fill = FillEvent(
        fill_id="F-000001",
        order_id="O-000001",
        ts=day(18),
        instrument=INSTRUMENT,
        quantity=Decimal(4),
        side=Side.BUY,
        price=71000.0,
        fee=100.0,
        slippage_per_share=0.0,
    )
    assert order_event_from_dict(order_event_to_dict(order)) == order
    assert fill_event_from_dict(fill_event_to_dict(fill)) == fill


def test_unknown_action_tag_rejected() -> None:
    with pytest.raises(SerdeError, match="unknown action type"):
        action_from_dict({"type": "teleport_position"})


def test_basket_leg_type_enforced() -> None:
    invalid = {
        "type": "basket",
        "legs": [{"type": "no_action", "reason": None}],  # NoAction은 BasketLeg가 아니다
        "group_policy": "proportional",
    }
    with pytest.raises(SerdeError, match="invalid basket leg"):
        action_from_dict(invalid)
