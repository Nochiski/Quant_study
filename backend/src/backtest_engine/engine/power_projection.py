"""체결 원장·견적·여력이 공유하는 순차 현금 적용과 평가 합산."""

from collections.abc import Sequence
from decimal import Decimal

ExecutionState = tuple[float, tuple[float, ...], int, bool]


def project_cash(cash: float, buy: bool, notional: float, fee: float, tax: float = 0.0) -> float:
    """실제 원장과 같이 대금, 수수료, 별도 거래세 순서로 적용한다."""
    cash = cash - notional if buy else cash + notional
    cash -= fee
    if tax > 0:
        cash = project_cost(cash, tax)
    return cash


def project_cost(cash: float, amount: float) -> float:
    return cash - amount


def value_portfolio(cash: float, values: Sequence[float]) -> tuple[float, float]:
    return cash + sum(values), sum(abs(value) for value in values)


def project_execution(
    state: ExecutionState,
    buy: bool,
    quantity: Decimal,
    new_quantity: Decimal,
    price: float,
    fee: float,
    tax: float,
) -> tuple[float, float, float]:
    cash, previous, index, _allow_margin = state
    cash = project_cash(cash, buy, float(quantity) * price, fee, tax)
    values = list(previous)
    if index < len(values):
        if new_quantity == 0:
            values.pop(index)
        else:
            values[index] = float(new_quantity) * price
    elif new_quantity != 0:
        values.append(float(new_quantity) * price)
    equity, gross = value_portfolio(cash, values)
    return cash, equity, gross


def project_valuation(
    equity: float,
    gross: float,
    old: Decimal,
    new: Decimal,
    old_mark: float,
    price: float,
    cost: float,
) -> tuple[float, float]:
    """현금 원장이 없던 저수준 호출만 위한 기존 수치 계약."""
    return (
        equity + (float(old) * (price - old_mark) - cost),
        gross + (float(abs(new)) * price - float(abs(old)) * old_mark),
    )
