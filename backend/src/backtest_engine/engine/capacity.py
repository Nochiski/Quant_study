"""실제 부동소수점 원장 조건을 만족하는 최대 정수 수량 탐색.

현금·위험 잔여량은 상쇄 반올림 때문에 수량에 단조라는 보장이 없다. 각 연산의
단조성을 이용한 구간 상한만 불가능성 증명에 쓰고, 허용 판정은 항상 실제 원장식이다.
"""

from dataclasses import dataclass
from decimal import Decimal

from backtest_engine.engine.power_projection import (
    ExecutionState,
    project_execution,
    project_valuation,
)


@dataclass(frozen=True)
class _Budget:
    buy: bool
    price: float
    held: Decimal
    fee_rate: float
    tax_rate: float
    leverage: float
    equity: float
    gross: float
    mark: float
    state: ExecutionState | None

    @property
    def closing(self) -> Decimal:
        return max(-self.held if self.buy else self.held, Decimal(0))

    def new_quantity(self, quantity: int) -> Decimal:
        return self.held + (Decimal(quantity) if self.buy else -Decimal(quantity))

    def allows(self, quantity: int) -> bool:
        notional = quantity * self.price
        fee = notional * self.fee_rate
        tax = 0.0 if self.buy else notional * self.tax_rate
        new = self.new_quantity(quantity)
        if self.state is not None:
            cash, equity, gross = project_execution(
                self.state, self.buy, Decimal(quantity), new, self.price, fee, tax
            )
            if not self.state[3] and not cash >= 0:
                return False
        else:
            equity, gross = project_valuation(
                self.equity, self.gross, self.held, new, self.mark, self.price, fee + tax
            )
        return quantity <= self.closing or self.leverage * equity - gross >= 0

    def impossible(self, low: int, high: int) -> bool:
        """독립 피연산자의 극값으로 만든 상한이 음수인 경우에만 구간을 버린다."""
        notional_low, notional_high = low * self.price, high * self.price
        fee_low = min(notional_low * self.fee_rate, notional_high * self.fee_rate)
        tax_low = (
            0.0 if self.buy else min(notional_low * self.tax_rate, notional_high * self.tax_rate)
        )
        new_low, new_high = self.new_quantity(low), self.new_quantity(high)
        value_low = float(min(new_low, new_high)) * self.price
        value_high = float(max(new_low, new_high)) * self.price
        gross_min = 0.0 if value_low <= 0 <= value_high else min(abs(value_low), abs(value_high))
        if self.state is not None:
            cash, previous, index, margin = self.state
            cash_upper = cash - notional_low if self.buy else cash + notional_high
            cash_upper -= fee_low
            if tax_low > 0:
                cash_upper -= tax_low
            if not margin and cash_upper < 0:
                return True
            upper_values = list(previous)
            lower_gross_values = [abs(value) for value in previous]
            if index < len(previous):
                upper_values[index] = value_high
                lower_gross_values[index] = gross_min
            else:
                upper_values.append(value_high)
                lower_gross_values.append(gross_min)
            equity_upper = cash_upper + sum(upper_values)
            gross_lower = sum(lower_gross_values)
        else:
            equity_upper = self.equity + (
                float(self.held) * (self.price - self.mark) - (fee_low + tax_low)
            )
            gross_lower = self.gross + (gross_min - float(abs(self.held)) * self.mark)
        # 청산 구간은 기존 정책대로 위험 한도 검사를 면제한다.
        return low > self.closing and self.leverage * equity_upper - gross_lower < 0


def maximum_quantity(
    quantity: int,
    *,
    buy: bool,
    price: float,
    held: Decimal,
    fee_rate: float,
    tax_rate: float,
    leverage: float,
    equity: float,
    gross: float,
    mark: float,
    state: ExecutionState | None,
) -> int:
    budget = _Budget(buy, price, held, fee_rate, tax_rate, leverage, equity, gross, mark, state)
    pending = [(1, quantity)]
    while pending:
        low, high = pending.pop()
        if low > high:
            continue
        if budget.allows(high):
            return high
        high -= 1
        if low > high or budget.impossible(low, high):
            continue
        middle = (low + high) // 2
        # 높은 구간을 먼저 처리하므로 처음 허용된 수량이 전체 최대다.
        pending.append((low, middle))
        pending.append((middle + 1, high))
    return 0
