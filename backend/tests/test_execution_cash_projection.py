"""견적 수량은 공유 산식을 기대값으로 쓰지 않고 실제 원장 적용으로 검증한다."""

from datetime import date
from decimal import Decimal
from math import inf, nextafter

import pytest

from backtest_engine import BacktestEngine, BacktestResult, RunConfig
from backtest_engine.data.feed import DataFeed
from backtest_engine.engine.broker import PythonQuoteCore
from backtest_engine.engine.core import RustQuoteCore, make_buying_power
from backtest_engine.engine.portfolio import Portfolio
from backtest_engine.errors import NegativeCashError
from backtest_engine.types.events import CostAccrued, CostKind, FillEvent
from backtest_engine.types.orders import Side
from tests.conftest import day, make_ohlc, make_snapshot
from tests.test_basket import A, market_leg
from tests.test_executable_capacity import _ShortBasket


def _actual(
    cash: float, price: float, side: Side, fee_rate: float, tax_rate: float, q: int
) -> bool:
    ledger = Portfolio(cash, allow_short=True)
    if q:
        notional = q * price
        tax = notional * tax_rate if side is Side.SELL else 0.0
        fill = FillEvent(
            "f", "o", day(2), A, Decimal(q), side, price, notional * fee_rate, 0, 10000, tax
        )
        try:
            ledger.apply(fill)
        except NegativeCashError:
            return False
        if tax:
            ledger.charge(CostAccrued(day(2), CostKind.SELL_TAX, A, tax))
    ledger.mark(make_snapshot(day(2), make_ohlc(day(2), A, price, price, price, price)))
    actual = ledger.snapshot(day(2))
    return actual.cash >= 0 and actual.equity > 0 and actual.gross_exposure <= 1


@pytest.mark.parametrize("core", ["python", "rust"])
def test_quote_maximum_matches_actual_fill_and_tax_ledger(core: str) -> None:
    quote_core = PythonQuoteCore() if core == "python" else RustQuoteCore()
    for side in Side:
        for price in (0.01, 0.1, 100.0, 123.45):
            for fee in (0.0, 0.0001, 0.0015):
                for tax in (0.0, 0.0001, 0.002):
                    for quantity in (1, 3, 7):
                        target = quantity * price * (1 + fee + (tax if side is Side.SELL else 0))
                        for cash in (nextafter(target, -inf), target, nextafter(target, inf)):
                            quote = quote_core.quote_numbers(
                                side,
                                price,
                                Decimal(quantity + 2),
                                10000,
                                None,
                                0,
                                None,
                                cash,
                                Decimal(0),
                                fee,
                                False,
                                tax,
                                execution_state=(cash, (), 0, False),
                            )
                            expected = max(
                                q
                                for q in range(quantity + 3)
                                if _actual(cash, price, side, fee, tax, q)
                            )
                            assert quote.quantity == expected, (side, price, fee, tax, cash)


@pytest.mark.parametrize("core", ["python", "rust", "rust_legacy", "rust_persistent"])
@pytest.mark.parametrize(
    "side,cash,price,fee,tax,expected",
    [
        (Side.BUY, 300.03, 100.0, 1.0, 0.0, 2),
        (Side.SELL, 0.010001, 0.01, 0.0, 1.0, 0),
        (Side.SELL, nextafter(0.03, -inf), 0.01, 0.0, 0.0, 3),
    ],
)
def test_engine_uses_actual_cash_in_all_execution_paths(
    core: str, side: Side, cash: float, price: float, fee: float, tax: float, expected: int
) -> None:
    _, result = actual_cash_scenario(core, side, cash, price, fee, tax)
    assert sum(int(fill.quantity) for fill in result.fills) == expected
    final = result.snapshots[-1]
    assert final.cash >= 0
    assert final.gross_exposure <= 1


@pytest.mark.parametrize("core", ["python", "rust"])
def test_execution_cash_and_ordered_values_restore_after_group_probe(core: str) -> None:
    from tests.test_basket import B
    from tests.test_portfolio import fill

    ledger = Portfolio(1000.03)
    ledger.apply(fill(Side.BUY, 3, 100.0, fee=0.03))
    power = make_buying_power(core, ledger.snapshot(day(2)), 1.0)
    before = power.execution_state(A)
    saved = power.checkpoint()
    power.consume_quantity(A, Side.SELL, Decimal(3), 100.0, 0.03, 0.01)
    power.consume_quantity(B, Side.BUY, Decimal(1), 100.0, 0.01)
    power.consume_quantity(A, Side.BUY, Decimal(1), 100.0, 0.01)
    assert power.execution_state(A)[2] == 1
    power.restore(saved)
    assert power.execution_state(A) == before
    assert before[0] == ledger.cash


@pytest.mark.parametrize("core", ["python", "rust"])
def test_cash_gate_uses_margin_feature_not_leverage(core: str) -> None:
    quote_core = PythonQuoteCore() if core == "python" else RustQuoteCore()
    # 기존 순수 청산은 노출을 줄이면 여력 음수를 허용한다. MARGIN 선언의 현금 허용도 유지한다.
    for margin, expected in ((False, 0), (True, 1)):
        quote = quote_core.quote_numbers(
            Side.BUY,
            100.0,
            Decimal(1),
            1000,
            None,
            0,
            None,
            0,
            Decimal(-1),
            0,
            False,
            execution_state=(0.0, (-100.0,), 0, margin),
        )
        assert quote.quantity == expected


def actual_cash_scenario(
    core: str,
    side: Side = Side.BUY,
    cash: float = 300.03,
    price: float = 100.0,
    fee: float = 1.0,
    tax: float = 0.0,
) -> tuple[BacktestEngine, BacktestResult]:
    bars = tuple(make_ohlc(day(i), A, price, price, price, price) for i in (1, 2))
    engine = BacktestEngine(
        RunConfig(
            run_id="actual-cash-boundary",
            initial_cash=cash,
            fee_bps=fee,
            sell_tax_schedule=((date(2026, 8, 1), tax),),
        ),
        core=core,
    )
    result = engine.run(_ShortBasket((market_leg(A, side, 3),)), DataFeed(bars))
    return engine, result
