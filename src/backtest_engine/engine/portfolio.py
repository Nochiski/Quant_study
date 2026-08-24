"""포트폴리오 원장.

현금과 보유 수량은 FillEvent 적용 시점에만 변한다 — 주문 생성으로는
절대 변하지 않는다. Snapshot은 fills와 bars만으로 재계산 가능해야 한다.

명령(apply/mark)과 조회(snapshot/cash/held_qty)를 분리한다 (CQS).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from backtest_engine.errors import NegativeCashError, NegativePositionError
from backtest_engine.types.events import FillEvent
from backtest_engine.types.instruments import InstrumentId
from backtest_engine.types.market import MarketSnapshot
from backtest_engine.types.orders import Side
from backtest_engine.types.portfolio import PortfolioSnapshot, Position


@dataclass
class _Ledger:
    quantity: Decimal
    average_price: float


class Portfolio:
    def __init__(self, initial_cash: float) -> None:
        self._cash = initial_cash
        self._ledgers: dict[InstrumentId, _Ledger] = {}
        self._marks: dict[InstrumentId, float] = {}

    # --- 명령 ---------------------------------------------------------------

    def apply(self, fill: FillEvent) -> None:
        """체결 결과만 상태에 반영한다. v1에서 수수료는 평균단가에 섞지 않고
        cash에서 별도로 차감한다."""
        quantity = float(fill.quantity)
        notional = quantity * fill.price
        ledger = self._ledgers.get(fill.instrument)

        if fill.side is Side.BUY:
            new_cash = self._cash - notional - fill.fee
            if new_cash < 0:
                raise NegativeCashError(
                    f"buy fill would make cash negative — fill_id={fill.fill_id} "
                    f"instrument={fill.instrument.symbol} quantity={fill.quantity} "
                    f"price={fill.price} fee={fill.fee} cash={self._cash}"
                )
            if ledger is None:
                self._ledgers[fill.instrument] = _Ledger(
                    quantity=fill.quantity, average_price=fill.price
                )
            else:
                old_quantity = float(ledger.quantity)
                new_quantity = old_quantity + quantity
                ledger.average_price = (
                    old_quantity * ledger.average_price + notional
                ) / new_quantity
                ledger.quantity += fill.quantity
            self._cash = new_cash
        else:
            held = ledger.quantity if ledger is not None else Decimal(0)
            if fill.quantity > held:
                raise NegativePositionError(
                    f"sell fill exceeds held quantity — fill_id={fill.fill_id} "
                    f"instrument={fill.instrument.symbol} sell={fill.quantity} held={held}"
                )
            if ledger is None:  # held == 0인 위 분기에서 이미 걸러짐, 타입 좁히기용
                raise NegativePositionError(
                    f"sell fill for instrument with no position — fill_id={fill.fill_id} "
                    f"instrument={fill.instrument.symbol}"
                )
            ledger.quantity -= fill.quantity
            self._cash += notional - fill.fee
            if ledger.quantity == 0:
                del self._ledgers[fill.instrument]

        self._marks.setdefault(fill.instrument, fill.price)

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
