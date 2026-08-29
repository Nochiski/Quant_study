"""BrokerSim 단위 테스트 (4b): 일봉 OHLC 기반 트리거·체결 규칙표.

기준 bar: open 100, high 110, low 90, close 105.
규칙: 시가에서 이미 조건 충족이면 시가, 장중 충족이면 조건 가격, 아니면 미체결.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from backtest_engine.engine.broker import BrokerSim, ExecutionStatus
from backtest_engine.engine.orders import OpenOrder
from backtest_engine.engine.slippage import FixedBpsSlippage
from backtest_engine.types.actions import (
    ExecutionPolicy,
    ExecutionStyle,
    ExecutionTiming,
    NoAction,
    QuantityTarget,
    SetPositionTarget,
    StrategyAction,
)
from backtest_engine.types.events import OrderEvent
from backtest_engine.types.orders import OrderType, Side, TimeInForce
from tests.conftest import day, make_instrument, make_ohlc

INSTRUMENT = make_instrument()
BAR = make_ohlc(day(2), INSTRUMENT, 100.0, 110.0, 90.0, 105.0)


def order(
    order_type: OrderType,
    side: Side,
    limit: float | None = None,
    stop: float | None = None,
    quantity: int = 10,
    triggered: bool = False,
    tif: TimeInForce = TimeInForce.DAY,
    source_action: StrategyAction | None = None,
) -> OpenOrder:
    event = OrderEvent(
        order_id="O-000001",
        decision_id="D-000001",
        ts=day(1),
        instrument=INSTRUMENT,
        quantity=Decimal(quantity),
        side=side,
        source_action=source_action if source_action is not None else NoAction(),
        order_type=order_type,
        limit_price=None if limit is None else Decimal(str(limit)),
        stop_price=None if stop is None else Decimal(str(stop)),
        time_in_force=tif,
    )
    return OpenOrder(order=event, remaining=Decimal(quantity), triggered=triggered)


L, S = OrderType.LIMIT, OrderType.STOP
B, SL = Side.BUY, Side.SELL

RULE_TABLE: list[tuple[str, OpenOrder, float | None]] = [
    ("market buy at open", order(OrderType.MARKET, B), 100.0),
    ("market sell at open", order(OrderType.MARKET, SL), 100.0),
    ("limit buy met at open", order(L, B, limit=105), 100.0),
    ("limit buy met intraday", order(L, B, limit=95), 95.0),
    ("limit buy unmet", order(L, B, limit=85), None),
    ("limit sell met at open", order(L, SL, limit=95), 100.0),
    ("limit sell met intraday", order(L, SL, limit=108), 108.0),
    ("limit sell unmet", order(L, SL, limit=115), None),
    ("stop buy triggered at open", order(S, B, stop=95), 100.0),
    ("stop buy triggered intraday", order(S, B, stop=108), 108.0),
    ("stop buy untriggered", order(S, B, stop=115), None),
    ("stop sell triggered at open", order(S, SL, stop=105), 100.0),
    ("stop sell triggered intraday", order(S, SL, stop=92), 92.0),
    ("stop sell untriggered", order(S, SL, stop=85), None),
    ("stop-limit buy fills at stop", order(OrderType.STOP_LIMIT, B, limit=109, stop=108), 108.0),
    ("stop-limit sell fills at stop", order(OrderType.STOP_LIMIT, SL, limit=91, stop=92), 92.0),
    ("stop-limit buy at open", order(OrderType.STOP_LIMIT, B, limit=101, stop=95), 100.0),
    (
        "stop-limit already triggered acts as limit",
        order(OrderType.STOP_LIMIT, B, limit=107, stop=108, triggered=True),
        100.0,
    ),
    (
        "stop-limit already triggered limit unmet",
        order(OrderType.STOP_LIMIT, SL, limit=115, stop=92, triggered=True),
        None,
    ),
]


@pytest.mark.parametrize(
    ("_name", "open_order", "expected_price"), RULE_TABLE, ids=[r[0] for r in RULE_TABLE]
)
def test_fill_price_rule_table(
    _name: str, open_order: OpenOrder, expected_price: float | None
) -> None:
    outcome = BrokerSim(fee_bps=0.0).execute(
        open_order, BAR, cash_available=1_000_000.0, fill_id="F-1"
    )
    if expected_price is None:
        assert outcome.fill is None
        assert outcome.status is ExecutionStatus.NOT_FILLED
    else:
        assert outcome.fill is not None
        assert outcome.fill.price == pytest.approx(expected_price)
        assert outcome.fill.quantity == Decimal(10)
        assert outcome.status is ExecutionStatus.FILLED


def test_stop_limit_triggered_but_limit_unmet_reports_triggered() -> None:
    # 매수: stop 108에서 발동, 발동가 108 > limit 107 → 체결 없음, TRIGGERED 상태
    outcome = BrokerSim(fee_bps=0.0).execute(
        order(OrderType.STOP_LIMIT, B, limit=107, stop=108), BAR, 1_000_000.0, "F-1"
    )
    assert outcome.fill is None
    assert outcome.status is ExecutionStatus.TRIGGERED_UNFILLED


def test_cash_cap_applies_to_limit_fill_price() -> None:
    # limit buy 95 체결가 95, 현금 500 → 5주
    outcome = BrokerSim(fee_bps=0.0).execute(order(L, B, limit=95), BAR, 500.0, "F-1")
    assert outcome.status is ExecutionStatus.CASH_LIMITED
    assert outcome.fill is not None
    assert outcome.fill.quantity == Decimal(5)
    assert outcome.fill.price == pytest.approx(95.0)


def test_partial_remaining_is_executed_not_original_quantity() -> None:
    open_order = OpenOrder(
        order=order(OrderType.MARKET, B).order, remaining=Decimal(3), triggered=False
    )
    outcome = BrokerSim(fee_bps=0.0).execute(open_order, BAR, 1_000_000.0, "F-1")
    assert outcome.fill is not None
    assert outcome.fill.quantity == Decimal(3)


# --- 4d: 유동성 참여율·슬리피지·IOC/FOK ------------------------------------------


def test_participation_caps_fill_to_share_of_volume() -> None:
    # volume 1,000 × 10% = 100주
    broker = BrokerSim(fee_bps=0.0, max_participation=0.1)
    outcome = broker.execute(order(OrderType.MARKET, B, quantity=500), BAR, 1_000_000.0, "F-1")
    assert outcome.status is ExecutionStatus.LIQUIDITY_LIMITED
    assert outcome.fill is not None
    assert outcome.fill.quantity == Decimal(100)


def test_zero_volume_bar_fills_nothing() -> None:
    empty = make_ohlc(day(2), INSTRUMENT, 100.0, 110.0, 90.0, 105.0, volume=0)
    broker = BrokerSim(fee_bps=0.0, max_participation=0.1)
    outcome = broker.execute(order(OrderType.MARKET, B), empty, 1_000_000.0, "F-1")
    assert outcome.fill is None
    assert outcome.status is ExecutionStatus.NOT_FILLED


def test_participation_and_cash_cap_take_the_smaller() -> None:
    broker = BrokerSim(fee_bps=0.0, max_participation=0.1)  # 유동성 캡 100주
    outcome = broker.execute(order(OrderType.MARKET, B, quantity=500), BAR, 5_000.0, "F-1")
    assert outcome.fill is not None
    assert outcome.fill.quantity == Decimal(50)  # 현금 5,000 / 100 = 50 < 100
    assert outcome.status is ExecutionStatus.CASH_LIMITED


def test_action_policy_participation_overrides_broker_default() -> None:
    policy = ExecutionPolicy(
        ExecutionStyle.MARKET, ExecutionTiming.NEXT_OPEN, TimeInForce.DAY, max_participation=0.05
    )
    action = SetPositionTarget(target=QuantityTarget(INSTRUMENT, Decimal(500)), execution=policy)
    broker = BrokerSim(fee_bps=0.0, max_participation=0.5)
    outcome = broker.execute(
        order(OrderType.MARKET, B, quantity=500, source_action=action), BAR, 1_000_000.0, "F-1"
    )
    assert outcome.fill is not None
    assert outcome.fill.quantity == Decimal(50)  # 1,000 × 5%


def test_fok_not_fully_fillable_is_rejected_without_fill() -> None:
    broker = BrokerSim(fee_bps=0.0, max_participation=0.1)
    outcome = broker.execute(
        order(OrderType.MARKET, B, quantity=500, tif=TimeInForce.FOK), BAR, 1_000_000.0, "F-1"
    )
    assert outcome.fill is None
    assert outcome.status is ExecutionStatus.FOK_REJECTED
    assert "fok" in (outcome.detail or "").lower()


def test_ioc_fills_what_it_can() -> None:
    broker = BrokerSim(fee_bps=0.0, max_participation=0.1)
    outcome = broker.execute(
        order(OrderType.MARKET, B, quantity=500, tif=TimeInForce.IOC), BAR, 1_000_000.0, "F-1"
    )
    assert outcome.fill is not None
    assert outcome.fill.quantity == Decimal(100)


def test_fixed_slippage_moves_market_fill_against_the_order() -> None:
    broker = BrokerSim(fee_bps=0.0, slippage=FixedBpsSlippage(bps=10.0))
    buy = broker.execute(order(OrderType.MARKET, B), BAR, 1_000_000.0, "F-1")
    sell = broker.execute(order(OrderType.MARKET, SL), BAR, 1_000_000.0, "F-2")
    assert buy.fill is not None and sell.fill is not None
    assert buy.fill.price == pytest.approx(100.1)
    assert buy.fill.slippage_per_share == pytest.approx(0.1)
    assert sell.fill.price == pytest.approx(99.9)
    assert sell.fill.slippage_per_share == pytest.approx(0.1)


def test_limit_fill_never_worse_than_limit_after_slippage() -> None:
    broker = BrokerSim(fee_bps=0.0, slippage=FixedBpsSlippage(bps=10.0))
    # limit buy 95, 장중 체결가 95 + 0.095 슬리피지 → 95로 clip, 기록 슬리피지 0
    outcome = broker.execute(order(L, B, limit=95), BAR, 1_000_000.0, "F-1")
    assert outcome.fill is not None
    assert outcome.fill.price == pytest.approx(95.0)
    assert outcome.fill.slippage_per_share == pytest.approx(0.0)
    # limit buy 105, 시가 100 체결 + 0.1 → 100.1 (limit 안이라 clip 없음)
    outcome = broker.execute(order(L, B, limit=105), BAR, 1_000_000.0, "F-2")
    assert outcome.fill is not None
    assert outcome.fill.price == pytest.approx(100.1)


def test_fee_is_charged_on_slipped_notional() -> None:
    broker = BrokerSim(fee_bps=10.0, slippage=FixedBpsSlippage(bps=10.0))
    outcome = broker.execute(order(OrderType.MARKET, B), BAR, 1_000_000.0, "F-1")
    assert outcome.fill is not None
    assert outcome.fill.fee == pytest.approx(10 * 100.1 * 0.001)


def test_invalid_participation_rejected() -> None:
    with pytest.raises(ValueError, match="max_participation"):
        BrokerSim(fee_bps=0.0, max_participation=1.5)


def test_liquidity_cap_uses_exact_decimal_arithmetic() -> None:
    """DEFECT-101: 90 × 0.7 = 63 (float 곱셈 62.999… 로 62가 되면 안 된다)."""
    bar = make_ohlc(day(2), INSTRUMENT, 100.0, 110.0, 90.0, 105.0, volume=90)
    broker = BrokerSim(fee_bps=0.0, max_participation=0.7)
    outcome = broker.execute(order(OrderType.MARKET, B, quantity=1_000), bar, 1_000_000.0, "F-1")
    assert outcome.fill is not None
    assert outcome.fill.quantity == Decimal(63)
