"""Fill 밖의 비용 계산: 세션 종료 숏 차입 비용·신용 이자(5단계)와 매도 거래세. 순수 함수."""

from __future__ import annotations

from decimal import Decimal

from backtest_engine.types.events import CostAccrued, CostKind, FillEvent
from backtest_engine.types.orders import Side
from backtest_engine.types.portfolio import PortfolioSnapshot
from backtest_engine.types.results import RunConfig


def sell_tax_amount(side: Side, quantity: Decimal, price: float, rate: float) -> float:
    """매도 체결 금액 × 거래세율. 매수는 0이다.

    연산 순서(수량 × 가격 × 세율)가 Rust 코어 `session::sell_tax`와 같아야 두 코어가 같은 비트를
    낸다.
    """
    return float(quantity) * price * rate if side is Side.SELL else 0.0


def sell_tax(fill: FillEvent, config: RunConfig) -> CostAccrued | None:
    """매도 체결 하나의 거래세 기록. 세금이 0이면 기록하지 않는다."""
    rate = config.sell_tax_rate(fill.ts.date())
    amount = sell_tax_amount(fill.side, fill.quantity, fill.price, rate)
    if amount <= 0:
        return None
    return CostAccrued(
        ts=fill.ts, kind=CostKind.SELL_TAX, instrument=fill.instrument, amount=amount
    )


def session_costs(snapshot: PortfolioSnapshot, config: RunConfig) -> tuple[CostAccrued, ...]:
    """세션 종료 평가 상태에서 발생하는 비용. 0인 비용은 기록하지 않는다.

    숏 차입: |숏 평가액| × short_borrow_bps_annual / 10,000 / annualization_days (종목별).
    신용 이자: |음수 현금| × margin_interest_bps_annual / 10,000 / annualization_days.
    """
    costs: list[CostAccrued] = []
    borrow_daily = config.short_borrow_bps_annual / 10_000.0 / config.annualization_days
    if borrow_daily > 0:
        for position in snapshot.positions:
            if position.quantity < 0:
                amount = abs(position.market_value) * borrow_daily
                if amount > 0:
                    costs.append(
                        CostAccrued(
                            ts=snapshot.ts,
                            kind=CostKind.SHORT_BORROW,
                            instrument=position.instrument,
                            amount=amount,
                        )
                    )
    interest_daily = config.margin_interest_bps_annual / 10_000.0 / config.annualization_days
    if interest_daily > 0 and snapshot.cash < 0:
        amount = -snapshot.cash * interest_daily
        if amount > 0:
            costs.append(
                CostAccrued(
                    ts=snapshot.ts, kind=CostKind.MARGIN_INTEREST, instrument=None, amount=amount
                )
            )
    return tuple(costs)
