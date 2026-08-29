"""BrokerSim 단위 테스트 (4b): 일봉 OHLC 기반 트리거·체결 규칙표.

기준 bar: open 100, high 110, low 90, close 105.
규칙: 시가에서 이미 조건 충족이면 시가, 장중 충족이면 조건 가격, 아니면 미체결.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from backtest_engine.engine.broker import BrokerSim, ExecutionStatus
from backtest_engine.engine.orders import OpenOrder
from backtest_engine.types.actions import NoAction
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
) -> OpenOrder:
    event = OrderEvent(
        order_id="O-000001",
        decision_id="D-000001",
        ts=day(1),
        instrument=INSTRUMENT,
        quantity=Decimal(quantity),
        side=side,
        source_action=NoAction(),
        order_type=order_type,
        limit_price=None if limit is None else Decimal(str(limit)),
        stop_price=None if stop is None else Decimal(str(stop)),
        time_in_force=TimeInForce.DAY,
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
