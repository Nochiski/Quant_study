"""비용으로 음수가 된 현금의 회복 구간을 실제 원장으로 검증한다."""

from decimal import Decimal

import pytest

from backtest_engine import BacktestEngine, BacktestResult, RunConfig
from backtest_engine.data.feed import DataFeed
from backtest_engine.engine.broker import PythonQuoteCore
from backtest_engine.engine.core import RustQuoteCore
from backtest_engine.engine.portfolio import Portfolio
from backtest_engine.errors import NegativeCashError
from backtest_engine.types.actions import BasketAction, GroupPolicy
from backtest_engine.types.events import CostAccrued, CostKind, FillEvent
from backtest_engine.types.orders import Side
from tests.conftest import day, make_ohlc, make_snapshot
from tests.test_basket import A, B, market_leg
from tests.test_executable_capacity import _ShortBasket


def recovery_scenario(core: str) -> tuple[BacktestEngine, BacktestResult]:
    # D2: B숏1+A롱10→현금3, 차입비7→현금-4/자본5. D3: A5매도→현금1, B1환매→현금0.
    opening = BasketAction(
        (market_leg(B, Side.SELL, 1), market_leg(A, Side.BUY, 10)), GroupPolicy.BEST_EFFORT
    )
    recovery = BasketAction(
        (market_leg(A, Side.SELL, 5), market_leg(B, Side.BUY, 1)), GroupPolicy.BEST_EFFORT
    )
    engine = BacktestEngine(
        RunConfig(
            run_id="negative-cash-recovery",
            initial_cash=12.0,
            fee_bps=0.0,
            annualization_days=1,
            short_borrow_bps_annual=70000.0,
        ),
        core=core,
    )
    bars = tuple(
        make_ohlc(day(i), instrument, 1.0, 1.0, 1.0, 1.0)
        for i in (1, 2, 3)
        for instrument in (A, B)
    )
    return engine, engine.run(_ShortBasket((opening, recovery)), DataFeed(bars))


@pytest.mark.parametrize("core", ["python", "rust", "rust_legacy", "rust_persistent"])
def test_accrued_borrow_cost_can_be_repaired_by_sell_then_cover(core: str) -> None:
    engine, result = recovery_scenario(core)
    assert [(s.cash, s.equity) for s in result.snapshots] == [(12.0, 12.0), (-4.0, 5.0), (0.0, 5.0)]
    assert [(f.instrument, f.side, f.quantity) for f in result.fills if f.ts == day(3)] == [
        (A, Side.SELL, Decimal(5)),
        (B, Side.BUY, Decimal(1)),
    ]
    assert [c.amount for c in engine.event_store.costs() if c.kind is CostKind.SHORT_BORROW] == [
        7.0
    ]


def _seed() -> Portfolio:
    ledger = Portfolio(12.0, allow_short=True)
    ledger.apply(FillEvent("s", "s", day(1), B, Decimal(1), Side.SELL, 1.0, 0, 0, 1000))
    ledger.apply(FillEvent("b", "b", day(1), A, Decimal(10), Side.BUY, 1.0, 0, 0, 1000))
    ledger.charge(CostAccrued(day(2), CostKind.SHORT_BORROW, B, 7.0))
    return ledger


@pytest.mark.parametrize("core", ["python", "rust"])
@pytest.mark.parametrize("fee,tax", [(0.0, 0.0), (0.1, 0.2), (0.0, 1.1), (1.1, 0.0)])
@pytest.mark.parametrize("requested", [3, 4, 5, 10, 15])
def test_negative_cash_feasible_interval_matches_actual_ledger(
    core: str, fee: float, tax: float, requested: int
) -> None:
    quote_core = PythonQuoteCore() if core == "python" else RustQuoteCore()
    quote = quote_core.quote_numbers(
        Side.SELL,
        1.0,
        Decimal(requested),
        1000,
        None,
        0.0,
        None,
        -6.0,
        Decimal(10),
        fee,
        False,
        tax,
        execution_state=(-4.0, (-1.0, 10.0), 1, False),
    )
    feasible = [0]
    for q in range(1, requested + 1):
        ledger = _seed()
        try:
            ledger.apply(
                FillEvent("r", "r", day(3), A, Decimal(q), Side.SELL, 1.0, q * fee, 0, 1000)
            )
        except NegativeCashError:
            continue
        if tax:
            ledger.charge(CostAccrued(day(3), CostKind.SELL_TAX, A, q * tax))
        ledger.mark(
            make_snapshot(
                day(3),
                make_ohlc(day(3), A, 1.0, 1.0, 1.0, 1.0),
                make_ohlc(day(3), B, 1.0, 1.0, 1.0, 1.0),
            )
        )
        state = ledger.snapshot(day(3))
        if state.cash >= 0 and (q <= 10 or state.gross_exposure <= 1):
            feasible.append(q)
    assert quote.quantity == max(feasible)


@pytest.mark.parametrize("core", ["python", "rust"])
def test_near_total_sell_cost_keeps_disconnected_cash_feasible_quantities(core: str) -> None:
    from math import inf, nextafter

    fee, tax = 0.1, nextafter(0.9, inf)
    quote_core = PythonQuoteCore() if core == "python" else RustQuoteCore()
    quote = quote_core.quote_numbers(
        Side.SELL,
        1.0,
        Decimal(20),
        1000,
        None,
        0,
        None,
        -2.0,
        Decimal(30),
        fee,
        False,
        tax,
        execution_state=(-1e-18, (-1.0, 30.0), 1, False),
    )
    feasible = []
    for q in range(1, 21):
        ledger = Portfolio(29.0, allow_short=True)
        ledger.apply(FillEvent("s", "s", day(1), B, Decimal(1), Side.SELL, 1.0, 0, 0, 1000))
        ledger.apply(FillEvent("b", "b", day(1), A, Decimal(30), Side.BUY, 1.0, 0, 0, 1000))
        ledger.charge(CostAccrued(day(2), CostKind.SHORT_BORROW, B, 1e-18))
        ledger.apply(FillEvent("r", "r", day(3), A, Decimal(q), Side.SELL, 1.0, q * fee, 0, 1000))
        ledger.charge(CostAccrued(day(3), CostKind.SELL_TAX, A, q * tax))
        if ledger.cash >= 0:
            feasible.append(q)
    assert max(feasible) == 19
    assert quote.quantity == 19


@pytest.mark.parametrize("core", ["python", "rust"])
def test_risk_rounding_does_not_assume_a_contiguous_feasible_prefix(core: str) -> None:
    from tests.conftest import make_instrument

    x, y = make_instrument("X"), make_instrument("Y")

    def seed() -> Portfolio:
        ledger = Portfolio(2e16 / 3, allow_short=True, allow_margin=True)
        for instrument, side, quantity in (
            (x, Side.BUY, 10**16),
            (A, Side.BUY, 1),
            (y, Side.SELL, 10**16),
        ):
            ledger.apply(
                FillEvent("s", "s", day(1), instrument, Decimal(quantity), side, 1.0, 0, 0, 10**17)
            )
        return ledger

    initial = seed().snapshot(day(1))
    feasible = []
    for q in range(1, 8):
        ledger = seed()
        ledger.apply(FillEvent("r", "r", day(2), A, Decimal(q), Side.SELL, 1.0, 0, 0, 1000))
        state = ledger.snapshot(day(2))
        if q <= 1 or 3 * state.equity - sum(abs(p.market_value) for p in state.positions) >= 0:
            feasible.append(q)
    quote_core = PythonQuoteCore() if core == "python" else RustQuoteCore()
    quote = quote_core.quote_numbers(
        Side.SELL,
        1.0,
        Decimal(7),
        1000,
        None,
        0,
        None,
        0,
        Decimal(1),
        0,
        False,
        0,
        3.0,
        execution_state=(initial.cash, tuple(p.market_value for p in initial.positions), 1, True),
    )
    assert feasible == [1, 2, 3, 6]
    assert quote.quantity == 6


@pytest.mark.parametrize("side", list(Side))
def test_large_normal_order_prunes_without_scanning_each_share(side: Side) -> None:
    from unittest.mock import patch

    from backtest_engine.engine.capacity import _Budget

    calls = 0
    original = _Budget.allows

    def counted(self: _Budget, quantity: int) -> bool:
        nonlocal calls
        calls += 1
        return original(self, quantity)

    with patch.object(_Budget, "allows", counted):
        quote = PythonQuoteCore().quote_numbers(
            side,
            100.0,
            Decimal(10**12),
            10**15,
            None,
            0,
            None,
            1000.0,
            Decimal(0),
            0.001,
            False,
            0.1,
            execution_state=(1000.0, (), 0, False),
        )
    assert quote.quantity == 9
    # 1조 주 주문도 일반 비용에서는 로그 규모의 구간만 평가한다. 후보 허용치가 아니다.
    assert calls < 128
