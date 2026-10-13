"""대금보다 큰 개별 비용의 엄격한 불가능성 증명과 성능 경계."""

from decimal import Decimal
from math import inf, ldexp, nextafter
from sys import float_info
from unittest.mock import patch

import pytest

from backtest_engine.engine.broker import PythonQuoteCore
from backtest_engine.engine.capacity import _Budget
from backtest_engine.engine.core import RustQuoteCore
from backtest_engine.engine.portfolio import Portfolio
from backtest_engine.errors import NegativeCashError
from backtest_engine.types.events import CostAccrued, CostKind, FillEvent
from backtest_engine.types.orders import Side
from tests.conftest import day
from tests.test_basket import A


@pytest.mark.parametrize("core", ["python", "rust"])
@pytest.mark.parametrize("cash", [-1e-18, 0.0])
@pytest.mark.parametrize(
    "fee,tax",
    [
        (nextafter(1.0, inf), 0.0),
        (0.0, nextafter(1.0, inf)),
        (0.5, nextafter(0.5, inf)),
        (nextafter(0.5, inf), 0.5),
    ],
)
@pytest.mark.parametrize("price", [2 * float_info.min, 1.0, 1e308])
def test_trillion_share_dominated_cost_is_rejected_as_one_interval(
    core: str, cash: float, fee: float, tax: float, price: float
) -> None:
    quote_core = PythonQuoteCore() if core == "python" else RustQuoteCore()
    calls = 0
    original = _Budget.allows

    def counted(self: _Budget, quantity: int) -> bool:
        nonlocal calls
        calls += 1
        assert calls == 1, "전 구간 불가능성 증명이 대량 후보 순회로 퇴행했다"
        return original(self, quantity)

    with patch.object(_Budget, "allows", counted):
        quote = quote_core.quote_numbers(
            Side.SELL,
            price,
            Decimal(10**12),
            10**15,
            None,
            0,
            None,
            0,
            Decimal(10**12),
            fee,
            False,
            tax,
            execution_state=(cash, (10**12 * price,), 0, False),
        )
    assert quote.quantity == 0
    if core == "python":
        assert calls == 1


def test_strict_normal_product_proof_at_every_binary_exponent() -> None:
    rate = nextafter(1.0, inf)
    for exponent in range(-1022, 1024):
        power = ldexp(1.0, exponent)
        for n in (nextafter(power, 0.0), power, nextafter(power, inf)):
            if n >= float_info.min:
                assert n * rate > n
            if n >= 2 * float_info.min:
                half = n * 0.5
                higher_half = n * nextafter(0.5, inf)
                assert higher_half > half
                assert n - higher_half < half
    assert float_info.max * rate == inf


@pytest.mark.parametrize("core", ["python", "rust"])
def test_subnormal_product_rounding_is_not_rejected_by_normal_certificate(core: str) -> None:
    price = float_info.min / 2
    rate = nextafter(1.0, inf)
    # 서브노멀에서는 증가분이 반 ULP에 걸려 원래 대금으로 반올림된다. 1주는 현금0으로 가능하다.
    assert price * rate == price
    ledger = Portfolio(0.0, allow_short=True)
    ledger.apply(FillEvent("f", "o", day(1), A, Decimal(1), Side.SELL, price, price * rate, 0, 10))
    assert ledger.cash == 0.0
    quote_core = PythonQuoteCore() if core == "python" else RustQuoteCore()
    quote = quote_core.quote_numbers(
        Side.SELL,
        price,
        Decimal(2),
        1000,
        None,
        0,
        None,
        0,
        Decimal(2),
        rate,
        False,
        execution_state=(0.0, (2 * price,), 0, False),
    )
    assert quote.quantity == 1


@pytest.mark.parametrize(
    "fee,tax",
    [
        (nextafter(1.0, inf), 0.0),
        (0.0, nextafter(1.0, inf)),
        (0.5, nextafter(0.5, inf)),
        (nextafter(0.5, inf), 0.5),
    ],
)
def test_certificate_rejection_matches_actual_fill_then_cost(fee: float, tax: float) -> None:
    for quantity in (1, 3, 19, 10000):
        ledger = Portfolio(0.0, allow_short=True)
        ledger.charge(CostAccrued(day(1), CostKind.SHORT_BORROW, A, 1e-18))
        try:
            ledger.apply(
                FillEvent(
                    "f", "o", day(2), A, Decimal(quantity), Side.SELL, 1.0, quantity * fee, 0, 10000
                )
            )
        except NegativeCashError:
            assert fee > 0
            continue
        ledger.charge(CostAccrued(day(2), CostKind.SELL_TAX, A, quantity * tax))
        assert ledger.cash < 0


@pytest.mark.parametrize("core", ["python", "rust"])
def test_cost_certificate_does_not_reject_margin_closing(core: str) -> None:
    quote_core = PythonQuoteCore() if core == "python" else RustQuoteCore()
    quote = quote_core.quote_numbers(
        Side.SELL,
        1.0,
        Decimal(20),
        1000,
        None,
        0,
        None,
        0,
        Decimal(20),
        nextafter(1.0, inf),
        False,
        execution_state=(-1e-18, (20.0,), 0, True),
    )
    assert quote.quantity == 20


@pytest.mark.parametrize("core", ["python", "rust"])
def test_half_cost_certificate_preserves_subnormal_feasible_boundary(core: str) -> None:
    price = float_info.min / 2
    tax = nextafter(0.5, inf)
    quote_core = PythonQuoteCore() if core == "python" else RustQuoteCore()
    quote = quote_core.quote_numbers(
        Side.SELL,
        price,
        Decimal(4),
        1000,
        None,
        0,
        None,
        0,
        Decimal(4),
        0.5,
        False,
        tax,
        execution_state=(0.0, (4 * price,), 0, False),
    )
    ledger = Portfolio(0.0, allow_short=True)
    n = 2 * price
    ledger.apply(FillEvent("f", "o", day(1), A, Decimal(2), Side.SELL, price, n * 0.5, 0, 10))
    ledger.charge(CostAccrued(day(1), CostKind.SELL_TAX, A, n * tax))
    assert ledger.cash == 0
    assert quote.quantity == 2
