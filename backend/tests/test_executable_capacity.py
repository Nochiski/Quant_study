"""최종 정수 주문은 현금·보유 평가로 검증한다. 견적 역산식은 기대값에 사용하지 않는다."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from math import inf, nextafter

import pytest

from backtest_engine import BacktestEngine, BacktestResult, RunConfig
from backtest_engine.data.feed import DataFeed
from backtest_engine.engine.broker import PythonQuoteCore
from backtest_engine.engine.core import RustQuoteCore
from backtest_engine.types.actions import BasketAction, GroupPolicy
from backtest_engine.types.market import Bar
from backtest_engine.types.orders import Side
from backtest_engine.types.requirements import EngineFeature, StrategyRequirements
from tests.conftest import day, make_ohlc
from tests.test_basket import A, B, BasketStrategy, hold_b, market_leg


@pytest.mark.parametrize("core", ["python", "rust"])
@pytest.mark.parametrize("side", [Side.BUY, Side.SELL])
@pytest.mark.parametrize(
    "cash,expected",
    [
        (0.0, 0),
        (nextafter(110.0, -inf), 0),
        (110.0, 1),
        (nextafter(110.0, inf), 1),
        (nextafter(660.0, -inf), 5),
        (660.0, 6),
        (nextafter(660.0, inf), 6),
    ],
)
def test_exact_integer_boundary_uses_executable_ledger(
    core: str, side: Side, cash: float, expected: int
) -> None:
    quote_core = PythonQuoteCore() if core == "python" else RustQuoteCore()
    # 양쪽 비용10%: 매수는 수수료, 매도는 세금. 6주는 600+60이고 7주는 여력 -110이다.
    fee = 0.1 if side is Side.BUY else 0.0
    tax = 0.1 if side is Side.SELL else 0.0
    quote = quote_core.quote_numbers(
        side, 100.0, Decimal(100), 1000, None, 0.0, None, cash, Decimal(0), fee, False, tax
    )
    assert quote.quantity == expected

    def budget(quantity: int) -> float:
        notional = quantity * 100.0
        remaining_cash = cash - notional * fee - notional * tax
        return remaining_cash - notional

    assert budget(expected) >= 0
    assert budget(expected + 1) < 0


class _ShortBasket(BasketStrategy):
    def requirements(self) -> StrategyRequirements:
        original = super().requirements()
        return replace(original, features=original.features | {EngineFeature.SHORT_SELLING})


def proportional_integer_scenario(
    core: str, a_price: float
) -> tuple[BacktestEngine, BacktestResult]:
    # B3 보유·현금0에서 A2를 사려 한다. A1 한도에 맞춘 B 매도는 floor(3/2)=1뿐이다.
    buy = market_leg(A, Side.BUY, 2)
    basket = BasketAction(
        legs=(market_leg(B, Side.SELL, 3), buy), group_policy=GroupPolicy.PROPORTIONAL
    )
    bars: list[Bar] = [
        make_ohlc(
            day(i), instrument, price, price, price, price, volume=10 if instrument == A else 1000
        )
        for i in (1, 2, 3)
        for instrument, price in ((A, a_price), (B, 50.0))
    ]
    engine = BacktestEngine(
        RunConfig(run_id="integer-basket", initial_cash=150.0, fee_bps=0.0),
        core=core,
        max_participation=0.1,
    )
    result = engine.run(BasketStrategy((hold_b(3), basket)), DataFeed(tuple(bars)))
    return engine, result


@pytest.mark.parametrize("core", ["python", "rust_legacy", "rust_persistent"])
@pytest.mark.parametrize("cash,a_price", [(150.0, 75.0), (150.0, 50.0)])
def test_proportional_requotes_after_integer_sale_contraction(
    core: str, cash: float, a_price: float
) -> None:
    _engine, result = proportional_integer_scenario(core, a_price)
    fills = [
        (fill.instrument, fill.side, fill.quantity) for fill in result.fills if fill.ts == day(3)
    ]
    expected = [(B, Side.SELL, Decimal(1))]
    if a_price == 50.0:
        expected.append((A, Side.BUY, Decimal(1)))
    assert fills == expected
    assert result.snapshots[-1].cash == (50.0 if a_price == 75.0 else 0.0)
    assert result.snapshots[-1].equity == cash
    assert all(s.cash >= 0 and s.gross_exposure <= 1 for s in result.snapshots)


@pytest.mark.parametrize("core", ["python", "rust_legacy", "rust_persistent"])
@pytest.mark.parametrize("grouped", [False, True])
def test_short_integer_budget_is_executable_for_single_and_basket(core: str, grouped: bool) -> None:
    action = market_leg(A, Side.SELL, 100)
    if grouped:
        action = BasketAction(
            legs=(action, market_leg(B, Side.SELL, 1)), group_policy=GroupPolicy.BEST_EFFORT
        )
    bars = tuple(
        make_ohlc(day(i), instrument, 100.0, 100.0, 100.0, 100.0)
        for i in (1, 2)
        for instrument in (A, B)
    )
    engine = BacktestEngine(
        RunConfig(
            run_id="short-integer-boundary",
            initial_cash=660.0,
            fee_bps=0.0,
            sell_tax_schedule=((date(2026, 8, 1), 1000.0),),
        ),
        core=core,
    )
    result = engine.run(_ShortBasket((action,)), DataFeed(bars))
    assert [(f.instrument, f.quantity) for f in result.fills] == [(A, Decimal(6))]
    final = result.snapshots[-1]
    assert final.cash == 1200.0
    assert final.equity == 600.0
    assert final.gross_exposure == 1.0


@pytest.mark.parametrize("core", ["python", "rust"])
@pytest.mark.parametrize("held", [-5, 0, 5, 20])
@pytest.mark.parametrize("price", [0.0, -1.0])
@pytest.mark.parametrize("side", [Side.BUY, Side.SELL])
def test_nonpositive_final_price_is_rejected_before_exposure_or_budget(
    core: str, held: int, price: float, side: Side
) -> None:
    quote_core = PythonQuoteCore() if core == "python" else RustQuoteCore()
    # 매도는 상한 없는 고정 슬리피지로 발생한다. 매수는 코어 경계의 비양수 입력도 막는다.
    base, slip = (100.0, 100.0 - price) if side is Side.SELL else (price, 0.0)
    quote = quote_core.quote_numbers(
        side,
        base,
        Decimal(10),
        1000,
        None,
        slip,
        None,
        1000.0,
        Decimal(held),
        0.0,
        False,
    )
    assert quote.price == price
    assert quote.quantity == 0
    assert quote.status.value == "rejected_no_cash"


@pytest.mark.parametrize("core", ["python", "rust", "rust_legacy", "rust_persistent"])
@pytest.mark.parametrize("bps", [10000.0, 10100.0])
def test_large_fixed_slippage_rejects_short_without_invalid_fill(core: str, bps: float) -> None:
    from backtest_engine.engine.slippage import FixedBpsSlippage

    action = market_leg(A, Side.SELL, 10)
    bars = tuple(make_ohlc(day(i), A, 100.0, 100.0, 100.0, 100.0) for i in (1, 2))
    engine = BacktestEngine(
        RunConfig(run_id="nonpositive-execution-price", initial_cash=1000.0, fee_bps=0.0),
        core=core,
        slippage=FixedBpsSlippage(bps),
    )
    result = engine.run(_ShortBasket((action,)), DataFeed(bars))
    assert not result.fills
    assert result.snapshots[-1].cash == 1000.0
    assert result.snapshots[-1].equity == 1000.0


@pytest.mark.parametrize("core", ["python", "rust"])
def test_valid_sell_limit_clips_slippage_before_price_validation(core: str) -> None:
    quote_core = PythonQuoteCore() if core == "python" else RustQuoteCore()
    quote = quote_core.quote_numbers(
        Side.SELL,
        100.0,
        Decimal(10),
        1000,
        None,
        101.0,
        Decimal(1),
        1000.0,
        Decimal(0),
        0.0,
        False,
    )
    assert (quote.price, quote.quantity, quote.status.value) == (1.0, Decimal(10), "filled")
