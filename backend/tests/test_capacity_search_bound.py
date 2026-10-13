"""최대 체결 수량 탐색의 상관 구간 증명·반복 예산과 정확성 경계."""

import random
from decimal import Decimal
from math import inf, nextafter
from unittest.mock import patch

import pytest

from backtest_engine.engine import capacity
from backtest_engine.engine.broker import PythonQuoteCore
from backtest_engine.engine.capacity import SEARCH_BUDGET, _Budget, maximum_quantity
from backtest_engine.engine.core import RustQuoteCore
from backtest_engine.types.orders import Side


def _sell_quote(core: str, quantity: int, fee: float, tax: float, cash: float) -> int:
    quote_core = PythonQuoteCore() if core == "python" else RustQuoteCore()
    return int(
        quote_core.quote_numbers(
            Side.SELL,
            1.0,
            Decimal(quantity),
            2**63 - 1,
            None,
            0,
            None,
            0,
            Decimal(quantity),
            fee,
            False,
            tax,
            execution_state=(cash, (float(quantity),), 0, False),
        ).quantity
    )


def _counting_allows() -> tuple[list[int], object]:
    calls = [0]
    original = _Budget.allows

    def counted(self: _Budget, quantity: int) -> bool:
        calls[0] += 1
        return original(self, quantity)

    return calls, counted


@pytest.mark.parametrize("core", ["python", "rust"])
@pytest.mark.parametrize("excess", [1e-12, 1e-9, 1e-6, 1e-3])
def test_cost_above_notional_is_pruned_by_correlated_bound(core: str, excess: float) -> None:
    # 대금·비용이 같은 n에서 나오는 상관을 쓰지 않으면 구간 끝값을 섞은 상한이 양수로 남아
    # 10만 주에서도 거의 전수 탐색이 됐다. 1조 주 요청도 로그 규모의 후보 확인으로 끝나야 한다.
    calls, counted = _counting_allows()
    with patch.object(_Budget, "allows", counted):
        assert _sell_quote(core, 10**12, 0.25, 0.75 + excess, -1e-18) == 0
    if core == "python":
        assert calls[0] <= 200


@pytest.mark.parametrize("quantity", [10**5, 10**12, 2**62])
def test_rounding_level_cost_cancellation_stops_within_search_budget(quantity: int) -> None:
    # 비용률 합이 100%를 한 ULP만 넘으면 결과가 반올림 방향으로만 정해져 어떤 오차 상한도
    # 구간을 지우지 못한다(2^62주는 반올림이 유리해 실제로 통과하는 수량이 있다).
    # 예산 안에서 찾으면 그 수량이 정답이고, 못 찾으면 체결하지 않는다. 두 코어는 같다.
    tax = nextafter(0.75, inf)
    calls, counted = _counting_allows()
    with patch.object(_Budget, "allows", counted):
        found = _sell_quote("python", quantity, 0.25, tax, -1e-18)
    assert calls[0] <= SEARCH_BUDGET
    assert _sell_quote("rust", quantity, 0.25, tax, -1e-18) == found
    state = (-1e-18, (float(quantity),), 0, False)
    budget = _Budget(False, 1.0, Decimal(quantity), 0.25, tax, 1.0, 0.0, 0.0, 1.0, state)
    assert found == 0 or budget.allows(found)


def test_search_budget_keeps_exact_answer_when_it_is_not_reached() -> None:
    # 예산보다 작은 요청은 예산에 닿지 않고 전수 결과와 같다(예산이 정답을 바꾸지 않는다).
    budget = _Budget(
        False,
        1.0,
        Decimal(1000),
        0.25,
        nextafter(0.75, inf),
        1.0,
        0.0,
        0.0,
        1.0,
        (-1e-18, (1000.0,), 0, False),
    )
    expected = max((q for q in range(1, 1001) if budget.allows(q)), default=0)
    assert expected == 0
    with patch.object(capacity, "SEARCH_BUDGET", 10**6):
        assert (
            maximum_quantity(
                1000,
                buy=False,
                price=1.0,
                held=Decimal(1000),
                fee_rate=0.25,
                tax_rate=nextafter(0.75, inf),
                leverage=1.0,
                equity=0.0,
                gross=0.0,
                mark=1.0,
                state=(-1e-18, (1000.0,), 0, False),
            )
            == expected
        )


@pytest.mark.parametrize("quantity", [10**3, 10**6, 10**12, 2**62])
def test_realistic_buy_request_finishes_in_logarithmic_candidates(quantity: int) -> None:
    calls, counted = _counting_allows()
    with patch.object(_Budget, "allows", counted):
        found = maximum_quantity(
            quantity,
            buy=True,
            price=73_000.0,
            held=Decimal(0),
            fee_rate=0.00015,
            tax_rate=0.0,
            leverage=1.0,
            equity=0.0,
            gross=0.0,
            mark=73_000.0,
            state=(1e8, (), 0, False),
        )
    assert found == min(quantity, 1369)
    assert calls[0] <= 200


def test_interval_pruning_never_drops_an_executable_quantity() -> None:
    # 실제 원장 전수 판정과 대조한다. 비용 합 100% 근방·극단 가격·마진·다보유를 섞는다.
    rng = random.Random(20261013)
    rates = [0.0, 1e-4, 0.0018, 0.25, 0.5, nextafter(0.5, 1), 0.75, nextafter(0.75, 1), 1.0, 2.0]
    prices = [1.0, 3.0, 0.1, 73000.0, 1e-300, 5e-324, 1e300, 7.0 / 3.0]
    cashes = [-4.0, -1e-18, 0.0, 1e-18, 3.0, 300.03, -1e300]
    for _ in range(800):
        buy = rng.random() < 0.5
        price, fee, tax, cash = (rng.choice(values) for values in (prices, rates, rates, cashes))
        held = rng.choice([-30, -5, 0, 5, 17, 60])
        values = [rng.choice([1.0, -3.5, 250.0, 1e-300]) for _ in range(rng.randrange(3))]
        index = rng.randrange(len(values) + 1)
        if held:
            values.insert(index, held * price)
        else:
            index = len(values)
        state = (cash, tuple(values), index, rng.random() < 0.3)
        leverage = rng.choice([0.0, 1.0, 2.0, 4.0])
        budget = _Budget(buy, price, Decimal(held), fee, tax, leverage, 0.0, 0.0, price, state)
        allowed = [q for q in range(1, 49) if budget.allows(q)]
        assert maximum_quantity(
            48,
            buy=buy,
            price=price,
            held=Decimal(held),
            fee_rate=fee,
            tax_rate=tax,
            leverage=leverage,
            equity=0.0,
            gross=0.0,
            mark=price,
            state=state,
        ) == max(allowed, default=0)
        for _ in range(4):
            low = rng.randrange(1, 49)
            high = rng.randrange(low, 49)
            if budget.impossible(low, high):
                assert not [q for q in allowed if low <= q <= high]
