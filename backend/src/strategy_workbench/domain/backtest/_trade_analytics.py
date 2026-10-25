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

from ._models import (
    CARRY_COST_KINDS,
    RawArtifactBundle,
    RawCorporateAction,
    RawCost,
    RawFill,
    RawTrade,
    RunCostKind,
)


@dataclass
class _OpenTrade:
    quantity: float
    average_price: float
    opened_on: date
    opening_fees: float
    opening_slippage: float


def _quantity_text(quantity: float) -> str:
    # 지수 표기 금지(#135): `Decimal("700.0").normalize()`는 `7E+2`라 `str()`이 "7E+2"를 냈다.
    # `format(…, "f")`는 같은 값을 "700"으로 — fills·positions의 정수 문자열과 표현이 맞는다.
    return format(Decimal(str(quantity)).normalize(), "f")


def _closing_trade(
    security_id: str,
    state: _OpenTrade,
    closed_quantity: float,
    exit_price: float,
    closed_on: date,
    *,
    exit_fees: float,
    exit_slippage: float,
) -> RawTrade:
    """열린 거래의 `closed_quantity` 만큼을 닫는다. 여는 비용은 닫는 몫만큼 나눠 싣는다."""
    open_fraction = closed_quantity / abs(state.quantity)
    fees = state.opening_fees * open_fraction + exit_fees
    gross_pnl = (
        (exit_price - state.average_price) * closed_quantity * (1 if state.quantity > 0 else -1)
    )
    return RawTrade(
        security_id=security_id,
        opened_on=state.opened_on,
        closed_on=closed_on,
        side="long" if state.quantity > 0 else "short",
        quantity=_quantity_text(closed_quantity),
        entry_price=state.average_price,
        exit_price=exit_price,
        pnl=gross_pnl - fees,
        fees=fees,
        slippage_cost=state.opening_slippage * open_fraction + exit_slippage,
    )


def _apply_corporate_action(
    states: dict[str, _OpenTrade], trades: list[RawTrade], action: RawCorporateAction
) -> None:
    """엔진이 적용한 분할·병합을 열린 거래에 똑같이 적용한다(수량 × ratio, 평균단가 ÷ ratio).

    분할로 수량이 바뀐 열린 거래를 옛 수량으로 들고 있으면, 분할 뒤 매도가 거짓 손실과 반대 방향
    잔량을 만든다. 엔진이 버린 단주(`cash_paid`)는 정산가로 닫힌 거래 한 건이다.
    """
    state = states.get(action.security_id)
    old_quantity = float(Decimal(action.old_quantity))
    if state is None or state.quantity != old_quantity:
        raise ValueError(
            "corporate action does not match the reconstructed open trade — "
            f"security_id={action.security_id} session={action.session} "
            f"old_quantity={action.old_quantity} "
            f"reconstructed={None if state is None else state.quantity}"
        )
    new_quantity = float(Decimal(action.new_quantity))
    # 단주는 엔진이 정한 값이다. 비율을 다시 곱하면 1e-9 반올림이 빠져 가짜 단주 거래가 생긴다.
    settled = float(Decimal(action.settled_quantity))
    scaled = new_quantity + settled
    adjusted = _OpenTrade(
        quantity=scaled,
        average_price=state.average_price / float(Decimal(action.ratio)),
        opened_on=state.opened_on,
        opening_fees=state.opening_fees,
        opening_slippage=state.opening_slippage,
    )
    fees, slippage = adjusted.opening_fees, adjusted.opening_slippage
    if settled != 0:
        # 정산가 = 정산 현금 ÷ 버린 단주(숏이면 둘 다 음수라 양수다).
        trades.append(
            _closing_trade(
                action.security_id,
                adjusted,
                abs(settled),
                action.cash_paid / settled,
                action.session,
                exit_fees=0.0,
                exit_slippage=0.0,
            )
        )
        kept = new_quantity / scaled
        fees, slippage = fees * kept, slippage * kept
    if new_quantity == 0:
        states.pop(action.security_id)
        return
    states[action.security_id] = _OpenTrade(
        quantity=new_quantity,
        average_price=adjusted.average_price,
        opened_on=adjusted.opened_on,
        opening_fees=fees,
        opening_slippage=slippage,
    )


def closed_trades(
    fills: Sequence[RawFill],
    costs: Sequence[RawCost],
    *,
    actions: Sequence[RawCorporateAction],
) -> tuple[RawTrade, ...]:
    """체결의 입력 순서가 원장 순서다. fill_id 문자열로 다시 정렬하지 않는다.

    `actions` 는 엔진이 보유에 적용한 분할·병합이다. 엔진은 같은 세션의 어떤 체결보다 먼저
    적용하므로 그 세션의 첫 체결 앞에서 열린 거래에 적용한다.
    """
    states: dict[str, _OpenTrade] = {}
    trades: list[RawTrade] = []
    pending = deque(sorted(actions, key=lambda item: item.session))
    # 새 체결의 확정 거래세를 소비한다. 세금 칸이 없던 역사 입력만 같은 세션·종목의
    # 비용 기록 순서로 복원한다. 명시적 0과 구판의 None을 구분한다.
    taxes: dict[tuple[date, str], deque[float]] = {}
    for cost in costs:
        if cost.kind == RunCostKind.SELL_TAX and cost.security_id is not None:
            taxes.setdefault((cost.session, cost.security_id), deque()).append(cost.amount)
    for fill in fills:
        security_id, session = fill.security_id, fill.session
        while pending and pending[0].session <= session:
            _apply_corporate_action(states, trades, pending.popleft())
        side, quantity, price = fill.side, float(fill.quantity), fill.price
        fee, slippage_per_share = fill.fee, fill.slippage_per_share
        pending_taxes = taxes.get((session, security_id))
        if fill.sell_tax is not None:
            fee += fill.sell_tax
            # 혼합 구판 입력에서도 뒤의 legacy 체결이 앞선 세금을 다시 가져오지 않는다.
            if side == "sell" and fill.sell_tax > 0 and pending_taxes:
                pending_taxes.popleft()
        elif side == "sell" and pending_taxes:
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
        trades.append(
            _closing_trade(
                security_id,
                state,
                closed_quantity,
                price,
                session,
                exit_fees=fee * exit_fraction,
                exit_slippage=slip * exit_fraction,
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
    # 마지막 체결 뒤의 분할·병합도 단주 정산이 있으면 닫힌 거래다.
    while pending:
        _apply_corporate_action(states, trades, pending.popleft())
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
        total_taxes=sum(item.amount for item in costs if item.kind == RunCostKind.SELL_TAX),
        total_slippage_cost=sum(
            float(item.quantity) * abs(item.slippage_per_share) for item in fills
        ),
        total_carry_cost=sum(item.amount for item in costs if item.kind in CARRY_COST_KINDS),
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
