"""실제 부동소수점 원장 조건을 만족하는 최대 정수 수량 탐색.

현금·위험 잔여량은 상쇄 반올림 때문에 수량에 단조라는 보장이 없다. 구간 상한(연산별
단조성, 같은 대금의 상관과 IEEE 반올림 오차 상한)은 불가능성 증명에만 쓰고, 허용 판정은
항상 실제 원장식이다.
"""

import math
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from sys import float_info

from backtest_engine.engine.power_projection import (
    ExecutionState,
    project_execution,
    project_valuation,
)

# IEEE 이진 배정밀도 반올림 단위 u와 최소 subnormal(언더플로 절대 오차 상한 η 이상).
_UNIT = 2.0**-53
_TINY = math.ulp(0.0)
# 현금 원장은 연산 5회다. 끝점 원장값과 실수 모형의 양방향 차이(2·5u)를 덮는 배수.
_CASH_ERROR = 16
# 위험 평가의 포지션 합산 외 연산 수(대금·비용·현금·평가액·레버리지·총노출) 상한.
_RISK_OPERATIONS = 12
# 탐색 반복 예산. 증명으로 지울 수 없는 후보는 매도 비용률 합이 100%를 반올림 오차만큼
# 넘는 상쇄 입력에서만 남고, 그 밖의 입력은 2^62주 요청도 수백 회 안에 끝난다.
SEARCH_BUDGET = 4096


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

    def _ledger(self, state: ExecutionState, quantity: int) -> tuple[float, float, float]:
        notional = quantity * self.price
        fee = notional * self.fee_rate
        tax = 0.0 if self.buy else notional * self.tax_rate
        return project_execution(
            state, self.buy, Decimal(quantity), self.new_quantity(quantity), self.price, fee, tax
        )

    def allows(self, quantity: int) -> bool:
        notional = quantity * self.price
        fee = notional * self.fee_rate
        tax = 0.0 if self.buy else notional * self.tax_rate
        new = self.new_quantity(quantity)
        if self.state is not None:
            cash, equity, gross = self._ledger(self.state, quantity)
            if not self.state[3] and not cash >= 0:
                return False
        else:
            equity, gross = project_valuation(
                self.equity, self.gross, self.held, new, self.mark, self.price, fee + tax
            )
        return quantity <= self.closing or self.leverage * equity - gross >= 0

    def _correlated_impossible(self, state: ExecutionState, low: int, high: int) -> bool:
        """같은 대금 n에서 나온 대금·비용의 상관을 쓰는 구간 불가능 증명.

        실수 모형의 현금 c±n−f·n−t·n은 n에 1차, 위험 L·평가액−총노출은 q에 오목이다.
        그래서 구간 최댓값은 끝점(위험은 보유 0 꺾임점 포함)에서 나온다. 실제 원장값과
        실수 모형의 차이는 연산마다 u·|결과|+η 이하라, 끝점 원장값에 그 합의 두 배를
        더해도 음수이면 구간 안의 어떤 수량도 실제 원장에서 음수다.
        """
        cash, previous, _index, margin = state
        notional_high = high * self.price
        if not math.isfinite(notional_high) or not math.isfinite(cash):
            return False
        lows, highs = self._ledger(state, low), self._ledger(state, high)
        if not (math.isfinite(lows[0]) and math.isfinite(highs[0])):
            return False
        costs = 1 + self.fee_rate + (0.0 if self.buy else self.tax_rate)
        if not margin:
            scale = abs(cash) + notional_high * costs
            bound = max(lows[0], highs[0]) + (_CASH_ERROR * _UNIT * scale + _CASH_ERROR * _TINY)
            if bound < 0:
                return True
        if low <= self.closing or self.leverage < 0:
            return False
        points = [lows, highs]
        kink = -self.held if self.buy else self.held
        for rounding in (ROUND_FLOOR, ROUND_CEILING):
            nearest = int(kink.to_integral_value(rounding))
            if low < nearest < high:
                points.append(self._ledger(state, nearest))
        risks = [self.leverage * equity - gross for _cash, equity, gross in points]
        if not all(math.isfinite(value) for value in risks):
            return False
        risk = max(risks)
        held_value = max(abs(float(self.new_quantity(low))), abs(float(self.new_quantity(high))))
        scale = (self.leverage + 1) * (
            abs(cash)
            + 2 * notional_high * costs
            + sum(abs(value) for value in previous)
            + 2 * held_value * self.price
        )
        terms = 2 * (len(previous) + _RISK_OPERATIONS)
        if not math.isfinite(scale):
            return False
        return risk + (terms * _UNIT * scale + terms * (self.leverage + 1) * _TINY) < 0

    def impossible(self, low: int, high: int) -> bool:
        """구간 안의 어떤 수량도 실제 원장을 통과할 수 없음이 증명될 때만 구간을 버린다."""
        if self.state is not None and self._correlated_impossible(self.state, low, high):
            return True
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
            if (
                not margin
                and not self.buy
                and cash <= 0
                and notional_low >= float_info.min
                and (
                    (self.fee_rate > 1 and self.tax_rate >= 0)
                    or (self.tax_rate > 1 and self.fee_rate >= 0)
                )
            ):
                # 유한 normal n은 r>1이면 fl(n*r)>n이다. 최소 r-1=2^-52가
                # n의 위쪽 ULP 이상이므로 반올림으로 n에 머물 수 없다.
                # fl(cash+n)<=n에서 개별 비용 하나가 n을 넘으면 현금은 엄격히 음수다.
                # n이 +inf로 넘친 경우에도 실제 현금은 inf-inf/0*inf로 NaN이라 불허다.
                # subnormal은 fl(n*r)==n일 수 있으므로 이 증명에서 제외한다.
                return True
            if (
                not margin
                and not self.buy
                and cash <= 0
                and notional_low >= 2 * float_info.min
                and (
                    (self.fee_rate >= 0.5 and self.tax_rate > 0.5)
                    or (self.fee_rate > 0.5 and self.tax_rate >= 0.5)
                )
            ):
                # h=n/2가 정확한 normal이면 각 비용을 h와 비교할 수 있다.
                # fee>=h,tax>h는 남은 대금<=h 뒤 엄격 음수다. 반대 방향도
                # fee<=n에서 Sterbenz에 의해 n-fee<h가 정확하고 tax>=h다.
                return True
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
    """`quantity` 이하에서 실제 원장을 통과하는 최대 정수 수량.

    큰 수량부터 확인하므로 처음 허용된 수량이 정답이다. 반복이 `SEARCH_BUDGET`을 넘으면
    그때까지 허용된 수량이 없다는 뜻이라 0(체결 없음)을 돌려준다. 장부는 음수가 되지 않고,
    정확한 최대 대신 하한이 되는 것은 증명으로 후보를 지울 수 없는 상쇄 입력뿐이다.
    """
    budget = _Budget(buy, price, held, fee_rate, tax_rate, leverage, equity, gross, mark, state)
    pending = [(1, quantity)]
    steps = 0
    while pending:
        steps += 1
        if steps > SEARCH_BUDGET:
            return 0
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
        # 빈 구간을 넣지 않아 반복 수(예산)가 Rust `Capacity::maximum`과 같다.
        if middle < high:
            pending.append((middle + 1, high))
    return 0
