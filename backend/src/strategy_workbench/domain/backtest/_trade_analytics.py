"""체결 원장의 거래 재구성과 전체·창 지표 입력 정책."""

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from strategy_workbench.domain.analytics.facade.metrics import (
    AnalysisPoint,
    AnalyticsInput,
    TradeOutcome,
)

from ._models import RawArtifactBundle, RawCost, RawFill, RawTrade

SELL_TAX = "sell_tax"
CARRY_COSTS = frozenset({"short_borrow", "margin_interest"})


@dataclass
class _OpenTrade:
    quantity: float
    average_price: float
    opened_on: date
    opening_fees: float
    opening_slippage: float


def closed_trades(fills: Sequence[RawFill], costs: Sequence[RawCost]) -> tuple[RawTrade, ...]:
    """체결의 입력 순서가 원장 순서다. fill_id 문자열로 다시 정렬하지 않는다."""
    states: dict[str, _OpenTrade] = {}
    trades: list[RawTrade] = []
    # 거래 단위 비용(`fees`)은 매도 거래세를 포함한다. 엔진은 매도 체결마다 거래세 기록을 그 체결
    # 바로 뒤에 하나씩 남기므로, 같은 세션·종목의 기록을 체결 순서대로 짝짓는다.
    taxes: dict[tuple[date, str], deque[float]] = {}
    for cost in costs:
        if cost.kind == SELL_TAX and cost.security_id is not None:
            taxes.setdefault((cost.session, cost.security_id), deque()).append(cost.amount)
    for fill in fills:
        security_id, session = fill.security_id, fill.session
        side, quantity, price = fill.side, float(fill.quantity), fill.price
        fee, slippage_per_share = fill.fee, fill.slippage_per_share
        pending_taxes = taxes.get((session, security_id))
        if side == "sell" and pending_taxes:
            fee += pending_taxes.popleft()
        delta = float(quantity) * (1 if side == "buy" else -1)
        slip = float(quantity) * abs(slippage_per_share)
        state = states.get(security_id)
        if state is None or state.quantity == 0 or state.quantity * delta > 0:
            current_quantity = abs(state.quantity) if state is not None else 0.0
            added = abs(delta)
            total = current_quantity + added
            states[security_id] = _OpenTrade(
                quantity=(1 if delta > 0 else -1) * total,
                average_price=(
                    ((state.average_price * current_quantity) if state is not None else 0.0)
                    + price * added
                )
                / total,
                opened_on=state.opened_on if state is not None else session,
                opening_fees=(state.opening_fees if state is not None else 0.0) + fee,
                opening_slippage=(state.opening_slippage if state is not None else 0.0) + slip,
            )
            continue
        open_quantity = abs(state.quantity)
        fill_quantity = abs(delta)
        closed_quantity = min(open_quantity, fill_quantity)
        open_fraction = closed_quantity / open_quantity
        exit_fraction = closed_quantity / fill_quantity
        fees = state.opening_fees * open_fraction + fee * exit_fraction
        slippage_cost = state.opening_slippage * open_fraction + slip * exit_fraction
        gross_pnl = (
            (price - state.average_price) * closed_quantity * (1 if state.quantity > 0 else -1)
        )
        trades.append(
            RawTrade(
                security_id=security_id,
                opened_on=state.opened_on,
                closed_on=session,
                side="long" if state.quantity > 0 else "short",
                # 지수 표기 금지(#135): `Decimal("700.0").normalize()`는 `7E+2`라 `str()`이
                # "7E+2"를 냈다. `format(…, "f")`는 같은 값을 "700"으로 — fills·positions의
                # 정수 문자열과 표현이 맞는다.
                quantity=format(Decimal(str(closed_quantity)).normalize(), "f"),
                entry_price=state.average_price,
                exit_price=price,
                pnl=gross_pnl - fees,
                fees=fees,
                slippage_cost=slippage_cost,
            )
        )
        remaining_open = open_quantity - closed_quantity
        remaining_fill = fill_quantity - closed_quantity
        if remaining_open > 0:
            states[security_id] = _OpenTrade(
                quantity=(1 if state.quantity > 0 else -1) * remaining_open,
                average_price=state.average_price,
                opened_on=state.opened_on,
                opening_fees=state.opening_fees * (1 - open_fraction),
                opening_slippage=state.opening_slippage * (1 - open_fraction),
            )
        elif remaining_fill > 0:
            states[security_id] = _OpenTrade(
                quantity=(1 if delta > 0 else -1) * remaining_fill,
                average_price=price,
                opened_on=session,
                opening_fees=fee * (1 - exit_fraction),
                opening_slippage=slip * (1 - exit_fraction),
            )
        else:
            states.pop(security_id, None)
    return tuple(trades)


def analytics_input(
    points: Sequence[AnalysisPoint],
    fills: Sequence[RawFill],
    costs: Sequence[RawCost],
    trades: Sequence[TradeOutcome],
    *,
    base: AnalysisPoint | None = None,
) -> AnalyticsInput:
    """전체·창 지표가 같은 순서와 공식으로 체결 및 비용 합계를 읽는다."""
    return AnalyticsInput(
        points=tuple(points),
        traded_notional=sum(float(item.quantity) * item.price for item in fills),
        trades=tuple(trades),
        total_fees=sum(item.fee for item in fills),
        total_taxes=sum(item.amount for item in costs if item.kind == SELL_TAX),
        total_slippage_cost=sum(
            float(item.quantity) * abs(item.slippage_per_share) for item in fills
        ),
        total_carry_cost=sum(item.amount for item in costs if item.kind in CARRY_COSTS),
        base=base,
    )


def slice_analytics(
    data: AnalyticsInput,
    artifacts: RawArtifactBundle,
    start: date,
    end: date,
) -> AnalyticsInput:
    """거래는 청산일, 비용은 체결일, 기준점은 창 직전 세션으로 귀속한다."""
    return analytics_input(
        tuple(item for item in data.points if start <= item.session <= end),
        tuple(item for item in artifacts.fills if start <= item.session <= end),
        tuple(item for item in artifacts.costs if start <= item.session <= end),
        tuple(item for item in data.trades if start <= item.closed_on <= end),
        base=next((item for item in reversed(data.points) if item.session < start), None),
    )
