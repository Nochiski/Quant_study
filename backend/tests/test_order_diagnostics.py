"""체결 0주 사유는 견적 판정을 기록하며 가격 조건·다른 leg에 전파하지 않는다."""

from dataclasses import replace
from decimal import Decimal

import pytest

from backtest_engine import RunConfig
from backtest_engine.data.feed import DataFeed
from backtest_engine.engine.broker import BrokerSim
from backtest_engine.types.actions import BasketAction, GroupPolicy
from backtest_engine.types.events import OrderStatus
from backtest_engine.types.orders import Side
from tests import test_basket, test_margin
from tests.conftest import day, make_bar
from tests.test_core_parity import RUST_ENGINE_CORES, _engine_scenario


@pytest.mark.parametrize("core", [pytest.param("python", id="python"), *RUST_ENGINE_CORES])
@pytest.mark.parametrize("cap_volume", [0, 1])
def test_zero_share_cap_records_structured_reason_on_the_order(core: str, cap_volume: int) -> None:
    bars = tuple(
        replace(make_bar(day(i), test_margin.INSTRUMENT, 100.0, 100.0), liquidity_volume=cap_volume)
        for i in (1, 2)
    )
    engine, result = _engine_scenario(
        core,
        RunConfig(run_id="no-liquidity", initial_cash=1000.0),
        test_margin.MarginStrategy((test_margin.target(5),)),
        DataFeed(bars),
        max_participation=0.1,
    )
    assert not result.fills
    updates = engine.event_store.order_updates()
    diagnosed = [item for item in updates if item.reason_code == "liquidity_cap_zero"]
    assert len(diagnosed) == 1
    assert diagnosed[0].status is OrderStatus.OPEN
    assert diagnosed[0].cap_volume == cap_volume
    assert diagnosed[0].ts == day(2)


@pytest.mark.parametrize("core", [pytest.param("python", id="python"), *RUST_ENGINE_CORES])
@pytest.mark.parametrize("policy", list(GroupPolicy))
def test_basket_zero_cap_diagnoses_only_the_affected_leg(core: str, policy: GroupPolicy) -> None:
    bars = tuple(
        replace(
            make_bar(day(i), instrument, 100.0, 100.0),
            liquidity_volume=0 if instrument == test_basket.A else 1000,
        )
        for i in (1, 2)
        for instrument in (test_basket.A, test_basket.B)
    )
    action = BasketAction(
        legs=(
            test_basket.market_leg(test_basket.A, Side.BUY, 5),
            test_basket.market_leg(test_basket.B, Side.BUY, 5),
        ),
        group_policy=policy,
    )
    engine, _ = _engine_scenario(
        core,
        RunConfig(run_id="no-liquidity-basket", initial_cash=10000.0),
        test_basket.BasketStrategy((action,)),
        DataFeed(bars),
        max_participation=0.1,
    )
    orders = {order.order_id: order for order in engine.event_store.orders()}
    diagnosed = [
        item for item in engine.event_store.order_updates() if item.reason_code is not None
    ]
    assert len(diagnosed) == 1
    assert orders[diagnosed[0].order_id].instrument == test_basket.A
    assert diagnosed[0].reason_code == "liquidity_cap_zero"
    assert diagnosed[0].cap_volume == 0


def test_unmet_price_condition_is_not_a_liquidity_diagnostic() -> None:
    from backtest_engine.engine.orders import OpenOrder
    from backtest_engine.types.orders import OrderType

    entry = test_margin.market_buy(5)
    order = replace(entry.order, order_type=OrderType.LIMIT, limit_price=Decimal(10))
    bar = replace(test_margin.BAR, liquidity_volume=0)
    quote = BrokerSim(0.0, max_participation=0.1).quote(OpenOrder(order, Decimal(5)), bar, 1000.0)
    assert quote.quantity == 0
    assert quote.reason_code is None
    assert quote.cap_volume is None


def test_order_update_decoder_preserves_legacy_absence_and_new_zero() -> None:
    from backtest_engine.engine.store import PersistentEventStore

    store = PersistentEventStore(object())
    old = store.order_update_from_wire(day(2), ("O-1", "open", "old detail"))
    new = store.order_update_from_wire(
        day(2), ("O-1", "open", "new detail", "liquidity_cap_zero", 0)
    )
    assert (old.reason_code, old.cap_volume) == (None, None)
    assert (new.reason_code, new.cap_volume) == ("liquidity_cap_zero", 0)
