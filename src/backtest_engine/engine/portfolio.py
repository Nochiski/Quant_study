"""포트폴리오 원장.

현금과 보유 수량은 FillEvent, 확인된 자본변동(CorporateActionApplied), 비용 발생
(CostAccrued) 적용 시점에만 변한다 — 주문 생성으로는 절대 변하지 않는다. Snapshot은
bars + fills + corporate actions applied + costs만으로 재계산 가능해야 한다.

명령(apply/mark)과 조회(snapshot/cash/held_qty)를 분리한다 (CQS).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, Decimal

from backtest_engine.errors import NegativeCashError, NegativePositionError
from backtest_engine.types.events import (
    CorporateActionApplied,
    CorporateActionEvent,
    CostAccrued,
    FillEvent,
)
from backtest_engine.types.instruments import InstrumentId
from backtest_engine.types.market import MarketSnapshot
from backtest_engine.types.orders import Side
from backtest_engine.types.portfolio import PortfolioSnapshot, Position


@dataclass
class _Ledger:
    quantity: Decimal
    average_price: float


# 원장 정수 비율(예: 1억/3억)은 Decimal로 순환소수가 되어 30 × 0.333…3 = 9.999…7이 된다.
# 주식 수 × 비율은 이론상 유리수이므로 소수 아래를 이 정밀도로 반올림한 뒤 floor한다.
_SCALE_QUANTUM = Decimal("1e-9")


def scale_quantity(quantity: Decimal, ratio: Decimal) -> Decimal:
    """자본변동 비율을 적용한 이론 수량 (floor 전). 순환소수 오차를 1e-9에서 흡수한다."""
    return (quantity * ratio).quantize(_SCALE_QUANTUM, rounding=ROUND_HALF_EVEN)


class Portfolio:
    """
    Args:
        initial_cash: 시작 현금.
        allow_short: True면 음수 수량(공매도)을 허용한다 (SHORT_SELLING 선언 전략).
        allow_margin: True면 음수 현금을 허용하되 equity가 0 미만이 되는 체결은 거절한다
            (MARGIN 선언 전략). 여력 상한은 브로커가 먼저 자른다.
    """

    def __init__(
        self, initial_cash: float, *, allow_short: bool = False, allow_margin: bool = False
    ) -> None:
        self._cash = initial_cash
        self._ledgers: dict[InstrumentId, _Ledger] = {}
        self._marks: dict[InstrumentId, float] = {}
        self._allow_short = allow_short
        self._allow_margin = allow_margin

    # --- 명령 ---------------------------------------------------------------

    def apply(self, fill: FillEvent) -> None:
        """체결 결과만 상태에 반영한다. 수수료는 평균단가에 섞지 않고 cash에서 별도 차감한다.

        평균단가 규칙: 같은 방향으로 늘리면 가중평균, 줄이면 유지, 0을 지나 방향이 바뀌면
        체결가로 재설정 (롱 5주에서 8주 매도 → 숏 3주 @ 매도가).
        """
        quantity = float(fill.quantity)
        notional = quantity * fill.price
        ledger = self._ledgers.get(fill.instrument)
        old_quantity = ledger.quantity if ledger is not None else Decimal(0)
        signed = fill.quantity if fill.side is Side.BUY else -fill.quantity
        new_quantity = old_quantity + signed

        if new_quantity < 0 and not self._allow_short:
            raise NegativePositionError(
                f"sell fill exceeds held quantity — fill_id={fill.fill_id} "
                f"instrument={fill.instrument.symbol} sell={fill.quantity} held={old_quantity} "
                f"(SHORT_SELLING not declared)"
            )
        if fill.side is Side.BUY:
            new_cash = self._cash - notional - fill.fee
        else:
            new_cash = self._cash + notional - fill.fee
        if new_cash < 0 and not self._allow_margin:
            raise NegativeCashError(
                f"buy fill would make cash negative — fill_id={fill.fill_id} "
                f"instrument={fill.instrument.symbol} quantity={fill.quantity} "
                f"price={fill.price} fee={fill.fee} cash={self._cash} (MARGIN not declared)"
            )

        if new_quantity == 0:
            self._ledgers.pop(fill.instrument, None)
        else:
            crossed = old_quantity == 0 or (old_quantity > 0) != (new_quantity > 0)
            if ledger is None or crossed:
                self._ledgers[fill.instrument] = _Ledger(
                    quantity=new_quantity, average_price=fill.price
                )
            elif abs(new_quantity) > abs(old_quantity):
                old_abs = float(abs(old_quantity))
                ledger.average_price = (old_abs * ledger.average_price + notional) / float(
                    abs(new_quantity)
                )
                ledger.quantity = new_quantity
            else:
                ledger.quantity = new_quantity
        self._cash = new_cash
        self._marks.setdefault(fill.instrument, fill.price)

    def charge(self, cost: CostAccrued) -> None:
        """비용 발생(차입·이자)을 현금에서 차감한다. 기록은 호출 측(EventStore) 책임."""
        if cost.amount <= 0:
            raise ValueError(
                f"cost amount must be > 0 — kind={cost.kind.value} ts={cost.ts} "
                f"amount={cost.amount}"
            )
        self._cash -= cost.amount

    def apply_corporate_action(
        self,
        action: CorporateActionEvent,
        settlement_price: float,
        settled_at: datetime | None = None,
    ) -> CorporateActionApplied | None:
        """확인된 분할·병합을 보유 포지션에 적용한다. 보유가 없으면 None.

        수량은 floor(qty × ratio), 평균단가는 avg / ratio. 단주(소수 부분)는
        settlement_price(정산 세션 시가)로 현금 지급한다. settled_at은 실제 적용 세션
        (사건 세션이 거래정지면 그 뒤 첫 거래 세션); None이면 action.ts.
        """
        ledger = self._ledgers.get(action.instrument)
        if ledger is None:
            return None
        if settlement_price <= 0:
            raise ValueError(
                f"corporate action settlement price must be > 0 — "
                f"instrument={action.instrument.symbol} ts={action.ts} price={settlement_price}"
            )
        old_quantity = ledger.quantity
        scaled = scale_quantity(old_quantity, action.ratio)
        new_quantity = scaled.quantize(Decimal(1), rounding=ROUND_FLOOR)
        cash_paid = float(scaled - new_quantity) * settlement_price
        old_average = ledger.average_price
        new_average = old_average / float(action.ratio)

        self._cash += cash_paid
        if new_quantity == 0:
            del self._ledgers[action.instrument]
        else:
            ledger.quantity = new_quantity
            ledger.average_price = new_average
        # 이전 세션 마크(분할 전 가격)로 평가하면 equity가 왜곡되므로 정산가로 교체한다.
        self._marks[action.instrument] = settlement_price
        return CorporateActionApplied(
            ts=settled_at if settled_at is not None else action.ts,
            instrument=action.instrument,
            action=action,
            old_quantity=old_quantity,
            new_quantity=new_quantity,
            old_average_price=old_average,
            new_average_price=new_average,
            cash_paid=cash_paid,
        )

    def mark(self, snapshot: MarketSnapshot) -> None:
        """현재 세션 종가로 평가 가격을 갱신한다."""
        for bar in snapshot.bars:
            self._marks[bar.instrument] = bar.close

    # --- 조회 ---------------------------------------------------------------

    @property
    def cash(self) -> float:
        return self._cash

    def held_qty(self, instrument: InstrumentId) -> Decimal:
        ledger = self._ledgers.get(instrument)
        return ledger.quantity if ledger is not None else Decimal(0)

    def snapshot(self, ts: datetime) -> PortfolioSnapshot:
        positions: list[Position] = []
        for instrument, ledger in self._ledgers.items():
            mark_price = self._marks[instrument]
            market_value = float(ledger.quantity) * mark_price
            positions.append(
                Position(
                    instrument=instrument,
                    quantity=ledger.quantity,
                    average_price=ledger.average_price,
                    market_price=mark_price,
                    market_value=market_value,
                    unrealized_pnl=(mark_price - ledger.average_price) * float(ledger.quantity),
                )
            )
        equity = self._cash + sum(position.market_value for position in positions)
        gross = sum(abs(position.market_value) for position in positions)
        return PortfolioSnapshot(
            ts=ts,
            cash=self._cash,
            positions=tuple(positions),
            equity=equity,
            gross_exposure=gross / equity if equity != 0 else 0.0,
        )
