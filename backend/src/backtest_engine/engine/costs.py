"""세션 종료 비용 계산 (5단계): 숏 차입 비용, 신용 이자. 순수 함수."""

from __future__ import annotations

from backtest_engine.types.events import CostAccrued, CostKind
from backtest_engine.types.portfolio import PortfolioSnapshot
from backtest_engine.types.results import RunConfig


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
